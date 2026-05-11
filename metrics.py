"""
Métricas e agregações de trades.

Port das fórmulas do projeto TradePontos
(`Templates/TradePontos/backend/core/processor.py`) adaptado ao schema
snake_case da nossa tabela `public.trades`.

Funções puras (sem Streamlit): facilita teste e reuso.
"""

from __future__ import annotations

import pandas as pd


# ---------------------------------------------------------------------------
# Overlap grouping engine — "operações" (group_id) a partir de trades
# ---------------------------------------------------------------------------


def compute_groups(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Atribui `group_id` aos trades e devolve (df_anotado, groups).

    Regra: dois trades pertencem ao mesmo grupo se compartilham
    `(contract_name, type)` e o `entered_at` do novo trade é <= ao
    `exited_at` máximo já visto no grupo (overlap temporal). O fim do
    grupo é estendido dinamicamente.

    Espelha `processor.py:158-190` do TradePontos.
    """
    if df.empty:
        return df.assign(group_id=pd.Series(dtype="int64")), pd.DataFrame()

    df = df.sort_values("entered_at").reset_index(drop=True).copy()
    df["group_id"] = -1
    next_id = 1

    for (_contract, _ttype), sub in df.groupby(["contract_name", "type"], sort=False):
        cur_id: int | None = None
        cur_end: pd.Timestamp | None = None
        for idx, row in sub.iterrows():
            entered = row["entered_at"]
            exited = row["exited_at"]
            if cur_id is None or entered > cur_end:
                cur_id = next_id
                next_id += 1
                cur_end = exited
            else:
                cur_end = max(cur_end, exited)
            df.at[idx, "group_id"] = cur_id

    groups = (
        df.groupby("group_id")
        .agg(
            contract_name=("contract_name", "first"),
            type=("type", "first"),
            group_start=("entered_at", "min"),
            group_end=("exited_at", "max"),
            trade_count=("id", "count"),
            total_points=("points", "sum"),
            total_pnl=("pnl", "sum"),
            total_net_pnl=("pnl_net", "sum"),
            total_size=("size", "sum"),
        )
        .reset_index()
    )
    groups["additions_count"] = groups["trade_count"] - 1
    groups["has_addition"] = groups["additions_count"] > 0
    groups["duration_min"] = (
        (groups["group_end"] - groups["group_start"]).dt.total_seconds() / 60.0
    )
    groups["points_status"] = groups["total_points"].apply(_status)
    groups["pnl_status"] = groups["total_pnl"].apply(_status)
    return df, groups


def _status(v: float) -> str:
    if v > 0:
        return "Winner"
    if v < 0:
        return "Loser"
    return "Flat"


# ---------------------------------------------------------------------------
# KPIs — em pontos (independente de tamanho/comissões)
# ---------------------------------------------------------------------------


def compute_kpis(df: pd.DataFrame, groups: pd.DataFrame) -> dict:
    """KPIs em pontos. Espelha `processor.py:252-271`."""
    if df.empty:
        return {
            "total_net_points": 0.0,
            "total_winning_points": 0.0,
            "total_losing_points": 0.0,
            "avg_points_per_trade": 0.0,
            "avg_winning_trade_points": 0.0,
            "avg_losing_trade_points": 0.0,
            "trade_count": 0,
            "winning_trade_count": 0,
            "losing_trade_count": 0,
            "rr_average": 0.0,
            "rr_aggregate": 0.0,
            "total_grouped_operations": 0,
            "win_rate_grouped": 0.0,
        }
    winners = df[df["points"] > 0]
    losers = df[df["points"] < 0]
    total_win = float(winners["points"].sum())
    total_loss = float(losers["points"].sum())
    mean_win = float(winners["points"].mean()) if not winners.empty else 0.0
    mean_loss = float(abs(losers["points"].mean())) if not losers.empty else 0.0
    rr_agg = total_win / abs(total_loss) if total_loss != 0 else 0.0
    rr_avg = mean_win / mean_loss if mean_loss != 0 else 0.0
    win_rate_grouped = (
        float((groups["points_status"] == "Winner").sum() / len(groups))
        if len(groups)
        else 0.0
    )
    return {
        "total_net_points": float(df["points"].sum()),
        "total_winning_points": total_win,
        "total_losing_points": total_loss,
        "avg_points_per_trade": float(df["points"].mean()),
        "avg_winning_trade_points": mean_win,
        "avg_losing_trade_points": mean_loss,
        "trade_count": int(len(df)),
        "winning_trade_count": int(len(winners)),
        "losing_trade_count": int(len(losers)),
        "rr_average": rr_avg,
        "rr_aggregate": rr_agg,
        "total_grouped_operations": int(len(groups)),
        "win_rate_grouped": win_rate_grouped,
    }


# ---------------------------------------------------------------------------
# Segmentação por adições — 4 buckets de operações
# ---------------------------------------------------------------------------


def compute_segments(groups: pd.DataFrame) -> dict[str, dict]:
    """4 segmentos. Espelha `processor.py:232-249`."""
    if groups.empty:
        return {
            "no_additions": _empty_segment(),
            "with_additions": _empty_segment(),
            "with_additions_winners": _empty_segment(),
            "with_additions_losers": _empty_segment(),
        }
    no_add = groups[~groups["has_addition"]]
    with_add = groups[groups["has_addition"]]
    win_add = groups[(groups["has_addition"]) & (groups["points_status"] == "Winner")]
    lose_add = groups[(groups["has_addition"]) & (groups["points_status"] == "Loser")]
    return {
        "no_additions": _segment(no_add),
        "with_additions": _segment(with_add),
        "with_additions_winners": _segment(win_add),
        "with_additions_losers": _segment(lose_add),
    }


def _segment(sub: pd.DataFrame) -> dict:
    n = len(sub)
    if n == 0:
        return _empty_segment()
    return {
        "count": int(n),
        "total_points": float(sub["total_points"].sum()),
        "total_pnl": float(sub["total_pnl"].sum()),
        "avg_points": float(sub["total_points"].mean()),
        "avg_pnl": float(sub["total_pnl"].mean()),
        "win_rate_by_group": float((sub["points_status"] == "Winner").sum() / n),
        "avg_additions": float(sub["additions_count"].mean()),
        "total_size": float(sub["total_size"].sum()),
    }


def _empty_segment() -> dict:
    return {
        "count": 0,
        "total_points": 0.0,
        "total_pnl": 0.0,
        "avg_points": 0.0,
        "avg_pnl": 0.0,
        "win_rate_by_group": 0.0,
        "avg_additions": 0.0,
        "total_size": 0.0,
    }


# ---------------------------------------------------------------------------
# Daily metrics — pontos vencedores/perdedores e contratos por dia
# ---------------------------------------------------------------------------


def compute_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Daily breakdown. Espelha `processor.py:274-293`."""
    if df.empty:
        return pd.DataFrame(
            columns=[
                "trade_day",
                "net_points",
                "winning_points",
                "losing_points",
                "reward_risk",
                "total_size",
                "winning_size",
                "losing_size",
            ]
        )

    def _agg(g: pd.DataFrame) -> pd.Series:
        wins = g[g["points"] > 0]
        losses = g[g["points"] < 0]
        w_pts = float(wins["points"].sum())
        l_pts = float(losses["points"].sum())
        rr = w_pts / abs(l_pts) if l_pts != 0 else 0.0
        return pd.Series(
            {
                "net_points": float(g["points"].sum()),
                "winning_points": w_pts,
                "losing_points": l_pts,
                "reward_risk": rr,
                "total_size": float(g["size"].sum()),
                "winning_size": float(wins["size"].sum()),
                "losing_size": float(losses["size"].sum()),
            }
        )

    out = (
        df.groupby("trade_day")
        .apply(_agg, include_groups=False)
        .reset_index()
        .sort_values("trade_day")
    )
    return out
