"""
Helpers puros do dashboard — formatadores e parsing de evento Plotly.

Extraidos de app.py para permitir testes unitarios sem o contexto Streamlit
completo (app.py nao e' importavel em bare-mode por causa de chamadas top-level
a auth.current_user). Sem dependencia de st.*; pandas e' a unica dep externa.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


def fmt_money(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}$ {abs(v):,.2f}"


def fmt_pts(v: float) -> str:
    return f"{v:+,.2f} pts"


def fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def color_class(v: float) -> str:
    return "pos" if v >= 0 else "neg"


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}min"
    return f"{seconds / 3600:.1f}h"


def extract_days_from_event(event: Any) -> set[date]:
    """Extrai conjunto de datas a partir de um evento Plotly de seleção.

    Aceita dois formatos:
    - Barras com `x = trade_day` (string ou Timestamp).
    - Heatmap com `customdata` (ISO date como string ou primeira posição de lista).

    Retorna conjunto vazio quando event/selection/points ausentes ou
    quando nenhum ponto produz data válida.
    """
    if not event or "selection" not in event:
        return set()
    points = event["selection"].get("points") or []
    if not points:
        return set()
    days: set[date] = set()
    for p in points:
        iso = p.get("customdata")
        if isinstance(iso, list):
            iso = iso[0] if iso else None
        raw = iso or p.get("x")
        if raw is None:
            continue
        try:
            d = pd.to_datetime(raw).date()
        except (ValueError, TypeError):
            continue
        days.add(d)
    return days
