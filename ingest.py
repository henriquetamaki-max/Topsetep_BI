"""
BI TopStep — ingestão de CSVs de trades para Supabase.

Lê todos os CSVs em "CSV input/", detecta automaticamente o formato
(TopStepX nativo ou TopStep Dashboard legacy), normaliza, faz upsert em
batch na tabela public.trades (PK: id), e move cada arquivo processado
com sucesso para "CSV output/" renomeado como YYYYMMDD_N.csv.

Formatos suportados:
- TopStepX (nativo): colunas Id, ContractName, EnteredAt, ExitedAt, ...
- TopStep Dashboard (legacy): Time, Trade Day, ID, Side, Size, Product,
  Entry Price, Total Fees, Profit — reconstrói trades pareando Buy/Sell.

A reconstrução do formato Dashboard é porta direta da função
`reconstruct_dashboard_trades` em
Templates/TradePontos/backend/core/processor.py:18-97.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "CSV input"
OUTPUT_DIR = ROOT / "CSV output"
ENV_FILE = ROOT / "Env" / "Topstep_bi.env"

BATCH_SIZE = 1000
TABLE = "trades"


def load_env() -> tuple[str, str]:
    if not ENV_FILE.exists():
        sys.exit(f"ERRO: arquivo de ambiente não encontrado: {ENV_FILE}")
    load_dotenv(ENV_FILE)
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url:
        sys.exit("ERRO: NEXT_PUBLIC_SUPABASE_URL não encontrada no .env")
    if not key:
        sys.exit(
            "ERRO: SUPABASE_SERVICE_ROLE_KEY não encontrada no .env.\n"
            "Pegue em Supabase -> Settings -> API -> service_role secret\n"
            f"e adicione a linha:  SUPABASE_SERVICE_ROLE_KEY=...  em {ENV_FILE}"
        )
    return url, key


# ---------------------------------------------------------------------------
# Detecção de formato e parsing TopStep Dashboard
# ---------------------------------------------------------------------------

TOPSTEPX_COLS = {"Id", "ContractName", "EnteredAt", "ExitedAt", "EntryPrice",
                 "ExitPrice", "PnL", "Size", "Type"}
DASHBOARD_COLS = {"Time", "Trade Day", "ID", "Side", "Size", "Product",
                  "Entry Price", "Total Fees", "Profit"}


def detect_format(df: pd.DataFrame) -> str:
    cols = set(df.columns.str.strip())
    if TOPSTEPX_COLS.issubset(cols):
        return "topstepx"
    if DASHBOARD_COLS.issubset(cols):
        return "dashboard"
    raise ValueError(
        f"Formato de CSV não reconhecido. Colunas: {sorted(cols)}"
    )


def _clean_money(val) -> float:
    if pd.isna(val) or val == "":
        return 0.0
    return float(str(val).replace("$", "").replace(",", "").strip())


def reconstruct_dashboard_trades(raw: pd.DataFrame) -> pd.DataFrame:
    """Pareia Buy/Sell do TopStep Dashboard em trades completos.

    Porta de Templates/TradePontos/backend/core/processor.py:18-97.
    Cada linha do CSV é UMA execução; precisamos parear na ordem cronológica
    (FIFO por produto), aportando PnL e fees por quantidade casada.
    """
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

            # mesmo lado da fila aberta -> empilha (aumentando posição)
            if not open_execs or open_execs[0]["Side"] == side:
                open_execs.append({
                    "Side": side, "Size": qty, "EntryPrice": price,
                    "Time": time, "ID": row["ID"], "FeePerUnit": fee_per_unit,
                })
                continue

            # lado oposto -> fecha contra a fila (FIFO)
            closing_initial = qty
            while qty > 0 and open_execs:
                first = open_execs[0]
                matched = min(qty, first["Size"])
                matched_profit = (
                    (matched / closing_initial) * row_profit
                    if closing_initial > 0
                    else 0.0
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
                # excesso vira nova abertura no lado oposto
                open_execs.append({
                    "Side": side, "Size": qty, "EntryPrice": price,
                    "Time": time, "ID": row["ID"], "FeePerUnit": fee_per_unit,
                })

    if not completed:
        return pd.DataFrame()
    return pd.DataFrame(completed)


def _synthetic_id(open_id, close_id) -> int:
    """IDs sintéticos para trades reconstruídos: hash determinístico dos dois IDs.

    A PK do banco é bigint; usamos um hash truncado para 15 dígitos
    (cabe em bigint signed e não colide com IDs reais TopStepX que são ~10 dígitos).
    """
    raw = f"R{open_id}_{close_id}"
    # hash determinístico → mesmos pares geram mesmo id (idempotente)
    h = abs(hash(raw)) % (10 ** 15)
    # prefixo "9" garante que não colide com IDs reais (~2.5e9 a ~2.6e9)
    return int(f"9{h:015d}"[:16])


def _duration_between(t1_str: str, t2_str: str) -> str:
    """Duração HH:MM:SS entre dois timestamps do Dashboard (sem TZ explícito)."""
    t1 = pd.to_datetime(t1_str, format="mixed")
    t2 = pd.to_datetime(t2_str, format="mixed")
    delta: timedelta = t2 - t1
    total = int(delta.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    micro = delta.microseconds
    return f"{h:02d}:{m:02d}:{s:02d}.{micro:06d}"


# ---------------------------------------------------------------------------
# Normalização (formato comum -> registros para o Supabase)
# ---------------------------------------------------------------------------


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

    # NaN em fees/commissions -> 0 (alguns exports não trazem essa coluna preenchida)
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
    """Reconstrói trades a partir das execuções e normaliza para o schema do banco."""
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

    # Timestamps do Dashboard: "YYYY-MM-DD HH:MM:SS" no fuso CT (America/Chicago).
    rebuilt["entered_at"] = pd.to_datetime(rebuilt["entered_at"], format="mixed").dt.tz_localize(
        "America/Chicago", ambiguous="NaT", nonexistent="shift_forward"
    ).dt.tz_convert("UTC")
    rebuilt["exited_at"] = pd.to_datetime(rebuilt["exited_at"], format="mixed").dt.tz_localize(
        "America/Chicago", ambiguous="NaT", nonexistent="shift_forward"
    ).dt.tz_convert("UTC")
    rebuilt["trade_day"] = pd.to_datetime(rebuilt["trade_day"]).dt.date

    rebuilt["trade_duration"] = rebuilt["trade_duration"].apply(_duration_to_pg_interval)
    rebuilt = rebuilt.drop_duplicates(subset="id", keep="last")
    return rebuilt


def _duration_to_pg_interval(s: str) -> str:
    """Converte 'HH:MM:SS.fffffff' do .NET para string aceita pelo Postgres interval."""
    m = re.match(r"^(\d+):(\d{2}):(\d{2})(?:\.(\d+))?$", str(s).strip())
    if not m:
        return s
    h, mm, ss, frac = m.groups()
    if frac:
        frac = frac[:6].ljust(6, "0")
        return f"{h}:{mm}:{ss}.{frac}"
    return f"{h}:{mm}:{ss}"


def records_for_supabase(df: pd.DataFrame) -> list[dict]:
    out = df.copy()
    out["entered_at"] = out["entered_at"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    out["exited_at"] = out["exited_at"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    out["trade_day"] = out["trade_day"].astype(str)
    return out.to_dict(orient="records")


def upsert_batches(client: Client, records: list[dict]) -> int:
    total = 0
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        client.table(TABLE).upsert(batch, on_conflict="id").execute()
        total += len(batch)
    return total


def next_output_name() -> str:
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y%m%d")
    existing = list(OUTPUT_DIR.glob(f"{today}_*.csv"))
    used = set()
    for f in existing:
        m = re.match(rf"^{today}_(\d+)\.csv$", f.name)
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"{today}_{n}.csv"


def main() -> int:
    url, key = load_env()
    client = create_client(url, key)

    OUTPUT_DIR.mkdir(exist_ok=True)
    csvs = sorted(INPUT_DIR.glob("*.csv"))
    if not csvs:
        print(f"Nenhum CSV em {INPUT_DIR}. Nada a fazer.")
        return 0

    files_ok = 0
    rows_total = 0
    errors: list[tuple[Path, str]] = []

    for csv_path in csvs:
        try:
            raw = pd.read_csv(csv_path)
            if raw.empty:
                print(f"  [skip] {csv_path.name}: vazio")
                shutil.move(csv_path, OUTPUT_DIR / next_output_name())
                continue

            fmt = detect_format(raw)
            if fmt == "topstepx":
                df = normalize_topstepx(raw)
            else:
                df = normalize_dashboard(raw)

            if df.empty:
                print(f"  [skip] {csv_path.name}: sem trades reconstruidos ({fmt})")
                shutil.move(csv_path, OUTPUT_DIR / next_output_name())
                continue

            records = records_for_supabase(df)
            n = upsert_batches(client, records)

            new_name = next_output_name()
            shutil.move(csv_path, OUTPUT_DIR / new_name)
            print(f"  [ok]  {csv_path.name} ({fmt}): {n} linhas upsertadas -> {new_name}")
            files_ok += 1
            rows_total += n
        except Exception as exc:  # noqa: BLE001
            errors.append((csv_path, str(exc)))
            print(f"  [ERR] {csv_path.name}: {exc}")

    print()
    print(f"Resumo: {files_ok}/{len(csvs)} arquivos processados, {rows_total} linhas upsertadas.")
    if errors:
        print(f"{len(errors)} arquivo(s) com erro (mantidos em CSV input/).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
