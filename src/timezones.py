"""
Utilitário de fuso horário para o BI TopStep.

Modelo dual-tz (M7 da fusão):
- **Primário fixo:** America/New_York (sessão CME — open 18:00 ET).
- **Secundário:** fuso do usuário, lido de `st.session_state["user_tz"]`
  (populado por settings.py via auto-detect JS no primeiro login + persistido
  em `user_metadata.preferred_tz`). Default = America/Sao_Paulo se ainda não
  detectado.

Funções puras (sem Streamlit) sempre que possível — facilita teste e reuso.
`user_tz()` é a única que toca `st.session_state`.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

PRIMARY_TZ = "America/New_York"
FALLBACK_USER_TZ = "America/Sao_Paulo"


def user_tz() -> str:
    """Fuso secundário do usuário corrente.

    Ordem de resolução:
    1. `st.session_state["user_tz"]` (setado por settings.py após auto-detect).
    2. `user_metadata.preferred_tz` do usuário logado.
    3. FALLBACK_USER_TZ.
    """
    cached = st.session_state.get("user_tz")
    if cached:
        return cached
    try:
        import auth  # noqa: PLC0415

        user = auth.current_user()
        meta = (user or {}).get("user_metadata") or {}
        tz = meta.get("preferred_tz")
        if tz:
            st.session_state["user_tz"] = tz
            return tz
    except Exception:
        pass
    return FALLBACK_USER_TZ


def _ensure_tz(ts: pd.Timestamp | pd.Series) -> pd.Timestamp | pd.Series:
    """Garante tz-aware: localiza UTC se vier naive."""
    if isinstance(ts, pd.Series):
        if ts.dt.tz is None:
            return ts.dt.tz_localize("UTC")
        return ts
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts


def to_primary(ts: pd.Timestamp | pd.Series) -> pd.Timestamp | pd.Series:
    """Converte timestamp para o fuso primário (ET)."""
    ts = _ensure_tz(ts)
    if isinstance(ts, pd.Series):
        return ts.dt.tz_convert(PRIMARY_TZ)
    return ts.tz_convert(PRIMARY_TZ)


def to_user(ts: pd.Timestamp | pd.Series) -> pd.Timestamp | pd.Series:
    """Converte timestamp para o fuso do usuário."""
    tz = user_tz()
    ts = _ensure_tz(ts)
    if isinstance(ts, pd.Series):
        return ts.dt.tz_convert(tz)
    return ts.tz_convert(tz)


def _tz_short(tz: str) -> str:
    """Abreviação curta para exibição: 'America/New_York' → 'ET'."""
    short = {
        "America/New_York": "ET",
        "America/Chicago": "CT",
        "America/Los_Angeles": "PT",
        "America/Sao_Paulo": "BRT",
        "America/Argentina/Buenos_Aires": "ART",
        "Europe/London": "BST/GMT",
        "Europe/Madrid": "CET",
        "Europe/Lisbon": "WET",
        "UTC": "UTC",
    }
    if tz in short:
        return short[tz]
    # Fallback: usa a última parte do IANA (ex.: 'Asia/Tokyo' → 'Tokyo').
    return tz.rsplit("/", 1)[-1]


PRIMARY_TZ_SHORT = _tz_short(PRIMARY_TZ)


def user_tz_short() -> str:
    return _tz_short(user_tz())


def fmt_dual(ts: pd.Timestamp, fmt: str = "%d/%m %H:%M") -> str:
    """Formata 'HH:MM ET / HH:MM <user_tz_short>'.

    Se o fuso do usuário for igual ao primário, exibe apenas uma cópia.
    """
    p = to_primary(ts).strftime(fmt)
    u_tz = user_tz()
    if u_tz == PRIMARY_TZ:
        return f"{p} {PRIMARY_TZ_SHORT}"
    u = to_user(ts).strftime(fmt)
    return f"{p} {PRIMARY_TZ_SHORT} / {u} {user_tz_short()}"


def fmt_dual_series(ts: pd.Series, fmt: str = "%d/%m %H:%M") -> pd.Series:
    """Versão vetorizada de `fmt_dual` para uma coluna inteira.

    Resolve `user_tz()` e as conversões de fuso **uma única vez** (em vez de
    por linha via `.apply(fmt_dual)`), usando `.dt.strftime`. NaT → "".
    Equivalente linha-a-linha a `ts.apply(fmt_dual)`, exceto que NaT vira ""
    (string vazia) em vez de "NaT ...".
    """
    ts = pd.to_datetime(ts, utc=True)
    p = to_primary(ts).dt.strftime(fmt)
    if user_tz() == PRIMARY_TZ:
        out = p + f" {PRIMARY_TZ_SHORT}"
    else:
        u = to_user(ts).dt.strftime(fmt)
        out = p + f" {PRIMARY_TZ_SHORT} / " + u + f" {user_tz_short()}"
    return out.where(ts.notna(), "")


def trade_day_primary(ts: pd.Series) -> pd.Series:
    """Deriva o trade_day em fuso primário (ET) a partir de entered_at.

    Equivalente a `entered_at.dt.tz_convert('America/New_York').dt.date`.
    """
    return to_primary(ts).dt.date
