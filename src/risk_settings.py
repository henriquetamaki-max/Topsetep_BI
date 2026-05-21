"""
risk_settings.py — CRUD da tabela public.risk_settings (M6 / Fase 3).

1 linha por usuário (PK = user_id). Sem linha = Risk Guard inativo. O trigger
Postgres `risk_guard_eval` (M10) lê esta tabela quando uma linha é inserida em
live_snapshots e decide se cria um alert.

Defaults baseados nos planos TopStep mais comuns:
- Express 50K  → DLL $1000, Trailing $2000
- Express 100K → DLL $2000, Trailing $3000
- Express 150K → DLL $3000, Trailing $4500
- Custom       → tudo livre (trader edita)

Funções puras (sem Streamlit). Reusa auth.get_client.
"""

from __future__ import annotations

from typing import Any

import auth

TABLE = "risk_settings"

ACCOUNT_DEFAULTS: dict[str, dict[str, float]] = {
    "Express 50K":  {"daily_loss_limit_usd": 1000.0, "trailing_drawdown_usd": 2000.0},
    "Express 100K": {"daily_loss_limit_usd": 2000.0, "trailing_drawdown_usd": 3000.0},
    "Express 150K": {"daily_loss_limit_usd": 3000.0, "trailing_drawdown_usd": 4500.0},
    "Custom":       {"daily_loss_limit_usd": 0.0,     "trailing_drawdown_usd": 0.0},
}

ACCOUNT_TYPES = list(ACCOUNT_DEFAULTS.keys())


def get_settings(user_id: str | None = None) -> dict[str, Any] | None:
    """Lê a linha de risk_settings do usuário corrente. Retorna None se não
    existir (Risk Guard inativo). RLS filtra naturalmente; `user_id` é só
    documentação — não usado na query.
    """
    del user_id
    try:
        r = (
            auth.get_client().table(TABLE)
            .select("*")
            .maybeSingle()
            .execute()
        )
        return r.data
    except Exception:
        return None


def upsert_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Upsert na linha do usuário corrente. `user_id` é injetado pelo cliente
    autenticado. Devolve `{ok, error}`.
    """
    try:
        client = auth.get_client()
        # `auth.uid()` no banco resolve o user_id; aqui precisamos passar
        # explicitamente porque RLS exige `with check (auth.uid() = user_id)`.
        uid = auth.current_user_id()
        if not uid:
            return {"ok": False, "error": "no_user"}
        row = {"user_id": uid, **payload}
        client.table(TABLE).upsert(row, on_conflict="user_id").execute()
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def apply_account_defaults(account_type: str) -> dict[str, float]:
    """Devolve dict com daily_loss_limit_usd + trailing_drawdown_usd
    sugeridos para o tipo de conta (UI usa para pré-preencher campos).
    """
    return dict(ACCOUNT_DEFAULTS.get(account_type, ACCOUNT_DEFAULTS["Custom"]))
