"""
BI TopStep — Dashboard de trades.

Cross-filter estilo PowerBI: filtros na sidebar (período, contrato, tipo,
dia da semana) reaplicam em TODOS os gráficos abaixo.

Métricas em $ + em pontos (port do projeto TradePontos), com análise de
overlap grouping e segmentação por adições.

Rodar:   streamlit run app.py
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import action_plan
import auth
import coach_ai
import i18n
import ingest_core
import metrics
from i18n import t

ROOT = Path(__file__).resolve().parent

# ----------------------------- Tema / Cores ----------------------------------

GREEN = "#7fc7a4"
RED = "#e08585"
GREY = "#3a3f4b"
BG = "#0e1117"
TEXT = "#e6e6e6"
MUTED = "#9aa0a6"
BLUE = "#65b5ff"

PLOTLY_LAYOUT = dict(
    paper_bgcolor=BG,
    plot_bgcolor=BG,
    font=dict(color=TEXT, family="Inter, system-ui, sans-serif"),
    margin=dict(l=10, r=10, t=40, b=10),
    xaxis=dict(gridcolor=GREY, zerolinecolor=GREY),
    yaxis=dict(gridcolor=GREY, zerolinecolor=GREY),
)

st.set_page_config(
    page_title="X-Metrics",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    /* Escala tipográfica:
       --fs-label  → títulos uppercase de cartões (segment-box h4, coach-card h4)
       --fs-body   → texto corrido em cartões (segment-row, coach-card p, coach-check)
       --fs-metric → valor grande dos KPIs (stMetricValue)
    */
    :root {
        --fs-label: 0.8rem;
        --fs-body: 0.9rem;
        --fs-metric: 1.5rem;
    }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    [data-testid="stMetricValue"] { font-size: var(--fs-metric); font-weight: 600; }
    [data-testid="stMetricLabel"] { color: #9aa0a6; }
    .segment-box {
        background:#161a23; border:1px solid #2a2f3a; border-radius:8px;
        padding:14px 16px; height:100%;
    }
    .segment-box h4 {
        font-size: var(--fs-label); letter-spacing:1px; text-transform:uppercase;
        color:#9aa0a6; margin:0 0 10px 0; font-weight:600;
    }
    .segment-row { display:flex; justify-content:space-between; font-size: var(--fs-body);
        padding:3px 0; border-bottom:1px dotted #2a2f3a;}
    .segment-row:last-child { border-bottom:none; }
    .segment-row span:first-child { color:#9aa0a6; }
    .segment-row span:last-child { color:#e6e6e6; font-weight:600; }
    .pos { color:#7fc7a4 !important; }
    .neg { color:#e08585 !important; }
    .coach-card {
        background:#161a23; border:1px solid #2a2f3a; border-radius:8px;
        padding:14px 18px; margin-bottom:10px;
    }
    .coach-card h4 {
        margin:0 0 8px 0; font-size: var(--fs-label); letter-spacing:.5px;
        text-transform:uppercase; color:#9aa0a6; font-weight:600;
    }
    .coach-card p { margin:4px 0; font-size: var(--fs-body); color:#e6e6e6; }
    .coach-check { padding:8px 0; border-bottom:1px dotted #2a2f3a; font-size: var(--fs-body); }
    .coach-check:last-child { border-bottom:none; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------- Auth gate -------------------------------------

auth.login_screen()  # bloqueia o app se o usuário não estiver logado
_user = auth.current_user()

# i18n: precisa rodar depois do login para conseguir ler preferred_language
# do user_metadata. O language_selector é renderizado mais abaixo, no topo
# da sidebar.
i18n.init()


# ----------------------------- Data loading ----------------------------------


@st.cache_data(ttl=60)
def load_trades(user_id: str) -> pd.DataFrame:
    # `user_id` é parte da chave do cache: dois usuários logados nunca
    # compartilham o mesmo DataFrame em memória. A RLS no banco já garante
    # filtragem; este parâmetro é só pro Streamlit isolar o cache.
    del user_id  # usado apenas como chave de cache
    client = auth.get_client()
    rows: list[dict] = []
    page = 0
    while True:
        r = (
            client.table("trades")
            .select("*")
            .order("entered_at", desc=False)
            .range(page * 1000, page * 1000 + 999)
            .execute()
        )
        rows.extend(r.data)
        if len(r.data) < 1000:
            break
        page += 1

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["entered_at"] = pd.to_datetime(df["entered_at"], utc=True)
    df["exited_at"] = pd.to_datetime(df["exited_at"], utc=True)
    df["trade_day"] = pd.to_datetime(df["trade_day"]).dt.date
    for c in ("entry_price", "exit_price", "fees", "commissions", "pnl", "pnl_net", "points"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["size"] = pd.to_numeric(df["size"], errors="coerce").astype("Int64")
    df["entry_hour"] = df["entered_at"].dt.tz_convert("America/Sao_Paulo").dt.hour
    df["weekday"] = pd.to_datetime(df["trade_day"]).dt.day_name()
    if "points" not in df.columns:
        # fallback caso o ALTER TABLE ainda não tenha rodado
        df["points"] = df.apply(
            lambda r: (r["exit_price"] - r["entry_price"])
            if r["type"] == "Long"
            else (r["entry_price"] - r["exit_price"]),
            axis=1,
        )
    return df


# ----------------------------- Helpers ---------------------------------------


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


def _apply_day_selection_from_event(event) -> None:
    """Lê pontos selecionados de um plotly_chart(on_select="rerun") e merge no
    session_state["selected_days"]. Aceita barras (x = trade_day) e heatmap
    (customdata = ISO date). Rerun se a seleção mudou de fato.
    """
    if not event or "selection" not in event:
        return
    points = event["selection"].get("points") or []
    if not points:
        return
    days: set = set()
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
    if not days:
        return
    current = set(st.session_state.get("selected_days", []))
    merged = current | days
    if merged != current:
        st.session_state["selected_days"] = sorted(merged)
        # Limpa a key do widget para que o multiselect releia o novo `default`
        # no próximo run em vez de manter o valor antigo.
        st.session_state.pop("selected_days_widget", None)
        st.rerun()


# ----------------------------- Renderers -------------------------------------


def render_dashboard(
    df: pd.DataFrame,
    df_with_groups: pd.DataFrame,
    groups: pd.DataFrame,
    pts_kpis: dict,
    segments: dict,
    daily: pd.DataFrame,
    overview: dict,
) -> None:
    # --- KPIs em $ (linha 1) -------------------------------------------------
    total_pnl = overview["total_pnl_net"]
    total_trades = overview["trade_count"]
    wins = df[df["pnl_net"] > 0]
    losses = df[df["pnl_net"] <= 0]
    win_rate = 100.0 * len(wins) / total_trades if total_trades else 0.0
    profit_factor = (
        float(wins["pnl_net"].sum() / abs(losses["pnl_net"].sum()))
        if len(losses) and losses["pnl_net"].sum() != 0
        else float("inf")
    )

    daily_pnl = df.groupby("trade_day", as_index=False)["pnl_net"].sum().sort_values("trade_day")

    st.subheader(t("dash.kpis_usd"))
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric(t("dash.kpi.total_pnl_net"), fmt_money(total_pnl))
    c2.metric(t("dash.kpi.trade_win_pct"), f"{win_rate:.1f}%")
    c3.metric(
        t("dash.kpi.avg_win_loss"),
        f"{fmt_money(overview['avg_winning_trade'])} / {fmt_money(overview['avg_losing_trade'])}",
    )
    c4.metric(
        t("dash.kpi.day_win_pct"),
        f"{overview['day_win_pct'] * 100:.1f}%",
        t("dash.kpi.day_win_delta", wins=overview['winning_days'], total=overview['total_days']),
    )
    c5.metric(t("dash.kpi.profit_factor"), f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞")
    c6.metric(t("dash.kpi.best_day_pct"), f"{overview['best_day_pct_of_total'] * 100:.1f}%")

    # --- KPIs em $ (linha 2) — volume e direção ------------------------------
    c7, c8, c9, c10, c11, c12 = st.columns(6)
    best_day_date = overview["best_day"][0] if overview["best_day"] else None
    best_day_val = overview["best_day"][1] if overview["best_day"] else 0.0
    worst_day_date = overview["worst_day"][0] if overview["worst_day"] else None
    worst_day_val = overview["worst_day"][1] if overview["worst_day"] else 0.0

    c7.metric(t("dash.kpi.trades"), f"{total_trades}")
    c8.metric(t("dash.kpi.total_lots"), f"{overview['total_lots']:,}")
    c9.metric(t("dash.kpi.avg_duration"), fmt_duration(overview["avg_trade_duration_sec"]))
    c10.metric(t("dash.kpi.avg_win_duration"), fmt_duration(overview["avg_win_duration_sec"]))
    c11.metric(t("dash.kpi.best_day"), fmt_money(best_day_val), f"{best_day_date}" if best_day_date else "")
    c12.metric(t("dash.kpi.worst_day"), fmt_money(worst_day_val), f"{worst_day_date}" if worst_day_date else "")

    # --- Best/Worst Trade individual + Trade Direction -----------------------
    bt = overview["best_trade"]
    wt = overview["worst_trade"]

    def _trade_card(title: str, trade: dict | None, accent: str) -> str:
        if not trade:
            return f"<div class='segment-box'><h4 style='color:{accent}'>{title}</h4><p>—</p></div>"
        entered = pd.to_datetime(trade["entered_at"]).tz_convert("America/Sao_Paulo")
        return f"""
        <div class="segment-box">
            <h4 style="color:{accent}">{title}</h4>
            <div class="segment-row"><span>{t('dash.card.pnl_net')}</span>
                <span class="{color_class(trade['pnl_net'])}">{fmt_money(trade['pnl_net'])}</span></div>
            <div class="segment-row"><span>{t('dash.card.contract')}</span><span>{trade['contract_name']} · {trade['type']}</span></div>
            <div class="segment-row"><span>{t('dash.card.qty')}</span><span>{trade['size']}</span></div>
            <div class="segment-row"><span>{t('dash.card.entry_at')}</span><span>{trade['entry_price']:,.2f}</span></div>
            <div class="segment-row"><span>{t('dash.card.exit_at')}</span><span>{trade['exit_price']:,.2f}</span></div>
            <div class="segment-row"><span>{t('dash.card.date')}</span><span>{entered.strftime('%d/%m %H:%M')}</span></div>
        </div>
        """

    bt_col, wt_col, dir_col = st.columns([1, 1, 1])
    bt_col.markdown(_trade_card(t("dash.best_trade"), bt, GREEN), unsafe_allow_html=True)
    wt_col.markdown(_trade_card(t("dash.worst_trade"), wt, RED), unsafe_allow_html=True)
    with dir_col:
        if total_trades:
            fig = go.Figure(
                go.Pie(
                    labels=["Long", "Short"],
                    values=[overview["long_count"], overview["short_count"]],
                    hole=0.6,
                    marker=dict(colors=[GREEN, RED]),
                    textinfo="label+percent",
                    hovertemplate="<b>%{label}</b><br>Trades: %{value}<extra></extra>",
                )
            )
            fig.update_layout(
                **PLOTLY_LAYOUT, height=220,
                title=t("dash.direction_title"), showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    # --- KPIs em pontos ------------------------------------------------------
    st.subheader(t("dash.kpis_pts"))
    p1, p2, p3, p4, p5, p6 = st.columns(6)
    p1.metric(t("dash.kpi.net_points"), fmt_pts(pts_kpis["total_net_points"]))
    p2.metric(t("dash.kpi.avg_win_pts"), f"{pts_kpis['avg_winning_trade_points']:.2f}")
    p3.metric(t("dash.kpi.avg_loss_pts"), f"{pts_kpis['avg_losing_trade_points']:.2f}")
    p4.metric(t("dash.kpi.rr_average"), f"{pts_kpis['rr_average']:.2f}")
    p5.metric(t("dash.kpi.rr_aggregate"), f"{pts_kpis['rr_aggregate']:.2f}")
    p6.metric(
        t("dash.kpi.operations"),
        f"{pts_kpis['total_grouped_operations']}",
        t("dash.kpi.operations_delta", wr=fmt_pct(pts_kpis['win_rate_grouped'])),
    )

    st.divider()

    # --- Equity curves ($ e pontos) -----------------------------------------
    st.subheader(t("dash.equity_curve"))
    eq = df_with_groups.sort_values("entered_at").copy()
    eq["cum_pnl"] = eq["pnl_net"].cumsum()
    eq["cum_pts"] = eq["points"].cumsum()

    col_eq1, col_eq2 = st.columns(2)
    with col_eq1:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=eq["entered_at"], y=eq["cum_pnl"], mode="lines",
                line=dict(color=GREEN, width=2),
                fill="tozeroy", fillcolor="rgba(34,255,136,0.12)",
                hovertemplate="<b>%{x}</b><br>PnL acum: $%{y:,.2f}<extra></extra>",
            )
        )
        fig.update_layout(**PLOTLY_LAYOUT, height=300, title=t("dash.cum_pnl_usd"))
        st.plotly_chart(fig, use_container_width=True)

    with col_eq2:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=eq["entered_at"], y=eq["cum_pts"], mode="lines",
                line=dict(color=BLUE, width=2),
                fill="tozeroy", fillcolor="rgba(101,181,255,0.12)",
                hovertemplate="<b>%{x}</b><br>Pontos acum: %{y:,.2f}<extra></extra>",
            )
        )
        fig.update_layout(**PLOTLY_LAYOUT, height=300, title=t("dash.cum_points"))
        st.plotly_chart(fig, use_container_width=True)

    # --- Daily charts (TopStepX style) --------------------------------------
    col_d1, col_d2 = st.columns(2)

    with col_d1:
        st.subheader(t("dash.daily_points"))
        st.caption(t("dash.daily_points_hint"))
        if not daily.empty:
            fig = go.Figure()
            fig.add_trace(
                go.Bar(
                    x=daily["trade_day"], y=daily["winning_points"],
                    name="Winning Pts", marker_color=GREEN,
                    hovertemplate="<b>%{x}</b><br>Win: %{y:,.2f}<extra></extra>",
                )
            )
            fig.add_trace(
                go.Bar(
                    x=daily["trade_day"], y=daily["losing_points"],
                    name="Losing Pts", marker_color=RED,
                    hovertemplate="<b>%{x}</b><br>Loss: %{y:,.2f}<extra></extra>",
                )
            )
            fig.update_layout(**PLOTLY_LAYOUT, height=320, barmode="relative",
                              legend=dict(orientation="h", y=1.1))
            event = st.plotly_chart(
                fig, use_container_width=True,
                key="chart_daily_points",
                on_select="rerun",
                selection_mode=("points", "box"),
            )
            _apply_day_selection_from_event(event)

    with col_d2:
        st.subheader(t("dash.daily_size"))
        if not daily.empty:
            fig = go.Figure()
            fig.add_trace(
                go.Bar(
                    x=daily["trade_day"], y=daily["winning_size"],
                    name="Winning Size", marker_color=GREEN,
                    hovertemplate="<b>%{x}</b><br>Win size: %{y}<extra></extra>",
                )
            )
            fig.add_trace(
                go.Bar(
                    x=daily["trade_day"], y=daily["losing_size"],
                    name="Losing Size", marker_color=RED,
                    hovertemplate="<b>%{x}</b><br>Loss size: %{y}<extra></extra>",
                )
            )
            fig.update_layout(**PLOTLY_LAYOUT, height=320, barmode="stack",
                              legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)

    # --- Bar charts por dimensão --------------------------------------------
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader(t("dash.pnl_by_weekday"))
        wd = df.groupby("weekday", as_index=False)["pnl_net"].sum()
        wd["order"] = wd["weekday"].map({d: i for i, d in enumerate(
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        )})
        wd = wd.sort_values("order")
        fig = go.Figure(
            go.Bar(
                x=wd["weekday"], y=wd["pnl_net"],
                marker_color=[GREEN if v >= 0 else RED for v in wd["pnl_net"]],
                hovertemplate="<b>%{x}</b><br>PnL: $%{y:,.2f}<extra></extra>",
            )
        )
        fig.update_layout(**PLOTLY_LAYOUT, height=300)
        st.plotly_chart(fig, use_container_width=True)

    with col_b:
        st.subheader(t("dash.pnl_by_hour"))
        hr = df.groupby("entry_hour", as_index=False)["pnl_net"].sum()
        fig = go.Figure(
            go.Bar(
                x=hr["entry_hour"], y=hr["pnl_net"],
                marker_color=[GREEN if v >= 0 else RED for v in hr["pnl_net"]],
                hovertemplate="<b>%{x}h</b><br>PnL: $%{y:,.2f}<extra></extra>",
            )
        )
        fig.update_layout(**PLOTLY_LAYOUT, height=300, xaxis_title=t("dash.axis_hour"))
        st.plotly_chart(fig, use_container_width=True)

    # --- PnL por contrato + heatmap calendário ------------------------------
    col_c, col_d = st.columns([1, 2])

    with col_c:
        st.subheader(t("dash.pnl_by_contract"))
        ct = df.groupby("contract_name", as_index=False)["pnl_net"].sum().sort_values("pnl_net")
        fig = go.Figure(
            go.Bar(
                y=ct["contract_name"], x=ct["pnl_net"], orientation="h",
                marker_color=[GREEN if v >= 0 else RED for v in ct["pnl_net"]],
                hovertemplate="<b>%{y}</b><br>PnL: $%{x:,.2f}<extra></extra>",
            )
        )
        fig.update_layout(**PLOTLY_LAYOUT, height=340)
        st.plotly_chart(fig, use_container_width=True)

    with col_d:
        st.subheader(t("dash.calendar"))
        st.caption(t("dash.calendar_hint"))
        cal = daily_pnl.copy()
        if not cal.empty:
            cal["trade_day"] = pd.to_datetime(cal["trade_day"])
            full_range = pd.date_range(cal["trade_day"].min(), cal["trade_day"].max(), freq="D")
            cal = cal.set_index("trade_day").reindex(full_range).rename_axis("trade_day").reset_index()
            cal["week"] = cal["trade_day"].dt.strftime("%Y-W%V")
            cal["dow_idx"] = cal["trade_day"].dt.weekday
            # Totais por semana — usado como rótulo do eixo Y do heatmap.
            trades_per_day = (
                df.groupby("trade_day", as_index=False).size().rename(columns={"size": "n_trades"})
            )
            trades_per_day["trade_day"] = pd.to_datetime(trades_per_day["trade_day"])
            cal = cal.merge(trades_per_day, on="trade_day", how="left")
            cal["n_trades"] = cal["n_trades"].fillna(0).astype(int)
            week_totals = cal.groupby("week").agg(
                pnl=("pnl_net", "sum"), trades=("n_trades", "sum"),
            ).reset_index()
            week_totals["label"] = week_totals.apply(
                lambda r: f"{r['week']}<br>${r['pnl']:,.0f}<br>{int(r['trades'])} trades",
                axis=1,
            )
            week_label_map = dict(zip(week_totals["week"], week_totals["label"]))
            pivot = cal.pivot_table(
                index="week", columns="dow_idx", values="pnl_net", aggfunc="sum"
            ).reindex(columns=[0, 1, 2, 3, 4, 5, 6])
            week_labels = [week_label_map.get(w, w) for w in pivot.index]
            text_pivot = cal.assign(
                label=cal.apply(
                    lambda r: f"{r['trade_day'].strftime('%d/%m')}<br>${r['pnl_net']:,.0f}"
                    if pd.notna(r["pnl_net"])
                    else r["trade_day"].strftime("%d/%m"),
                    axis=1,
                )
            ).pivot_table(
                index="week", columns="dow_idx", values="label", aggfunc="first"
            ).reindex(columns=[0, 1, 2, 3, 4, 5, 6])
            # customdata mantém a data ISO de cada célula para recuperar via on_select.
            date_pivot = cal.assign(
                iso=cal["trade_day"].dt.strftime("%Y-%m-%d"),
            ).pivot_table(
                index="week", columns="dow_idx", values="iso", aggfunc="first"
            ).reindex(columns=[0, 1, 2, 3, 4, 5, 6])

            vmax = max(abs(daily_pnl["pnl_net"].min()), abs(daily_pnl["pnl_net"].max())) or 1.0
            fig = go.Figure(
                go.Heatmap(
                    z=pivot.values,
                    x=[
                        t("weekday.short.mon"), t("weekday.short.tue"), t("weekday.short.wed"),
                        t("weekday.short.thu"), t("weekday.short.fri"), t("weekday.short.sat"),
                        t("weekday.short.sun"),
                    ],
                    y=week_labels,
                    customdata=date_pivot.values,
                    colorscale=[[0.0, RED], [0.5, "#1a1d24"], [1.0, GREEN]],
                    zmin=-vmax, zmax=vmax,
                    text=text_pivot.values, texttemplate="%{text}",
                    textfont={"size": 10, "color": TEXT},
                    hovertemplate="%{text}<extra></extra>",
                    showscale=False,
                )
            )
            fig.update_layout(**PLOTLY_LAYOUT, height=340)
            fig.update_yaxes(tickfont=dict(size=10))
            event = st.plotly_chart(
                fig, use_container_width=True,
                key="chart_calendar",
                on_select="rerun",
                selection_mode=("points", "box"),
            )
            _apply_day_selection_from_event(event)

    st.divider()

    # --- Net Daily P&L ($) + Daily Cumulative ($) ---------------------------
    st.subheader(t("dash.daily_pnl_usd"))
    col_n1, col_n2 = st.columns(2)
    if not daily_pnl.empty:
        cum = daily_pnl.copy()
        cum["cum"] = cum["pnl_net"].cumsum()
        with col_n1:
            fig = go.Figure(
                go.Scatter(
                    x=cum["trade_day"], y=cum["cum"], mode="lines+markers",
                    line=dict(color=GREEN, width=2),
                    fill="tozeroy", fillcolor="rgba(127,199,164,0.12)",
                    hovertemplate="<b>%{x}</b><br>Cumulative: $%{y:,.2f}<extra></extra>",
                )
            )
            fig.update_layout(**PLOTLY_LAYOUT, height=300, title=t("dash.daily_cum_title"))
            st.plotly_chart(fig, use_container_width=True)
        with col_n2:
            fig = go.Figure(
                go.Bar(
                    x=daily_pnl["trade_day"], y=daily_pnl["pnl_net"],
                    marker_color=[GREEN if v >= 0 else RED for v in daily_pnl["pnl_net"]],
                    hovertemplate="<b>%{x}</b><br>PnL: $%{y:,.2f}<extra></extra>",
                )
            )
            fig.update_layout(**PLOTLY_LAYOUT, height=300, title=t("dash.daily_net_title"))
            st.plotly_chart(fig, use_container_width=True)

    # --- Trade Duration Analysis + Win Rate Analysis -------------------------
    duration_buckets = metrics.compute_duration_buckets(df)
    if not duration_buckets.empty and duration_buckets["trades"].sum() > 0:
        col_du1, col_du2 = st.columns(2)
        with col_du1:
            st.subheader(t("dash.duration_analysis"))
            fig = go.Figure(
                go.Bar(
                    x=duration_buckets["trades"], y=duration_buckets["bucket"],
                    orientation="h", marker_color=MUTED,
                    text=duration_buckets["trades"], textposition="outside",
                    hovertemplate="<b>%{y}</b><br>Trades: %{x}<extra></extra>",
                )
            )
            fig.update_layout(**PLOTLY_LAYOUT, height=380)
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(fig, use_container_width=True)
        with col_du2:
            st.subheader(t("dash.win_rate_analysis"))
            wr = duration_buckets.copy()
            # Mostra apenas buckets com pelo menos 1 trade para evitar barras
            # falsas de 0% que confundem leitura.
            wr["win_rate_pct"] = wr["win_rate"] * 100
            fig = go.Figure(
                go.Bar(
                    x=wr["win_rate_pct"], y=wr["bucket"], orientation="h",
                    marker_color=[GREEN if t > 0 else GREY for t in wr["trades"]],
                    text=[f"{v:.0f}%" if t > 0 else ""
                          for v, t in zip(wr["win_rate_pct"], wr["trades"])],
                    textposition="outside",
                    hovertemplate="<b>%{y}</b><br>Win rate: %{x:.0f}%<extra></extra>",
                )
            )
            fig.update_layout(**PLOTLY_LAYOUT, height=380)
            fig.update_xaxes(range=[0, 110], ticksuffix="%")
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # --- Análise de Adições (expander) --------------------------------------
    with st.expander(t("dash.additions_expander"), expanded=False):
        st.caption(t("dash.additions_caption"))

        def render_segment(title: str, seg: dict, accent: str = TEXT) -> str:
            pnl_cls = color_class(seg["total_pnl"])
            pts_cls = color_class(seg["total_points"])
            return f"""
            <div class="segment-box">
                <h4 style="color:{accent}">{title}</h4>
                <div class="segment-row"><span>{t('dash.seg.count')}</span><span>{seg['count']}</span></div>
                <div class="segment-row"><span>{t('dash.seg.total_pts')}</span>
                    <span class="{pts_cls}">{seg['total_points']:+,.2f}</span></div>
                <div class="segment-row"><span>{t('dash.seg.avg_pts')}</span>
                    <span class="{pts_cls}">{seg['avg_points']:+,.2f}</span></div>
                <div class="segment-row"><span>{t('dash.seg.total_pnl')}</span>
                    <span class="{pnl_cls}">$ {seg['total_pnl']:,.2f}</span></div>
                <div class="segment-row"><span>{t('dash.seg.avg_pnl')}</span>
                    <span class="{pnl_cls}">$ {seg['avg_pnl']:,.2f}</span></div>
                <div class="segment-row"><span>{t('dash.seg.win_rate')}</span>
                    <span>{seg['win_rate_by_group'] * 100:.1f}%</span></div>
                <div class="segment-row"><span>{t('dash.seg.avg_additions')}</span>
                    <span>{seg['avg_additions']:.2f}</span></div>
                <div class="segment-row"><span>{t('dash.seg.total_size')}</span>
                    <span>{int(seg['total_size'])}</span></div>
            </div>
            """

        s1, s2, s3, s4 = st.columns(4)
        s1.markdown(render_segment(t("dash.seg.no_additions"), segments["no_additions"], MUTED), unsafe_allow_html=True)
        s2.markdown(render_segment(t("dash.seg.with_additions"), segments["with_additions"], BLUE), unsafe_allow_html=True)
        s3.markdown(render_segment(t("dash.seg.with_winners"), segments["with_additions_winners"], GREEN), unsafe_allow_html=True)
        s4.markdown(render_segment(t("dash.seg.with_losers"), segments["with_additions_losers"], RED), unsafe_allow_html=True)

        st.markdown("&nbsp;")
        st.markdown(t("dash.groups_title"))
        if not groups.empty:
            g_show = groups.sort_values("group_start", ascending=False)[
                [
                    "group_id", "contract_name", "type", "group_start", "group_end",
                    "trade_count", "additions_count", "total_points", "total_pnl",
                    "total_net_pnl", "duration_min", "points_status",
                ]
            ].rename(
                columns={
                    "group_id": t("dash.groups.col.group"),
                    "contract_name": t("dash.groups.col.contract"),
                    "type": t("dash.groups.col.type"),
                    "group_start": t("dash.groups.col.start"),
                    "group_end": t("dash.groups.col.end"),
                    "trade_count": t("dash.groups.col.trades"),
                    "additions_count": t("dash.groups.col.additions"),
                    "total_points": t("dash.groups.col.total_pts"),
                    "total_pnl": t("dash.groups.col.pnl_gross"),
                    "total_net_pnl": t("dash.groups.col.pnl_net"),
                    "duration_min": t("dash.groups.col.duration_min"),
                    "points_status": t("dash.groups.col.status"),
                }
            )
            st.dataframe(g_show, use_container_width=True, hide_index=True, height=280)

    st.divider()

    # --- Tabela de trades ---------------------------------------------------
    st.subheader(t("dash.trades_title", n=len(df)))
    show = df_with_groups.sort_values("entered_at", ascending=False)[
        [
            "id", "trade_day", "contract_name", "type", "size",
            "entry_price", "exit_price", "points", "pnl", "fees",
            "commissions", "pnl_net", "trade_duration", "group_id",
        ]
    ].rename(
        columns={
            "id": t("dash.trades.col.id"),
            "trade_day": t("dash.trades.col.day"),
            "contract_name": t("dash.trades.col.contract"),
            "type": t("dash.trades.col.type"),
            "size": t("dash.trades.col.qty"),
            "entry_price": t("dash.trades.col.entry"),
            "exit_price": t("dash.trades.col.exit"),
            "points": t("dash.trades.col.points"),
            "pnl": t("dash.trades.col.pnl_gross"),
            "fees": t("dash.trades.col.fees"),
            "commissions": t("dash.trades.col.commissions"),
            "pnl_net": t("dash.trades.col.pnl_net"),
            "trade_duration": t("dash.trades.col.duration"),
            "group_id": t("dash.trades.col.group"),
        }
    )
    st.dataframe(show, use_container_width=True, hide_index=True, height=380)


def render_coach(
    df: pd.DataFrame,
    groups: pd.DataFrame,
    df_all: pd.DataFrame,
    filter_ctx: coach_ai.FilterContext,
) -> None:
    coach = metrics.compute_coach(df, groups)

    # --- Gerador de prompt para análise em LLM externa -------------------------
    ai_col1, ai_col2 = st.columns([1, 3])
    with ai_col1:
        gen_prompt = st.button(
            t("coach.btn_gen_prompt"),
            type="primary",
            use_container_width=True,
            help=t("coach.btn_gen_prompt_help"),
        )
    with ai_col2:
        st.caption(t("coach.copy_hint"))

    if gen_prompt:
        history = coach_ai.fetch_history(filter_ctx.contracts)
        st.session_state["coach_ai_prompt"] = coach_ai.build_prompt(
            df, groups, df_all, filter_ctx, history=history, lang=i18n.current_lang()
        )
        st.session_state["coach_ai_history_count"] = len(history)
        st.session_state["coach_prompt_collapsed"] = False

    if "coach_ai_prompt" in st.session_state:
        with st.container(border=True):
            header_col, btn_col = st.columns([4, 1])
            with header_col:
                st.markdown(t("coach.prompt_ready"))
                hist_n = st.session_state.get("coach_ai_history_count", 0)
                if hist_n:
                    st.caption(t("coach.prompt_history_n", n=hist_n))
                else:
                    st.caption(t("coach.prompt_no_history"))
            with btn_col:
                collapsed = st.session_state.get("coach_prompt_collapsed", False)
                label = t("coach.btn_expand") if collapsed else t("coach.btn_collapse")
                if st.button(label, key="toggle_prompt", use_container_width=True):
                    st.session_state["coach_prompt_collapsed"] = not collapsed
                    st.rerun()

            if not st.session_state.get("coach_prompt_collapsed", False):
                st.caption(t("coach.copy_icon_hint"))
                st.code(st.session_state["coach_ai_prompt"], language="markdown")
                with st.expander(t("coach.where_to_paste"), expanded=False):
                    st.markdown(
                        "- Gemini: https://gemini.google.com\n"
                        "- Perplexity: https://perplexity.ai\n"
                        "- ChatGPT: https://chat.openai.com\n"
                        "- Claude: https://claude.ai"
                    )

        with st.container(border=True):
            st.markdown(t("coach.paste_response_title"))
            st.caption(t("coach.paste_response_caption"))

            # Limpa o textarea ANTES de instanciar o widget (flag setada no
            # ciclo anterior, após salvar com sucesso).
            if st.session_state.pop("coach_response_clear", False):
                st.session_state["coach_response_text"] = ""

            last_saved = st.session_state.pop("coach_response_saved_msg", None)
            if last_saved:
                st.success(last_saved)

            response_text = st.text_area(
                t("coach.response_label"),
                key="coach_response_text",
                height=240,
                placeholder=t("coach.response_placeholder"),
                label_visibility="collapsed",
            )
            save_col, status_col = st.columns([1, 3])
            with save_col:
                save_clicked = st.button(
                    t("coach.btn_save"),
                    type="primary",
                    use_container_width=True,
                    disabled=not response_text.strip(),
                )
            with status_col:
                if save_clicked:
                    result = coach_ai.save_analysis(filter_ctx, response_text)
                    if result["ok"]:
                        st.session_state["coach_response_clear"] = True
                        st.session_state["coach_response_saved_msg"] = t("coach.save_ok")
                        st.rerun()
                    else:
                        st.error(t("coach.save_err", err=result['error']))

    st.divider()

    st.subheader(t("coach.summary"))
    for bullet in coach["headline"]:
        st.markdown(f"- {bullet}")

    st.divider()

    # --- Padrões comportamentais --------------------------------------------
    st.subheader(t("coach.patterns"))

    rev = coach["revenge"]
    cut = coach["cut_winners_hold_losers"]
    over = coach["overtrading"]
    streak = coach["losing_streak"]

    cc1, cc2 = st.columns(2)
    with cc1:
        rev_color = RED if rev["pnl"] < 0 else GREEN if rev["pnl"] > 0 else MUTED
        st.markdown(
            f"""
            <div class="coach-card">
                <h4 style="color:{rev_color}">{t('coach.revenge.title')}</h4>
                <p>{t('coach.revenge.desc', min=metrics.REVENGE_WINDOW_MIN)}</p>
                <p>{t('coach.revenge.line1', count=rev['count'], cls=color_class(rev['pnl']), pnl=fmt_money(rev['pnl']))}</p>
                <p>{t('coach.revenge.line2',
                    cls_rev=color_class(rev['revenge_avg_pnl']), rev=fmt_money(rev['revenge_avg_pnl']),
                    cls_base=color_class(rev['baseline_avg_pnl']), base=fmt_money(rev['baseline_avg_pnl']))}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        cut_color = RED if cut["flag"] else GREEN
        cut_flag = t("coach.cut.flag_bad") if cut["flag"] else t("coach.cut.flag_ok")
        st.markdown(
            f"""
            <div class="coach-card">
                <h4 style="color:{cut_color}">{t('coach.cut.title')}</h4>
                <p>{t('coach.cut.avg_win', dur=fmt_duration(cut['avg_win_sec']))}</p>
                <p>{t('coach.cut.avg_loss', dur=fmt_duration(cut['avg_loss_sec']))}</p>
                <p>{t('coach.cut.ratio', ratio=cut['ratio'], flag=cut_flag)}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with cc2:
        over_color = RED if over["tilt_avg_pnl"] < over["normal_avg_pnl"] else GREEN
        st.markdown(
            f"""
            <div class="coach-card">
                <h4 style="color:{over_color}">{t('coach.over.title')}</h4>
                <p>{t('coach.over.threshold', thr=over['threshold'])}</p>
                <p>{t('coach.over.tilt_days', n=over['tilt_days'])}</p>
                <p>{t('coach.over.compare',
                    cls_t=color_class(over['tilt_avg_pnl']), tilt=fmt_money(over['tilt_avg_pnl']),
                    cls_n=color_class(over['normal_avg_pnl']), norm=fmt_money(over['normal_avg_pnl']))}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        streak_text = ""
        if streak["start"] is not None:
            streak_text = t(
                "coach.streak.range",
                start=streak['start'].strftime('%d/%m %H:%M'),
                end=streak['end'].strftime('%d/%m %H:%M'),
            )
        st.markdown(
            f"""
            <div class="coach-card">
                <h4 style="color:{RED}">{t('coach.streak.title')}</h4>
                <p>{t('coach.streak.length', n=streak['length'])}</p>
                <p>{t('coach.streak.pnl', cls=color_class(streak['pnl']), pnl=fmt_money(streak['pnl']))}</p>
                {streak_text}
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()

    # --- Vazamentos vs Pontos fortes ----------------------------------------
    col_leak, col_str = st.columns(2)

    def render_combo_table(title: str, sub: pd.DataFrame, color: str) -> None:
        st.markdown(f"<h4 style='color:{color}'>{title}</h4>", unsafe_allow_html=True)
        if sub.empty:
            st.caption(t("coach.combo.empty"))
            return
        col_pnl = t("coach.combo.col.pnl")
        col_avg = t("coach.combo.col.avg_pnl")
        col_wr = t("coach.combo.col.win_rate")
        show = sub.rename(
            columns={
                "contract_name": t("coach.combo.col.contract"),
                "weekday": t("coach.combo.col.weekday"),
                "entry_hour": t("coach.combo.col.hour"),
                "trades": t("coach.combo.col.trades"),
                "pnl": col_pnl,
                "avg_pnl": col_avg,
                "win_rate": col_wr,
            }
        ).copy()
        show[col_pnl] = show[col_pnl].map(fmt_money)
        show[col_avg] = show[col_avg].map(fmt_money)
        show[col_wr] = show[col_wr].map(lambda v: f"{v*100:.0f}%")
        st.dataframe(show, use_container_width=True, hide_index=True)

    with col_leak:
        render_combo_table(t("coach.leaks.title"), coach["leaks"], RED)

    with col_str:
        render_combo_table(t("coach.strengths.title"), coach["strengths"], GREEN)

    st.divider()

    # --- Tamanho de posição --------------------------------------------------
    col_sz, col_dist = st.columns(2)

    with col_sz:
        st.subheader(t("coach.size_title"))
        sz = coach["size_buckets"]
        if sz.empty:
            st.caption(t("coach.size_empty"))
        else:
            fig = go.Figure()
            fig.add_trace(
                go.Bar(
                    x=sz["size"].astype(str), y=sz["avg_pnl"],
                    marker_color=[GREEN if v >= 0 else RED for v in sz["avg_pnl"]],
                    text=[f"n={int(n)}" for n in sz["trades"]],
                    textposition="outside",
                    hovertemplate=(
                        "<b>Size %{x}</b><br>"
                        "Avg PnL: $%{y:,.2f}<br>"
                        "Trades: %{text}<extra></extra>"
                    ),
                )
            )
            fig.update_layout(
                **PLOTLY_LAYOUT, height=320,
                xaxis_title=t("coach.size_axis_x"), yaxis_title=t("coach.size_axis_y"),
            )
            st.plotly_chart(fig, use_container_width=True)

    with col_dist:
        st.subheader(t("coach.dist_title"))
        dist = coach["points_distribution"]
        if not dist["values"]:
            st.caption(t("coach.dist_empty"))
        else:
            fig = go.Figure()
            fig.add_trace(
                go.Histogram(
                    x=dist["values"], nbinsx=40,
                    marker=dict(color=BLUE, line=dict(color=GREY, width=0.5)),
                    hovertemplate="Pts: %{x}<br>Trades: %{y}<extra></extra>",
                )
            )
            fig.add_vline(x=dist["mean"], line_color=GREEN, line_dash="dash",
                          annotation_text=t("coach.dist_mean", v=dist['mean']),
                          annotation_position="top right")
            fig.add_vline(x=dist["median"], line_color=RED, line_dash="dot",
                          annotation_text=t("coach.dist_median", v=dist['median']),
                          annotation_position="top left")
            fig.update_layout(
                **PLOTLY_LAYOUT, height=320,
                xaxis_title=t("coach.dist_axis_x"), yaxis_title=t("coach.dist_axis_y"),
            )
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # --- Checklist acionável -------------------------------------------------
    st.subheader(t("coach.checklist"))
    if not coach["checklist"]:
        st.caption(t("coach.checklist_empty"))
    else:
        for item in coach["checklist"]:
            st.markdown(f"<div class='coach-check'>• {item}</div>", unsafe_allow_html=True)


# ----------------------------- Plano de Ação ---------------------------------


@st.cache_data(ttl=30)
def _load_action_items() -> pd.DataFrame:
    return action_plan.list_items()


def render_action_plan() -> None:
    st.subheader(t("plan.title"))
    st.caption(t("plan.caption"))

    try:
        original = _load_action_items()
    except Exception as e:
        msg = str(e)
        if "action_items" in msg or "does not exist" in msg.lower():
            st.error(t("plan.err_table_missing"))
        else:
            st.error(t("plan.err_load", msg=msg))
        return

    # KPIs ---------------------------------------------------------------
    if not original.empty:
        pend = int((original["status"] == "Pendente").sum())
        anda = int((original["status"] == "Em andamento").sum())
        conc = int((original["status"] == "Concluído").sum())
    else:
        pend = anda = conc = 0
    k1, k2, k3 = st.columns(3)
    k1.metric(t("plan.kpi.pending"), pend)
    k2.metric(t("plan.kpi.in_progress"), anda)
    k3.metric(t("plan.kpi.done"), conc)

    # Editor -------------------------------------------------------------
    # Snapshot original em session_state para o diff no save.
    st.session_state["_action_plan_original"] = original.copy()

    # Mapeia canônico PT (banco) → label traduzido (UI). O original fica
    # intacto para o diff/upsert; a cópia exibida usa labels traduzidos.
    display = original.copy()
    if not display.empty:
        display["status"] = display["status"].map(
            lambda v: i18n.status_label(v) if pd.notna(v) else v
        )
        display["priority"] = display["priority"].map(
            lambda v: i18n.priority_label(v) if pd.notna(v) else v
        )

    edited = st.data_editor(
        display,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_order=action_plan.EDITABLE_COLUMNS,
        column_config={
            "task": st.column_config.TextColumn(
                t("plan.col.task"), required=True, width="large",
            ),
            "priority": st.column_config.SelectboxColumn(
                t("plan.col.priority"),
                options=i18n.priority_options(),
                default=i18n.priority_label("Média"), required=True, width="small",
            ),
            "status": st.column_config.SelectboxColumn(
                t("plan.col.status"),
                options=i18n.status_options(),
                default=i18n.status_label("Pendente"), required=True, width="small",
            ),
            "due_date": st.column_config.DateColumn(
                t("plan.col.due_date"), format="DD/MM/YYYY", width="small",
            ),
            "done": st.column_config.CheckboxColumn(
                t("plan.col.done"), default=False, width="small",
            ),
            # Mantém id internamente para o diff, mas oculto via column_order.
            "id": None, "created_at": None, "updated_at": None,
        },
        key="action_plan_editor",
    )

    col_save, col_reload, _ = st.columns([1, 1, 4])
    save_clicked = col_save.button(t("plan.btn_save"), type="primary", use_container_width=True)
    reload_clicked = col_reload.button(t("plan.btn_reload"), use_container_width=True)

    if reload_clicked:
        _load_action_items.clear()
        st.rerun()

    if save_clicked:
        # Converte labels traduzidos de volta para os valores canônicos do
        # banco antes do diff/upsert. action_plan._normalize_row é tolerante
        # a valores fora do conjunto, então essa conversão é o que mantém o
        # CHECK constraint feliz.
        edited_for_save = edited.copy()
        if not edited_for_save.empty:
            edited_for_save["status"] = edited_for_save["status"].map(
                lambda v: i18n.status_from_label(v) if isinstance(v, str) else v
            )
            edited_for_save["priority"] = edited_for_save["priority"].map(
                lambda v: i18n.priority_from_label(v) if isinstance(v, str) else v
            )
        with st.spinner(t("plan.saving")):
            result = action_plan.upsert_items(original, edited_for_save)
        if result["ok"]:
            st.success(
                t("plan.save_ok",
                  ins=result['inserted'], upd=result['updated'], dele=result['deleted'])
            )
            _load_action_items.clear()
            st.rerun()
        else:
            st.error(t("plan.save_err", err=result['error']))


# ----------------------------- Importar CSVs --------------------------------


def render_import(user_id: str) -> None:
    st.subheader(t("import.subheader"))
    st.caption(t("import.caption"))

    # A key do uploader rotaciona via contador para "esvaziar" a caixa após
    # uma importação. O Streamlit não permite escrever em
    # st.session_state["csv_uploader"] depois do widget instanciado, então o
    # único jeito de resetar é forçar um widget novo (key diferente).
    uploader_seq = st.session_state.get("csv_uploader_seq", 0)
    uploader_key = f"csv_uploader_{uploader_seq}"

    # Resultado da importação anterior (setado no rerun após sucesso).
    last_result = st.session_state.pop("csv_import_last_result", None)
    if last_result:
        st.success(t("import.done", n=last_result["total"]))
        st.dataframe(
            pd.DataFrame(last_result["rows_log"]),
            use_container_width=True,
            hide_index=True,
        )

    files = st.file_uploader(
        t("import.uploader"),
        type="csv",
        accept_multiple_files=True,
        key=uploader_key,
    )
    col_btn, _ = st.columns([1, 3])
    if files and col_btn.button(t("import.btn"), type="primary", use_container_width=True):
        client = auth.get_client()
        total = 0
        rows_log: list[dict] = []
        with st.spinner(t("import.processing", n=len(files))):
            for f in files:
                n, fmt, err = ingest_core.ingest_uploaded_csv(f, client, user_id)
                total += n
                rows_log.append(
                    {
                        t("import.col.file"): f.name,
                        t("import.col.format"): fmt,
                        t("import.col.rows"): n,
                        t("import.col.status"): err or "ok",
                    }
                )
        # Invalida o cache para o próximo load_trades pegar os novos trades.
        load_trades.clear()
        # Rotaciona a key do uploader e dispara rerun: a caixa volta vazia
        # e o resumo aparece no topo via csv_import_last_result.
        st.session_state["csv_import_last_result"] = {"total": total, "rows_log": rows_log}
        st.session_state["csv_uploader_seq"] = uploader_seq + 1
        st.rerun()


# ----------------------------- App -------------------------------------------

# Sidebar superior: seletor de idioma + identidade do usuário + sair.
# O language_selector é renderizado ANTES dos demais widgets para que uma
# troca de idioma já reaplique em todo o resto do mesmo rerun.
with st.sidebar:
    i18n.language_selector()
    st.markdown(f"👤 **{_user.get('email') or _user['id']}**")
    if st.button(t("auth.sign_out"), use_container_width=True, key="btn_sign_out"):
        auth.sign_out()
    st.divider()

df_all = load_trades(_user["id"])

st.title(t("app.title"))
st.caption(t("app.caption_logged", email=_user.get('email') or _user['id']))

# Sem trades ainda: pula filtros e mostra só a aba de upload.
if df_all.empty:
    st.warning(t("app.empty_no_trades"))
    render_import(_user["id"])
    st.stop()

# --- Sidebar: filtros (cross-filter) -----------------------------------------

with st.sidebar:
    st.header(t("sidebar.filters"))

    min_d, max_d = df_all["trade_day"].min(), df_all["trade_day"].max()

    # Atalhos de período — keys internas estáveis (independem do idioma);
    # exibição via format_func.
    today_brt = pd.Timestamp.now(tz="America/Sao_Paulo").date()
    shortcut = st.radio(
        t("sidebar.period_shortcuts"),
        options=i18n.SHORTCUT_KEYS,
        index=0,
        horizontal=False,
        key="date_shortcut",
        format_func=i18n.shortcut_label,
    )

    def _clamp(d: date) -> date:
        return max(min_d, min(max_d, d))

    preset_range: tuple[date, date] | None = None
    if shortcut == "today":
        preset_range = (_clamp(today_brt), _clamp(today_brt))
    elif shortcut == "last_7":
        preset_range = (_clamp(today_brt - timedelta(days=6)), _clamp(today_brt))
    elif shortcut == "current_week":
        # Semana = segunda a domingo da semana corrente.
        monday = today_brt - timedelta(days=today_brt.weekday())
        sunday = monday + timedelta(days=6)
        preset_range = (_clamp(monday), _clamp(sunday))
    elif shortcut == "last_30":
        preset_range = (_clamp(today_brt - timedelta(days=29)), _clamp(today_brt))
    elif shortcut == "current_month":
        first = today_brt.replace(day=1)
        # Último dia do mês = primeiro do próximo mês - 1.
        if first.month == 12:
            next_first = first.replace(year=first.year + 1, month=1)
        else:
            next_first = first.replace(month=first.month + 1)
        last = next_first - timedelta(days=1)
        preset_range = (_clamp(first), _clamp(last))
    elif shortcut == "all":
        preset_range = (min_d, max_d)

    default_range = preset_range if preset_range is not None else (min_d, max_d)
    # `key` muda junto com o atalho para forçar o date_input a reler o `value`.
    date_range = st.date_input(
        t("sidebar.period_label"),
        value=default_range,
        min_value=min_d,
        max_value=max_d,
        key=f"date_range_{shortcut}",
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_d, end_d = date_range
    else:
        start_d = end_d = date_range if isinstance(date_range, date) else min_d

    contracts = sorted(df_all["contract_name"].unique())
    sel_contracts = st.multiselect(t("sidebar.contract"), contracts, default=contracts)

    types = sorted(df_all["type"].unique())
    sel_types = st.multiselect(t("sidebar.type"), types, default=types)

    # weekdays mantém os nomes em inglês (o que vem do pandas) como valores
    # canônicos; exibe traduzido via format_func.
    sel_weekdays = st.multiselect(
        t("sidebar.weekday"),
        i18n.WEEKDAY_PANDAS,
        default=i18n.WEEKDAY_PANDAS,
        format_func=i18n.weekday_label,
    )

    # Dias específicos.
    # Estado canônico em st.session_state["selected_days"] — escrito por
    # _apply_day_selection_from_event (após os widgets serem renderizados) e
    # pelo on_change do multiselect abaixo.
    # O multiselect tem key própria ("selected_days_widget") porque o Streamlit
    # proíbe escrever em st.session_state[<widget_key>] depois do widget ser
    # instanciado. Sincronizamos via default + on_change.
    available_days = sorted(
        d for d in df_all["trade_day"].unique() if start_d <= d <= end_d
    )
    canonical = [
        d for d in st.session_state.get("selected_days", []) if d in available_days
    ]
    st.session_state["selected_days"] = canonical

    def _sync_selected_days() -> None:
        st.session_state["selected_days"] = list(
            st.session_state.get("selected_days_widget", [])
        )

    sel_days = st.multiselect(
        t("sidebar.days_specific"),
        options=available_days,
        default=canonical,
        format_func=lambda d: d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d),
        key="selected_days_widget",
        on_change=_sync_selected_days,
        help=t("sidebar.days_help"),
    )
    if sel_days and st.button(t("sidebar.clear_days"), use_container_width=True):
        st.session_state["selected_days"] = []
        # Remover a key do widget força o multiselect a reler o `default` no
        # próximo run (caso contrário ele preserva o valor anterior).
        st.session_state.pop("selected_days_widget", None)
        st.rerun()

    result_filter = st.radio(
        t("sidebar.result"),
        options=i18n.RESULT_KEYS,
        index=0,
        horizontal=False,
        format_func=i18n.result_label,
    )

    st.divider()
    if st.button(t("sidebar.reload")):
        st.cache_data.clear()
        st.rerun()

# --- Aplica filtros ----------------------------------------------------------

df = df_all[
    (df_all["trade_day"] >= start_d)
    & (df_all["trade_day"] <= end_d)
    & (df_all["contract_name"].isin(sel_contracts))
    & (df_all["type"].isin(sel_types))
    & (df_all["weekday"].isin(sel_weekdays))
].copy()
selected_days = st.session_state.get("selected_days", [])
if selected_days:
    df = df[df["trade_day"].isin(selected_days)]
if result_filter == "winners":
    df = df[df["pnl_net"] > 0]
elif result_filter == "losers":
    df = df[df["pnl_net"] <= 0]

if df.empty:
    st.warning(t("app.empty_filtered"))
    st.stop()

# --- Derivações: grupos, KPIs em pts, segmentos, daily -----------------------

df_with_groups, groups = metrics.compute_groups(df)
pts_kpis = metrics.compute_kpis(df_with_groups, groups)
segments = metrics.compute_segments(groups)
daily = metrics.compute_daily(df_with_groups)
overview = metrics.compute_overview(df_with_groups)

# --- Abas --------------------------------------------------------------------

tab_dash, tab_coach, tab_plan, tab_import = st.tabs(
    [t("tab.dashboard"), t("tab.coach"), t("tab.plan"), t("tab.import")]
)

with tab_dash:
    render_dashboard(df, df_with_groups, groups, pts_kpis, segments, daily, overview)

with tab_coach:
    filter_ctx = coach_ai.FilterContext(
        start=start_d,
        end=end_d,
        contracts=list(sel_contracts),
        types=list(sel_types),
        weekdays=list(sel_weekdays),
        result_filter=result_filter,
    )
    render_coach(df_with_groups, groups, df_all, filter_ctx)

with tab_plan:
    render_action_plan()

with tab_import:
    render_import(_user["id"])

# --- Rodapé ------------------------------------------------------------------

st.markdown(
    f"<div style='text-align:center; color:{MUTED}; font-size:0.8rem; "
    f"margin-top:2rem; padding-top:1rem; border-top:1px solid {GREY};'>"
    f"{t('app.footer')}</div>",
    unsafe_allow_html=True,
)
