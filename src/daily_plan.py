"""
Plano matinal — CRUD da tabela public.daily_plans.

Cada linha representa o que o trader pretende fazer em um contrato/direção
específico em uma data específica. Operações reais (em public.trades) são
confrontadas contra estas linhas em metrics.compute_plan_adherence() para
gerar score de aderência ao plano (PR-C).

Funções puras (sem Streamlit). Reusa o cliente Supabase de coach_ai
(mesma instância autenticada com JWT do usuário, respeita RLS).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from coach_ai import _current_user_id, _supabase

TABLE = "daily_plans"

# Fallback estático para o caso de a tabela `public.contracts` ainda não ter
# sido aplicada (ambientes pré-M6 da fusão). Em produção, a fonte da verdade
# é a tabela `public.contracts` — ver _load_contract_values().
_POINT_VALUE_FALLBACK: dict[str, float] = {
    "MNQ": 2.0,
}

# Cache em memória, populado por _load_contract_values() (TTL 1h). Não usar
# diretamente — sempre acessar via point_value_usd().
_CONTRACTS_CACHE: dict[str, float] | None = None


def _load_contract_values() -> dict[str, float]:
    """Lê `public.contracts` e devolve dict {symbol: point_value_usd}.

    Falha silenciosa: se a tabela não existir (ambiente pré-M6) ou o cliente
    Supabase estiver indisponível, devolve o fallback estático para não
    quebrar a UI de Day Plan.
    """
    try:
        client = _supabase()
        r = client.table("contracts").select("symbol, point_value_usd").execute()
        rows = r.data or []
        if not rows:
            return dict(_POINT_VALUE_FALLBACK)
        return {
            str(row["symbol"]).strip().upper(): float(row["point_value_usd"])
            for row in rows
            if row.get("symbol") and row.get("point_value_usd") is not None
        }
    except Exception:
        return dict(_POINT_VALUE_FALLBACK)


def _contracts() -> dict[str, float]:
    global _CONTRACTS_CACHE
    if _CONTRACTS_CACHE is None:
        _CONTRACTS_CACHE = _load_contract_values()
    return _CONTRACTS_CACHE


def invalidate_contracts_cache() -> None:
    """Limpa o cache. Chamar após mudança no catálogo (raro)."""
    global _CONTRACTS_CACHE
    _CONTRACTS_CACHE = None


def point_value_usd(contract_name: str | None) -> float | None:
    """Valor do ponto em USD para um contrato. None se contrato desconhecido."""
    if not contract_name:
        return None
    return _contracts().get(str(contract_name).strip().upper())


def compute_usd(
    points: float | None, max_size: int | None, contract_name: str | None,
) -> float | None:
    """Converte pontos em USD: points × point_value × max_size. None se algum
    insumo estiver ausente ou o contrato não tiver mapeamento.
    """
    pv = point_value_usd(contract_name)
    if pv is None or points is None or max_size is None:
        return None
    try:
        return float(points) * pv * int(max_size)
    except (TypeError, ValueError):
        return None


EDITABLE_COLUMNS = [
    "contract_name",
    "direction",
    "max_size",
    "entry_trigger",
    "stop_points",
    "target_points",
    "notes",
]
ALL_COLUMNS = [
    "id",
    "plan_date",
    "created_at",
    "updated_at",
    *EDITABLE_COLUMNS,
]

VALID_DIRECTION = ("Long", "Short")


def list_plans(plan_date: date | None = None) -> pd.DataFrame:
    """Lê planos do trader autenticado (RLS filtra por user_id).

    Se `plan_date` for fornecido, devolve só os planos daquela data. Se None,
    devolve todos. Sempre devolve as colunas em `ALL_COLUMNS` (vazio com
    schema correto se não houver dados).
    """
    client = _supabase()
    query = client.table(TABLE).select("*")
    if plan_date is not None:
        query = query.eq("plan_date", plan_date.isoformat())
    r = query.execute()
    rows = r.data or []
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in ALL_COLUMNS})

    for c in ALL_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df["plan_date"] = pd.to_datetime(df["plan_date"]).dt.date
    df["max_size"] = pd.to_numeric(df["max_size"], errors="coerce").fillna(0).astype("Int64")
    for c in ("stop_points", "target_points"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values(
        by=["plan_date", "contract_name", "direction"],
        ascending=[False, True, True],
    ).reset_index(drop=True)
    return df[ALL_COLUMNS]


def _normalize_row(row: pd.Series, default_date: date | None = None) -> dict | None:
    """Converte linha do data_editor em payload Supabase.

    Devolve None se a linha não tem o mínimo (contract_name + direction válida
    + max_size > 0) — essas linhas são tratadas como vazias e ignoradas no
    upsert (evita inserir linha em branco quando o usuário clica no '+').
    """
    name = (str(row.get("contract_name") or "")).strip().upper()
    direction = row.get("direction")
    if isinstance(direction, str):
        direction = direction.strip()
    if direction not in VALID_DIRECTION:
        return None
    try:
        max_size = int(row.get("max_size") or 0)
    except (TypeError, ValueError):
        return None
    if not name or max_size <= 0:
        return None

    plan_date = row.get("plan_date") or default_date
    if isinstance(plan_date, pd.Timestamp):
        plan_date = plan_date.date()
    if plan_date is None:
        return None
    if not isinstance(plan_date, date):
        plan_date = pd.to_datetime(plan_date).date()

    trigger = row.get("entry_trigger")
    trigger = str(trigger).strip() if trigger and not pd.isna(trigger) else None
    notes = row.get("notes")
    notes = str(notes).strip() if notes and not pd.isna(notes) else None

    stop = row.get("stop_points")
    target = row.get("target_points")

    return {
        "plan_date": plan_date.isoformat(),
        "contract_name": name,
        "direction": direction,
        "max_size": max_size,
        "entry_trigger": trigger or None,
        "stop_points": float(stop) if stop is not None and not pd.isna(stop) else None,
        "target_points": float(target) if target is not None and not pd.isna(target) else None,
        "notes": notes or None,
    }


def upsert_plans(
    original: pd.DataFrame,
    edited: pd.DataFrame,
    default_date: date | None = None,
) -> dict:
    """Diff entre `original` (snapshot de list_plans) e `edited` (DataFrame
    após o usuário mexer no data_editor). Aplica insert/update/delete e
    devolve contadores. Mesmo padrão de action_plan.upsert_items.
    """
    try:
        client = _supabase()
    except Exception as e:
        return {"ok": False, "inserted": 0, "updated": 0, "deleted": 0, "error": str(e)}

    orig_ids = {int(i) for i in original["id"].dropna().tolist()} if not original.empty else set()
    edited_ids = {int(i) for i in edited["id"].dropna().tolist()} if not edited.empty else set()

    to_delete = orig_ids - edited_ids
    to_insert: list[dict] = []
    to_update: list[tuple[int, dict]] = []

    orig_by_id = (
        original.set_index("id").to_dict("index") if not original.empty else {}
    )

    diff_keys = (
        "plan_date", "contract_name", "direction", "max_size",
        "entry_trigger", "stop_points", "target_points", "notes",
    )

    for _, row in edited.iterrows():
        payload = _normalize_row(row, default_date=default_date)
        if payload is None:
            continue
        rid = row.get("id")
        if pd.isna(rid) or rid is None:
            to_insert.append(payload)
            continue
        rid_int = int(rid)
        prev = orig_by_id.get(rid_int) or orig_by_id.get(float(rid_int)) or {}
        prev_norm = _normalize_row(pd.Series(prev), default_date=default_date) or {}
        if any(payload.get(k) != prev_norm.get(k) for k in diff_keys):
            to_update.append((rid_int, payload))

    inserted = updated = deleted = 0
    try:
        if to_insert:
            user_id = _current_user_id()
            if not user_id:
                return {
                    "ok": False, "inserted": 0, "updated": 0, "deleted": 0,
                    "error": "Usuário não autenticado.",
                }
            for payload in to_insert:
                payload["user_id"] = user_id
            client.table(TABLE).insert(to_insert).execute()
            inserted = len(to_insert)
        for rid, payload in to_update:
            payload["updated_at"] = pd.Timestamp.utcnow().isoformat()
            client.table(TABLE).update(payload).eq("id", rid).execute()
            updated += 1
        if to_delete:
            client.table(TABLE).delete().in_("id", list(to_delete)).execute()
            deleted = len(to_delete)
    except Exception as e:
        return {
            "ok": False,
            "inserted": inserted, "updated": updated, "deleted": deleted,
            "error": str(e),
        }

    return {
        "ok": True,
        "inserted": inserted, "updated": updated, "deleted": deleted,
        "error": None,
    }
