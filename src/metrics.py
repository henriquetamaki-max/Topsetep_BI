"""
Métricas e agregações de trades.

Port das fórmulas do projeto TradePontos
(`Templates/TradePontos/backend/core/processor.py`) adaptado ao schema
snake_case da nossa tabela `public.trades`.

Funções puras (sem Streamlit): facilita teste e reuso.
"""

from __future__ import annotations

import pandas as pd


# Nome da coluna usada como "dia da sessão" em KPIs/agregações do Dashboard.
# load_trades() em app.py adiciona `trade_day_et` (sessão NY/ET — fuso primário
# da fusão M7). Se a coluna não estiver presente (caller antigo), `_day_col`
# faz fallback para `trade_day` (CT, vindo do CSV TopStepX).
def _day_col(df: pd.DataFrame) -> str:
    return "trade_day_et" if "trade_day_et" in df.columns else "trade_day"


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


def _to_int(v) -> int:
    """Cast defensivo: `size`/`total_size` são Int64 nullable — um trade com
    size NaN propaga <NA> no sum e `int(<NA>)` levanta. Coerge NaN → 0."""
    n = pd.to_numeric(v, errors="coerce")
    return int(n) if pd.notna(n) else 0


# ---------------------------------------------------------------------------
# Aderência ao plano matinal (daily_plans) — M5 da fusão com Trade_Agent
# ---------------------------------------------------------------------------


VIOLATION_TYPES = (
    "unplanned",        # sem plano (mesmo contrato + direção) no dia
    "size_exceeded",    # operação isolada acima do max_size do plano
    "against_plan",     # plano existe para a contraparte (mesmo contrato, direção OPOSTA)
    "size_creep_day",   # soma do dia em (contrato, direção) ultrapassa max_size
)


def compute_plan_adherence(groups: pd.DataFrame, plans: pd.DataFrame) -> dict:
    """Score de aderência ao plano matinal (M8 da fusão).

    Classifica cada operação real (linha em `groups`) em uma das 5 categorias:

    - **compliant**: plano existe (mesmo contrato + direção) e `total_size <= plan.max_size`.
    - **unplanned**: não há plano nenhum para esse (date, contract, direction).
    - **size_exceeded**: plano existe mas `total_size > plan.max_size`.
    - **against_plan**: plano existe para o contrato em **direção oposta** (trader
      operou contra a tese matinal). Toma precedência sobre `unplanned`.
    - **size_creep_day**: a soma de `total_size` ao longo do dia em
      (contrato, direção) ultrapassa `plan.max_size` — mesmo que cada operação
      individual estivesse dentro do limite. Detecta "tilt distribuído" em
      múltiplos grupos no mesmo dia.

    O `trade_day` da operação usa fuso primário NY/ET (sessão CME). Mesmo
    fuso usado pelo Dashboard via `trade_day_et` (M7).

    Devolve dict com contadores, score percentual e DataFrame `violations`
    pronto para renderização.
    """
    if groups.empty:
        return _empty_adherence()

    g = groups.copy()
    g["trade_day"] = (
        pd.to_datetime(g["group_start"])
        .dt.tz_convert("America/New_York")
        .dt.date
    )

    if plans.empty:
        violations = g.assign(
            violation_type="unplanned", plan_max_size=pd.NA,
        )
        return {
            "total_groups": int(len(g)),
            "compliant": 0,
            "unplanned": int(len(g)),
            "size_exceeded": 0,
            "against_plan": 0,
            "size_creep_day": 0,
            "score_pct": 0.0,
            "violations": violations,
        }

    # Índices auxiliares:
    # - plan_idx: (date, contract, direction) -> max_size — match exato.
    # - plan_contracts_by_day: (date, contract) -> set(direction) — para detectar
    #   against_plan (existe plano para o contrato em direção oposta).
    plan_idx = (
        plans.set_index(["plan_date", "contract_name", "direction"])["max_size"]
        .to_dict()
    )
    plan_contracts_by_day: dict[tuple, set] = {}
    for _, p in plans.iterrows():
        key = (p["plan_date"], p["contract_name"])
        plan_contracts_by_day.setdefault(key, set()).add(p["direction"])

    # Acumulador para size_creep_day: soma rolante de total_size por
    # (day, contract, direction) — processada em ordem temporal para que
    # a primeira operação que cruzar o limite seja a flagada.
    g_sorted = g.sort_values("group_start").copy()
    day_cumulative: dict[tuple, int] = {}
    creep_flags: dict[int, int] = {}  # group_id -> max_size do plano quando estourou

    for idx, row in g_sorted.iterrows():
        key = (row["trade_day"], row["contract_name"], row["type"])
        max_size = plan_idx.get(key)
        if max_size is None:
            continue
        prev = day_cumulative.get(key, 0)
        new_total = prev + _to_int(row["total_size"])
        day_cumulative[key] = new_total
        # Marca creep apenas se já há volume anterior (>0) e o acumulado
        # acabou de cruzar — operações isoladas que estouram sozinhas viram
        # size_exceeded, não creep.
        if prev > 0 and new_total > int(max_size) and _to_int(row["total_size"]) <= int(max_size):
            creep_flags[int(row["group_id"])] = int(max_size)

    def _classify(row: pd.Series) -> pd.Series:
        key = (row["trade_day"], row["contract_name"], row["type"])
        max_size = plan_idx.get(key)

        if max_size is None:
            # Sem plano exato. Checa direção oposta para distinguir against_plan
            # de unplanned puro.
            day_contract = (row["trade_day"], row["contract_name"])
            sides = plan_contracts_by_day.get(day_contract, set())
            opposite = {"Long": "Short", "Short": "Long"}.get(row["type"])
            if opposite and opposite in sides:
                opp_max = plan_idx.get((*day_contract, opposite))
                return pd.Series({
                    "violation_type": "against_plan",
                    "plan_max_size": int(opp_max) if opp_max is not None else pd.NA,
                })
            return pd.Series({"violation_type": "unplanned", "plan_max_size": pd.NA})

        if _to_int(row["total_size"]) > int(max_size):
            return pd.Series({
                "violation_type": "size_exceeded",
                "plan_max_size": int(max_size),
            })

        # size_creep_day toma precedência sobre compliant nesta linha específica.
        if int(row["group_id"]) in creep_flags:
            return pd.Series({
                "violation_type": "size_creep_day",
                "plan_max_size": creep_flags[int(row["group_id"])],
            })

        return pd.Series({"violation_type": pd.NA, "plan_max_size": int(max_size)})

    g[["violation_type", "plan_max_size"]] = g.apply(_classify, axis=1)

    counts = {v: int((g["violation_type"] == v).sum()) for v in VIOLATION_TYPES}
    compliant = int(g["violation_type"].isna().sum())
    total = int(len(g))
    score_pct = 100.0 * compliant / total if total else 0.0
    violations = g[g["violation_type"].notna()].copy()

    return {
        "total_groups": total,
        "compliant": compliant,
        "score_pct": score_pct,
        "violations": violations,
        **counts,
    }


def _empty_adherence() -> dict:
    return {
        "total_groups": 0,
        "compliant": 0,
        "unplanned": 0,
        "size_exceeded": 0,
        "against_plan": 0,
        "size_creep_day": 0,
        "score_pct": 0.0,
        "violations": pd.DataFrame(),
    }


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
    """Daily breakdown. Espelha `processor.py:274-293`.

    Usa `trade_day_et` (sessão NY/ET) se presente; senão fallback para
    `trade_day` (CT, vindo do CSV TopStepX). A coluna devolvida mantém o
    nome `trade_day` para preservar compatibilidade com os charts.
    """
    day_col = _day_col(df)
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
        df.groupby(day_col)
        .apply(_agg, include_groups=False)
        .reset_index()
        .rename(columns={day_col: "trade_day"})
        .sort_values("trade_day")
    )
    return out


# ---------------------------------------------------------------------------
# Overview — KPIs em $ no estilo do dashboard TopStepX
# ---------------------------------------------------------------------------


# Buckets de duração (em segundos) replicando o painel TopStepX
# "Trade Duration Analysis" / "Win Rate Analysis".
DURATION_BUCKETS: list[tuple[str, float, float]] = [
    ("Under 15 sec", 0, 15),
    ("15-45 sec", 15, 45),
    ("45 sec - 1 min", 45, 60),
    ("1 min - 2 min", 60, 120),
    ("2 min - 5 min", 120, 300),
    ("5 min - 10 min", 300, 600),
    ("10 min - 30 min", 600, 1800),
    ("30 min - 1 hour", 1800, 3600),
    ("1 hour - 2 hours", 3600, 7200),
    ("2 hours - 4 hours", 7200, 14400),
    ("4 hours and up", 14400, float("inf")),
]


def compute_overview(df: pd.DataFrame) -> dict:
    """KPIs em $ no estilo TopStepX (Day Win %, Best Day %, Best/Worst Trade,
    Avg Win/Loss em $, Avg Duration, Total Lots, Trade Direction %).
    Tudo derivado dos trades já filtrados.
    """
    if df.empty:
        return _empty_overview()

    d = df.copy()
    d["duration_sec"] = (d["exited_at"] - d["entered_at"]).dt.total_seconds()
    day_col = _day_col(d)

    wins = d[d["pnl_net"] > 0]
    losses = d[d["pnl_net"] < 0]

    daily_pnl = d.groupby(day_col, as_index=False)["pnl_net"].sum().rename(
        columns={day_col: "trade_day"}
    )
    day_total = float(daily_pnl["pnl_net"].sum())
    winning_days = int((daily_pnl["pnl_net"] > 0).sum())
    total_days = int(len(daily_pnl))
    day_win_pct = (winning_days / total_days) if total_days else 0.0

    best_day = daily_pnl.loc[daily_pnl["pnl_net"].idxmax()] if total_days else None
    worst_day = daily_pnl.loc[daily_pnl["pnl_net"].idxmin()] if total_days else None
    # Razão do melhor dia sobre o total — só faz sentido quando o total é > 0.
    best_day_pct_of_total = (
        float(best_day["pnl_net"] / day_total) if best_day is not None and day_total > 0 else 0.0
    )

    # Best/Worst trade individual (por PnL líquido).
    best_trade = d.loc[d["pnl_net"].idxmax()] if len(d) else None
    worst_trade = d.loc[d["pnl_net"].idxmin()] if len(d) else None

    longs = d[d["type"] == "Long"]
    shorts = d[d["type"] == "Short"]
    long_pct = float(len(longs) / len(d)) if len(d) else 0.0
    short_pct = float(len(shorts) / len(d)) if len(d) else 0.0

    return {
        "total_pnl_net": float(d["pnl_net"].sum()),
        "trade_count": int(len(d)),
        "winning_trades": int(len(wins)),
        "losing_trades": int(len(losses)),
        "total_lots": int(pd.to_numeric(d["size"], errors="coerce").fillna(0).sum()),
        "avg_winning_trade": float(wins["pnl_net"].mean()) if not wins.empty else 0.0,
        "avg_losing_trade": float(losses["pnl_net"].mean()) if not losses.empty else 0.0,
        "avg_trade_duration_sec": float(d["duration_sec"].mean()),
        "avg_win_duration_sec": float(wins["duration_sec"].mean()) if not wins.empty else 0.0,
        "avg_loss_duration_sec": float(losses["duration_sec"].mean()) if not losses.empty else 0.0,
        "day_win_pct": day_win_pct,
        "winning_days": winning_days,
        "total_days": total_days,
        "best_day_pct_of_total": best_day_pct_of_total,
        "best_day": (best_day["trade_day"], float(best_day["pnl_net"])) if best_day is not None else None,
        "worst_day": (worst_day["trade_day"], float(worst_day["pnl_net"])) if worst_day is not None else None,
        "best_trade": _trade_summary(best_trade) if best_trade is not None else None,
        "worst_trade": _trade_summary(worst_trade) if worst_trade is not None else None,
        "long_pct": long_pct,
        "short_pct": short_pct,
        "long_count": int(len(longs)),
        "short_count": int(len(shorts)),
    }


def _trade_summary(row: pd.Series) -> dict:
    return {
        "id": int(row["id"]),
        "contract_name": str(row["contract_name"]),
        "type": str(row["type"]),
        "size": int(row["size"]) if pd.notna(row["size"]) else 0,
        "entry_price": float(row["entry_price"]),
        "exit_price": float(row["exit_price"]),
        "pnl_net": float(row["pnl_net"]),
        "entered_at": row["entered_at"],
        "exited_at": row["exited_at"],
    }


def _empty_overview() -> dict:
    return {
        "total_pnl_net": 0.0, "trade_count": 0,
        "winning_trades": 0, "losing_trades": 0, "total_lots": 0,
        "avg_winning_trade": 0.0, "avg_losing_trade": 0.0,
        "avg_trade_duration_sec": 0.0,
        "avg_win_duration_sec": 0.0, "avg_loss_duration_sec": 0.0,
        "day_win_pct": 0.0, "winning_days": 0, "total_days": 0,
        "best_day_pct_of_total": 0.0,
        "best_day": None, "worst_day": None,
        "best_trade": None, "worst_trade": None,
        "long_pct": 0.0, "short_pct": 0.0, "long_count": 0, "short_count": 0,
    }


def compute_duration_buckets(df: pd.DataFrame) -> pd.DataFrame:
    """Trade counts e win rate por bucket de duração (estilo TopStepX)."""
    cols = ["bucket", "trades", "wins", "win_rate"]
    if df.empty:
        return pd.DataFrame({c: [] for c in cols})
    d = df.copy()
    d["duration_sec"] = (d["exited_at"] - d["entered_at"]).dt.total_seconds()
    rows: list[dict] = []
    for label, lo, hi in DURATION_BUCKETS:
        mask = (d["duration_sec"] >= lo) & (d["duration_sec"] < hi)
        sub = d[mask]
        n = int(len(sub))
        wins = int((sub["pnl_net"] > 0).sum())
        rows.append({
            "bucket": label,
            "trades": n,
            "wins": wins,
            "win_rate": (wins / n) if n else 0.0,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Coach — análise comportamental determinística (sem LLM)
# ---------------------------------------------------------------------------


REVENGE_WINDOW_MIN = 5          # trade <= 5 min após loss grande
TILT_TRADES_PER_DAY_QUANTILE = 0.75
LEAK_MIN_TRADES = 3              # mínimo de trades para considerar uma combinação


def compute_coach(df: pd.DataFrame, groups: pd.DataFrame) -> dict:
    """Análise comportamental: padrões, vazamentos, pontos fortes.

    Retorna dict pronto pra renderização. Tudo derivado dos trades já
    filtrados — respeita os filtros da sidebar automaticamente.
    """
    if df.empty:
        return _empty_coach()

    d = df.sort_values("entered_at").reset_index(drop=True).copy()
    d["pnl_net"] = pd.to_numeric(d["pnl_net"], errors="coerce")
    d["duration_sec"] = (d["exited_at"] - d["entered_at"]).dt.total_seconds()

    return {
        "headline": _coach_headline(d, groups),
        "revenge": _coach_revenge(d),
        "cut_winners_hold_losers": _coach_cut_hold(d),
        "overtrading": _coach_overtrading(d),
        "losing_streak": _coach_losing_streak(d),
        "leaks": _coach_combo(d, kind="leak"),
        "strengths": _coach_combo(d, kind="strength"),
        "size_buckets": _coach_size_buckets(d),
        "points_distribution": _coach_points_dist(d),
        "checklist": _coach_checklist(d),
    }


def _empty_coach() -> dict:
    return {
        "headline": [],
        "revenge": {"count": 0, "pnl": 0.0, "baseline_avg_pnl": 0.0, "revenge_avg_pnl": 0.0},
        "cut_winners_hold_losers": {
            "avg_win_sec": 0.0, "avg_loss_sec": 0.0, "ratio": 0.0, "flag": False,
        },
        "overtrading": {
            "threshold": 0, "tilt_days": 0, "tilt_avg_pnl": 0.0, "normal_avg_pnl": 0.0,
        },
        "losing_streak": {"length": 0, "pnl": 0.0, "start": None, "end": None},
        "leaks": pd.DataFrame(),
        "strengths": pd.DataFrame(),
        "size_buckets": pd.DataFrame(),
        "points_distribution": {"values": [], "mean": 0.0, "median": 0.0},
        "checklist": [],
    }


def _coach_revenge(d: pd.DataFrame) -> dict:
    """Trades abertos logo após uma perda significativa."""
    if len(d) < 2:
        return {"count": 0, "pnl": 0.0, "baseline_avg_pnl": 0.0, "revenge_avg_pnl": 0.0}
    losses = d[d["pnl_net"] < 0]
    if losses.empty:
        return {"count": 0, "pnl": 0.0, "baseline_avg_pnl": float(d["pnl_net"].mean()), "revenge_avg_pnl": 0.0}
    big_loss_threshold = float(losses["pnl_net"].mean())  # média (negativa) — losses piores que ela
    d = d.copy()
    d["prev_pnl"] = d["pnl_net"].shift(1)
    d["gap_min"] = (d["entered_at"] - d["exited_at"].shift(1)).dt.total_seconds() / 60.0
    revenge_mask = (
        (d["prev_pnl"] <= big_loss_threshold)
        & (d["gap_min"] >= 0)
        & (d["gap_min"] <= REVENGE_WINDOW_MIN)
    )
    rev = d[revenge_mask]
    non_rev = d[~revenge_mask]
    return {
        "count": int(len(rev)),
        "pnl": float(rev["pnl_net"].sum()),
        "baseline_avg_pnl": float(non_rev["pnl_net"].mean()) if not non_rev.empty else 0.0,
        "revenge_avg_pnl": float(rev["pnl_net"].mean()) if not rev.empty else 0.0,
    }


def _coach_cut_hold(d: pd.DataFrame) -> dict:
    """Assimetria de duração entre wins e losses."""
    wins = d[d["pnl_net"] > 0]
    losses = d[d["pnl_net"] < 0]
    avg_w = float(wins["duration_sec"].mean()) if not wins.empty else 0.0
    avg_l = float(losses["duration_sec"].mean()) if not losses.empty else 0.0
    ratio = (avg_l / avg_w) if avg_w > 0 else 0.0
    return {
        "avg_win_sec": avg_w,
        "avg_loss_sec": avg_l,
        "ratio": ratio,
        "flag": ratio >= 2.0,
    }


def _coach_overtrading(d: pd.DataFrame) -> dict:
    """Compara dias acima do p75 de nº de trades vs. dias normais."""
    day_col = _day_col(d)
    per_day = d.groupby(day_col).agg(
        trades=("id", "count"), pnl=("pnl_net", "sum"),
    ).reset_index()
    if per_day.empty:
        return {"threshold": 0, "tilt_days": 0, "tilt_avg_pnl": 0.0, "normal_avg_pnl": 0.0}
    threshold = float(per_day["trades"].quantile(TILT_TRADES_PER_DAY_QUANTILE))
    tilt = per_day[per_day["trades"] > threshold]
    normal = per_day[per_day["trades"] <= threshold]
    return {
        "threshold": int(threshold),
        "tilt_days": int(len(tilt)),
        "tilt_avg_pnl": float(tilt["pnl"].mean()) if not tilt.empty else 0.0,
        "normal_avg_pnl": float(normal["pnl"].mean()) if not normal.empty else 0.0,
    }


def _coach_losing_streak(d: pd.DataFrame) -> dict:
    """Maior sequência de losses consecutivos e o PnL acumulado dela."""
    if d.empty:
        return {"length": 0, "pnl": 0.0, "start": None, "end": None}
    best_len = 0
    best_pnl = 0.0
    best_start = best_end = None
    cur_len = 0
    cur_pnl = 0.0
    cur_start = None
    for _, row in d.iterrows():
        if row["pnl_net"] < 0:
            if cur_len == 0:
                cur_start = row["entered_at"]
            cur_len += 1
            cur_pnl += float(row["pnl_net"])
            if cur_len > best_len:
                best_len = cur_len
                best_pnl = cur_pnl
                best_start = cur_start
                best_end = row["entered_at"]
        else:
            cur_len = 0
            cur_pnl = 0.0
            cur_start = None
    return {
        "length": int(best_len),
        "pnl": float(best_pnl),
        "start": best_start,
        "end": best_end,
    }


def _coach_combo(d: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Top combinações contrato × hora × dia da semana por PnL.

    kind='leak' → piores (PnL negativo); kind='strength' → melhores.
    """
    # `weekday`/`entry_hour` são derivadas em load_trades (app.py); um caller
    # fora da UI (teste, CLI, outra origem) pode não tê-las. Guarda espelha
    # _coach_size_buckets — devolve vazio em vez de KeyError.
    if not {"contract_name", "weekday", "entry_hour"}.issubset(d.columns):
        return pd.DataFrame()
    g = d.groupby(["contract_name", "weekday", "entry_hour"], as_index=False).agg(
        trades=("id", "count"),
        pnl=("pnl_net", "sum"),
        avg_pnl=("pnl_net", "mean"),
        win_rate=("pnl_net", lambda s: (s > 0).mean()),
    )
    g = g[g["trades"] >= LEAK_MIN_TRADES]
    if g.empty:
        return g
    if kind == "leak":
        g = g[g["pnl"] < 0].sort_values("pnl", ascending=True)
    else:
        g = g[g["pnl"] > 0].sort_values("pnl", ascending=False)
    return g.head(5).reset_index(drop=True)


def _coach_size_buckets(d: pd.DataFrame) -> pd.DataFrame:
    """PnL médio por tamanho de posição (size)."""
    if "size" not in d.columns:
        return pd.DataFrame()
    g = d.groupby("size", as_index=False).agg(
        trades=("id", "count"),
        total_pnl=("pnl_net", "sum"),
        avg_pnl=("pnl_net", "mean"),
        win_rate=("pnl_net", lambda s: (s > 0).mean()),
    ).sort_values("size")
    return g


def _coach_points_dist(d: pd.DataFrame) -> dict:
    pts = pd.to_numeric(d["points"], errors="coerce").dropna()
    if pts.empty:
        return {"values": [], "mean": 0.0, "median": 0.0}
    return {
        "values": pts.tolist(),
        "mean": float(pts.mean()),
        "median": float(pts.median()),
    }


def _coach_headline(d: pd.DataFrame, groups: pd.DataFrame) -> list[str]:
    """3-5 bullets de leitura rápida."""
    out: list[str] = []
    total_pnl = float(d["pnl_net"].sum())
    total = int(len(d))
    wins = d[d["pnl_net"] > 0]
    losses = d[d["pnl_net"] <= 0]
    win_rate = len(wins) / total if total else 0.0
    pf = float(wins["pnl_net"].sum() / abs(losses["pnl_net"].sum())) if not losses.empty and losses["pnl_net"].sum() != 0 else 0.0
    avg_win = float(wins["pnl_net"].mean()) if not wins.empty else 0.0
    avg_loss = float(losses["pnl_net"].mean()) if not losses.empty else 0.0

    if pf >= 1.5:
        out.append(f"Profit factor saudável: **{pf:.2f}** — sistema com edge positivo.")
    elif pf >= 1.0:
        out.append(f"Profit factor marginal: **{pf:.2f}** — operando perto do breakeven.")
    else:
        out.append(f"Profit factor abaixo de 1: **{pf:.2f}** — perdendo mais do que ganha.")

    if win_rate >= 0.55:
        out.append(f"Win rate alto ({win_rate*100:.0f}%) — você acerta a direção com frequência.")
    elif win_rate < 0.4 and avg_win > 0 and abs(avg_loss) > 0 and avg_win / abs(avg_loss) >= 1.5:
        out.append(f"Win rate baixo ({win_rate*100:.0f}%) mas avg win / avg loss = {avg_win/abs(avg_loss):.2f}: estratégia de poucos trades grandes.")
    else:
        out.append(f"Win rate em {win_rate*100:.0f}% com avg win ${avg_win:.0f} vs avg loss ${avg_loss:.0f}.")

    if avg_win > 0 and abs(avg_loss) > avg_win:
        out.append(f"⚠️ Avg loss (${avg_loss:.0f}) maior que avg win (${avg_win:.0f}) — losses estão grandes demais.")

    if not groups.empty:
        n_add = int((groups["additions_count"] > 0).sum())
        if n_add > 0:
            add_winrate = float(
                (groups[groups["additions_count"] > 0]["points_status"] == "Winner").mean()
            )
            no_add_winrate = float(
                (groups[groups["additions_count"] == 0]["points_status"] == "Winner").mean()
            ) if (groups["additions_count"] == 0).any() else 0.0
            delta = add_winrate - no_add_winrate
            if delta < -0.05:
                out.append(f"⚠️ Adições reduzem win rate: {add_winrate*100:.0f}% com adição vs {no_add_winrate*100:.0f}% sem.")
            elif delta > 0.05:
                out.append(f"Adições ajudam: {add_winrate*100:.0f}% com adição vs {no_add_winrate*100:.0f}% sem.")

    out.append(f"PnL líquido no período filtrado: **${total_pnl:,.2f}** em {total} trades.")
    return out


def _coach_checklist(d: pd.DataFrame) -> list[str]:
    """Regras acionáveis derivadas dos vazamentos."""
    items: list[str] = []
    leaks = _coach_combo(d, kind="leak")
    for _, r in leaks.head(3).iterrows():
        items.append(
            f"Evite **{r['contract_name']}** {r['weekday']} ~{int(r['entry_hour'])}h (BRT): "
            f"{int(r['trades'])} trades, ${r['pnl']:,.0f} acumulado."
        )
    cut = _coach_cut_hold(d)
    if cut["flag"]:
        items.append(
            f"Você segura losses {cut['ratio']:.1f}× mais tempo que wins "
            f"(avg loss {cut['avg_loss_sec']/60:.1f}min vs avg win {cut['avg_win_sec']/60:.1f}min). "
            "Defina stop fixo antes de entrar."
        )
    rev = _coach_revenge(d)
    if rev["count"] >= 3 and rev["pnl"] < 0:
        items.append(
            f"Revenge trading: {rev['count']} trades em <{REVENGE_WINDOW_MIN}min após loss grande, "
            f"PnL ${rev['pnl']:,.0f}. Imponha pausa de 10min após loss acima da média."
        )
    over = _coach_overtrading(d)
    if over["tilt_days"] > 0 and over["tilt_avg_pnl"] < over["normal_avg_pnl"]:
        items.append(
            f"Dias com >{over['threshold']} trades rendem ${over['tilt_avg_pnl']:,.0f} médio "
            f"vs ${over['normal_avg_pnl']:,.0f} em dias normais. Cap diário sugerido: {over['threshold']} trades."
        )
    return items
