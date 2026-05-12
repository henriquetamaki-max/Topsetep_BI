"""
Núcleo de ingestão — funções puras de parsing/upsert reutilizadas pelo
upload via UI (Streamlit) e pelo CLI legado (ingest.py).

Suporta dois formatos de CSV (detecção automática):
- TopStepX nativo: Id, ContractName, EnteredAt, ExitedAt, ...
- TopStep Dashboard legacy: Time, Trade Day, ID, Side, ... (reconstruído FIFO).

Multi-tenant: todas as rows recebem `user_id` antes do upsert; a PK composta
`(user_id, id)` no banco torna a idempotência por-usuário.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import IO

import pandas as pd
from supabase import Client

BATCH_SIZE = 1000
TABLE = "trades"

TOPSTEPX_COLS = {
    "Id", "ContractName", "EnteredAt", "ExitedAt", "EntryPrice",
    "ExitPrice", "PnL", "Size", "Type",
}
DASHBOARD_COLS = {
    "Time", "Trade Day", "ID", "Side", "Size", "Product",
    "Entry Price", "Total Fees", "Profit",
}


def detect_format(df: pd.DataFrame) -> str:
    cols = set(df.columns.str.strip())
    if TOPSTEPX_COLS.issubset(cols):
        return "topstepx"
    if DASHBOARD_COLS.issubset(cols):
        return "dashboard"
    raise ValueError(f"Formato de CSV não reconhecido. Colunas: {sorted(cols)}")


def _clean_money(val) -> float:
    if pd.isna(val) or val == "":
        return 0.0
    return float(str(val).replace("$", "").replace(",", "").strip())


def _synthetic_id(open_id, close_id) -> int:
    raw = f"R{open_id}_{close_id}"
    h = abs(hash(raw)) % (10 ** 15)
    return int(f"9{h:015d}"[:16])


def _duration_between(t1_str: str, t2_str: str) -> str:
    t1 = pd.to_datetime(t1_str, format="mixed")
    t2 = pd.to_datetime(t2_str, format="mixed")
    delta: timedelta = t2 - t1
    total = int(delta.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    micro = delta.microseconds
    return f"{h:02d}:{m:02d}:{s:02d}.{micro:06d}"


def reconstruct_dashboard_trades(raw: pd.DataFrame) -> pd.DataFrame:
    """Pareia Buy/Sell do TopStep Dashboard em trades completos (FIFO por produto)."""
    raw = raw.copy()
    raw["Time"] = raw["Time"].astype(str).str.replace(" CT", "").str.replace(" ET", "")
    raw["TimeParsed"] = pd.to_datetime(raw["Time"], format="mixed")
    raw = raw.sort_values("TimeParsed").reset_index(drop=True)

    completed: list[dict] = []
    for product, sub in raw.groupby("Product"):
        open_execs: list[dict] = []
        for _, row in sub.iterrows():
            side = str(row["Side"])
            qty = float(row["Size"])
            price = _clean_money(row["Entry Price"])
            time = row["Time"]
            row_profit = _clean_money(row["Profit"])
            row_fee = abs(_clean_money(row["Total Fees"]))
            fee_per_unit = row_fee / qty if qty > 0 else 0.0

            if not open_execs or open_execs[0]["Side"] == side:
                open_execs.append({
                    "Side": side, "Size": qty, "EntryPrice": price,
                    "Time": time, "ID": row["ID"], "FeePerUnit": fee_per_unit,
                })
                continue

            closing_initial = qty
            while qty > 0 and open_execs:
                first = open_execs[0]
                matched = min(qty, first["Size"])
                matched_profit = (
                    (matched / closing_initial) * row_profit
                    if closing_initial > 0 else 0.0
                )
                entry_fee = matched * first["FeePerUnit"]
                exit_fee = matched * fee_per_unit
                total_fee = entry_fee + exit_fee

                completed.append({
                    "Id": _synthetic_id(first["ID"], row["ID"]),
                    "ContractName": product,
                    "EnteredAt": first["Time"],
                    "ExitedAt": time,
                    "EntryPrice": first["EntryPrice"],
                    "ExitPrice": price,
                    "PnL": matched_profit,
                    "Fees": total_fee,
                    "Commissions": 0.0,
                    "Size": matched,
                    "Type": "Long" if first["Side"].lower() == "buy" else "Short",
                    "TradeDay": str(row["Trade Day"]),
                    "TradeDuration": _duration_between(first["Time"], time),
                })
                qty -= matched
                first["Size"] -= matched
                if first["Size"] <= 0:
                    open_execs.pop(0)

            if qty > 0:
                open_execs.append({
                    "Side": side, "Size": qty, "EntryPrice": price,
                    "Time": time, "ID": row["ID"], "FeePerUnit": fee_per_unit,
                })

    if not completed:
        return pd.DataFrame()
    return pd.DataFrame(completed)


def _duration_to_pg_interval(s: str) -> str:
    m = re.match(r"^(\d+):(\d{2}):(\d{2})(?:\.(\d+))?$", str(s).strip())
    if not m:
        return s
    h, mm, ss, frac = m.groups()
    if frac:
        frac = frac[:6].ljust(6, "0")
        return f"{h}:{mm}:{ss}.{frac}"
    return f"{h}:{mm}:{ss}"


def normalize_topstepx(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(
        columns={
            "Id": "id", "ContractName": "contract_name",
            "EnteredAt": "entered_at", "ExitedAt": "exited_at",
            "EntryPrice": "entry_price", "ExitPrice": "exit_price",
            "Fees": "fees", "PnL": "pnl", "Size": "size", "Type": "type",
            "TradeDay": "trade_day", "TradeDuration": "trade_duration",
            "Commissions": "commissions",
        }
    )
    df["id"] = df["id"].astype("int64")
    df["size"] = df["size"].astype("int64")
    for col in ("fees", "commissions"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0
    for col in ("entry_price", "exit_price", "pnl"):
        df[col] = pd.to_numeric(df[col], errors="raise")
    ts_fmt = "%m/%d/%Y %H:%M:%S %z"
    df["entered_at"] = pd.to_datetime(df["entered_at"], format=ts_fmt, utc=True)
    df["exited_at"] = pd.to_datetime(df["exited_at"], format=ts_fmt, utc=True)
    df["trade_day"] = pd.to_datetime(df["trade_day"], format=ts_fmt, utc=True).dt.date
    df["trade_duration"] = df["trade_duration"].apply(_duration_to_pg_interval)
    df = df.drop_duplicates(subset="id", keep="last")
    return df


def normalize_dashboard(df: pd.DataFrame) -> pd.DataFrame:
    rebuilt = reconstruct_dashboard_trades(df)
    if rebuilt.empty:
        return rebuilt
    rebuilt = rebuilt.rename(
        columns={
            "Id": "id", "ContractName": "contract_name",
            "EnteredAt": "entered_at", "ExitedAt": "exited_at",
            "EntryPrice": "entry_price", "ExitPrice": "exit_price",
            "Fees": "fees", "Commissions": "commissions", "PnL": "pnl",
            "Size": "size", "Type": "type", "TradeDay": "trade_day",
            "TradeDuration": "trade_duration",
        }
    )
    rebuilt["id"] = rebuilt["id"].astype("int64")
    rebuilt["size"] = rebuilt["size"].astype("int64")
    for col in ("entry_price", "exit_price", "fees", "commissions", "pnl"):
        rebuilt[col] = pd.to_numeric(rebuilt[col], errors="raise")
    rebuilt["entered_at"] = pd.to_datetime(
        rebuilt["entered_at"], format="mixed"
    ).dt.tz_localize("America/Chicago", ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")
    rebuilt["exited_at"] = pd.to_datetime(
        rebuilt["exited_at"], format="mixed"
    ).dt.tz_localize("America/Chicago", ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")
    rebuilt["trade_day"] = pd.to_datetime(rebuilt["trade_day"]).dt.date
    rebuilt["trade_duration"] = rebuilt["trade_duration"].apply(_duration_to_pg_interval)
    rebuilt = rebuilt.drop_duplicates(subset="id", keep="last")
    return rebuilt


def records_for_supabase(df: pd.DataFrame, user_id: str) -> list[dict]:
    out = df.copy()
    out["entered_at"] = out["entered_at"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    out["exited_at"] = out["exited_at"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    out["trade_day"] = out["trade_day"].astype(str)
    out["user_id"] = user_id
    return out.to_dict(orient="records")


def upsert_batches(client: Client, records: list[dict]) -> int:
    """Upsert em lotes. PK composta `(user_id, id)` torna a operação idempotente
    por usuário — reprocessar o mesmo CSV para o mesmo dono não cria duplicatas."""
    total = 0
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        client.table(TABLE).upsert(batch, on_conflict="user_id,id").execute()
        total += len(batch)
    return total


def parse_csv_to_df(raw: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Detecta formato e devolve (df normalizado, formato). Pode retornar df vazio."""
    fmt = detect_format(raw)
    if fmt == "topstepx":
        return normalize_topstepx(raw), fmt
    return normalize_dashboard(raw), fmt


def ingest_uploaded_csv(
    file: IO | str, client: Client, user_id: str
) -> tuple[int, str, str | None]:
    """Ingere um CSV (file-like ou path) para o usuário dado.

    Retorna `(n_rows, formato, erro_ou_None)`. Erros viram string em vez de
    exceção pra a UI poder mostrar por arquivo sem parar a fila.
    """
    try:
        raw = pd.read_csv(file)
    except Exception as e:
        return 0, "?", f"falha ao ler CSV: {e}"
    if raw.empty:
        return 0, "?", "CSV vazio"
    try:
        df, fmt = parse_csv_to_df(raw)
    except Exception as e:
        return 0, "?", str(e)
    if df.empty:
        return 0, fmt, "sem trades após normalização"
    try:
        records = records_for_supabase(df, user_id)
        n = upsert_batches(client, records)
    except Exception as e:
        return 0, fmt, f"falha no upsert: {e}"
    return n, fmt, None
