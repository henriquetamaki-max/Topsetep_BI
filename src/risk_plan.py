"""
risk_plan.py — CRUD do Risk Planner (release 3.0) + ponte para daily_plans.

Tabelas:
- public.risk_plans       — header 1 linha por (user, plan_date): inputs do dia
                            (saldo, MLL, política de risco, params Monte Carlo) +
                            result_snapshot jsonb.
- public.risk_plan_assets — resultado por ativo (comparativo + seleção).

Funções puras (sem Streamlit). Reusa auth.get_client (RLS por user_id) e
daily_plan.upsert_plans para gravar as sugestões no plano matinal. A matemática
vive em risk_engine.py; aqui só persiste e orquestra.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

import auth
import daily_plan

TABLE = "risk_plans"
ASSETS_TABLE = "risk_plan_assets"


# --- catálogo de contratos --------------------------------------------------


def list_contracts() -> list[dict]:
    """Lê public.contracts (symbol, point_value_usd, is_micro). RLS aberta para
    leitura. Devolve [] em falha (UI cai no fallback)."""
    try:
        r = (
            auth.get_client().table("contracts")
            .select("symbol, point_value_usd, is_micro")
            .order("symbol")
            .execute()
        )
        return r.data or []
    except Exception:
        return []


# --- header (risk_plans) ----------------------------------------------------


def get_plan(plan_date: date) -> dict | None:
    """Header do dia para o usuário corrente (RLS filtra). None se não existe."""
    try:
        r = (
            auth.get_client().table(TABLE)
            .select("*")
            .eq("plan_date", plan_date.isoformat())
            .maybeSingle()
            .execute()
        )
        return r.data
    except Exception:
        return None


def list_plans_range(start: date, end: date) -> pd.DataFrame:
    """Headers risk_plans no intervalo [start, end] (inclusivo) para o usuário
    corrente — RLS filtra. Usado pela Avaliação de Risco (M15) para confrontar
    trades importados contra o plano de cada dia. DataFrame vazio em falha."""
    try:
        r = (
            auth.get_client().table(TABLE)
            .select("*")
            .gte("plan_date", start.isoformat())
            .lte("plan_date", end.isoformat())
            .order("plan_date")
            .execute()
        )
        return pd.DataFrame(r.data or [])
    except Exception:
        return pd.DataFrame()


def upsert_plan(payload: dict) -> dict:
    """Upsert do header na linha (user, plan_date). Injeta user_id (RLS exige).
    Devolve `{ok, id, error}` — `id` é necessário para gravar os assets."""
    try:
        client = auth.get_client()
        uid = auth.current_user_id()
        if not uid:
            return {"ok": False, "id": None, "error": "no_user"}
        row = {"user_id": uid, **payload}
        r = client.table(TABLE).upsert(row, on_conflict="user_id,plan_date").execute()
        rid = r.data[0]["id"] if r.data else None
        return {"ok": True, "id": rid, "error": None}
    except Exception as e:
        return {"ok": False, "id": None, "error": str(e)}


# --- assets (risk_plan_assets) ----------------------------------------------


def list_assets(risk_plan_id: int) -> pd.DataFrame:
    """Linhas do comparativo persistido para um header."""
    try:
        r = (
            auth.get_client().table(ASSETS_TABLE)
            .select("*")
            .eq("risk_plan_id", risk_plan_id)
            .execute()
        )
        return pd.DataFrame(r.data or [])
    except Exception:
        return pd.DataFrame()


def save_assets(risk_plan_id: int, rows: list[dict]) -> dict:
    """Substitui (delete + insert) os assets de um header. Injeta user_id e
    risk_plan_id em todo insert (RLS exige). Devolve `{ok, error}`."""
    try:
        client = auth.get_client()
        uid = auth.current_user_id()
        if not uid:
            return {"ok": False, "error": "no_user"}
        client.table(ASSETS_TABLE).delete().eq("risk_plan_id", risk_plan_id).execute()
        if rows:
            payload = [
                {"risk_plan_id": risk_plan_id, "user_id": uid, **row} for row in rows
            ]
            client.table(ASSETS_TABLE).insert(payload).execute()
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- ponte para daily_plans -------------------------------------------------


def push_to_daily_plans(
    plan_date: date, selected_assets: pd.DataFrame, *, overwrite: bool = False
) -> dict:
    """Converte os assets selecionados em linhas de daily_plans para `plan_date`.

    Mapeia por ativo: max_size=max_contracts (pula <=0), stop_points=max_stop_points,
    direction (default Long), notes='Risk Planner'. Reusa daily_plan.upsert_plans
    (diff + injeção de user_id já testados). Respeita a UNIQUE
    (user_id, plan_date, contract_name, direction): se já existe a mesma
    (contrato, direção) em daily_plans, atualiza quando `overwrite`, senão pula
    (conta em `skipped`). Devolve `{ok, inserted, updated, deleted, skipped, error}`.
    """
    empty = {"ok": True, "inserted": 0, "updated": 0, "deleted": 0, "skipped": 0, "error": None}
    if selected_assets is None or selected_assets.empty:
        return empty

    try:
        original = daily_plan.list_plans(plan_date)
    except Exception as e:
        return {**empty, "ok": False, "error": str(e)}

    edited = original.copy()
    existing: dict[tuple[str, str], object] = {}
    if not edited.empty:
        for idx, r in edited.iterrows():
            key = (str(r["contract_name"]).strip().upper(), str(r["direction"]))
            existing[key] = idx

    new_rows: list[dict] = []
    skipped = 0
    for _, a in selected_assets.iterrows():
        contract = str(a.get("contract_name") or "").strip().upper()
        if not contract:
            continue
        direction = str(a.get("direction") or "Long")
        try:
            max_size = int(a.get("max_contracts") or 0)
        except (TypeError, ValueError):
            max_size = 0
        if max_size <= 0:
            continue  # nada a planejar nesse ativo
        stop = a.get("max_stop_points")
        stop_val = float(stop) if stop is not None and pd.notna(stop) else None
        key = (contract, direction)
        if key in existing:
            if overwrite:
                idx = existing[key]
                edited.at[idx, "max_size"] = max_size
                edited.at[idx, "stop_points"] = stop_val
                edited.at[idx, "notes"] = "Risk Planner"
            else:
                skipped += 1
            continue
        new_rows.append({
            "id": pd.NA,
            "plan_date": plan_date,
            "contract_name": contract,
            "direction": direction,
            "max_size": max_size,
            "entry_trigger": None,
            "stop_points": stop_val,
            "target_points": None,
            "notes": "Risk Planner",
        })

    if new_rows:
        edited = pd.concat([edited, pd.DataFrame(new_rows)], ignore_index=True)

    result = daily_plan.upsert_plans(original, edited, default_date=plan_date)
    result["skipped"] = skipped
    return result
