"""
alerts.py — leitura e ciclo de vida da tabela public.alerts (M6 / Fase 3).

Funcoes puras. RLS isola por user_id no banco; os helpers aqui sao read/write
do usuario autenticado.

Ciclo de vida:
- created (created_at)
- read    (read_at)
- dismissed (dismissed_at)

UI da aba Live (src/live.py) consome list_recent e usa mark_read/mark_dismissed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

import auth

TABLE = "alerts"


def list_recent(limit: int = 50) -> pd.DataFrame:
    """Ultimos N alertas do usuario, do mais novo para o mais antigo."""
    try:
        r = (
            auth.get_client().table(TABLE)
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = r.data or []
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
        for c in ("read_at", "dismissed_at"):
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


def mark_read(alert_id: int) -> dict:
    return _patch(alert_id, {"read_at": _now_iso()})


def mark_dismissed(alert_id: int) -> dict:
    return _patch(alert_id, {"dismissed_at": _now_iso()})


def mark_all_read() -> dict:
    """Marca todos os nao-lidos do usuario como lidos. RLS limita ao proprio."""
    try:
        (
            auth.get_client().table(TABLE)
            .update({"read_at": _now_iso()})
            .is_("read_at", "null")
            .execute()
        )
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _patch(alert_id: int, payload: dict) -> dict:
    try:
        (
            auth.get_client().table(TABLE)
            .update(payload)
            .eq("id", int(alert_id))
            .execute()
        )
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
