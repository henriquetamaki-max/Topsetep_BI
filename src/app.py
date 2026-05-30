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

import account
import action_plan
import auth
import billing
import coach_ai
import daily_plan
import i18n
import ingest_core
import live as live_tab
import metrics
import risk_engine
import risk_plan
import risk_settings
import settings as user_settings
import timezones
from i18n import t

ROOT = Path(__file__).resolve().parent.parent

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
    /* Streamlit marca elementos como data-stale=true durante reruns e aplica
       opacidade reduzida. Em paginas com st_autorefresh frequente (aba Live,
       3s) o estado stale e' quase perpetuo e tudo parece desabilitado. */
    [data-stale="true"],
    [data-testid="stElementContainer"][data-stale="true"] {
        opacity: 1 !important;
    }
    [data-testid="stMetricValue"],
    [data-testid="stMetricValue"] > div {
        font-size: var(--fs-metric) !important;
        font-weight: 700 !important;
        color: #ffffff !important;
    }
    [data-testid="stMetricLabel"],
    [data-testid="stMetricLabel"] > div,
    [data-testid="stMetricLabel"] p {
        color: #c8ccd4 !important;
        font-weight: 500 !important;
    }
    h1, h2, h3, h4, h5, h6,
    [data-testid="stMarkdownContainer"] h1,
    [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3,
    [data-testid="stMarkdownContainer"] h4 {
        color: #ffffff !important;
    }
    /* Caption / texto small fica visivel sem virar branco puro */
    [data-testid="stCaptionContainer"],
    .stCaption, small { color: #b8bcc4 !important; }
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

# Garante que o usuário tem uma row em `subscriptions` (safety net caso a
# trigger não tenha rodado) e resolve o plano efetivo para gating posterior.
billing.ensure_subscription(auth.get_client())
_plan = billing.get_effective_plan(_user["id"])


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
    df["entry_hour"] = df["entered_at"].dt.tz_convert(timezones.user_tz()).dt.hour
    # trade_day_et: trade_day derivado em fuso primario (ET). KPIs e graficos
    # do Dashboard usam esta coluna; o filtro da sidebar continua em trade_day
    # original (vindo do CSV TopStepX, em CT) para preservar UX dos atalhos.
    df["trade_day_et"] = (
        df["entered_at"].dt.tz_convert(timezones.PRIMARY_TZ).dt.date
    )
    df["weekday"] = pd.to_datetime(df["trade_day_et"]).dt.day_name()
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


from app_helpers import (
    color_class,
    extract_days_from_event,
    fmt_duration,
    fmt_money,
    fmt_pct,
    fmt_pts,
)


def _apply_day_selection_from_event(event) -> None:
    """Lê pontos selecionados de um plotly_chart(on_select="rerun") e merge no
    session_state["selected_days"]. Aceita barras (x = trade_day) e heatmap
    (customdata = ISO date). Rerun se a seleção mudou de fato.
    """
    days = extract_days_from_event(event)
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
    adherence: dict,
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
        entered_dual = timezones.fmt_dual(pd.to_datetime(trade["entered_at"]))
        return f"""
        <div class="segment-box">
            <h4 style="color:{accent}">{title}</h4>
            <div class="segment-row"><span>{t('dash.card.pnl_net')}</span>
                <span class="{color_class(trade['pnl_net'])}">{fmt_money(trade['pnl_net'])}</span></div>
            <div class="segment-row"><span>{t('dash.card.contract')}</span><span>{trade['contract_name']} · {trade['type']}</span></div>
            <div class="segment-row"><span>{t('dash.card.qty')}</span><span>{trade['size']}</span></div>
            <div class="segment-row"><span>{t('dash.card.entry_at')}</span><span>{trade['entry_price']:,.2f}</span></div>
            <div class="segment-row"><span>{t('dash.card.exit_at')}</span><span>{trade['exit_price']:,.2f}</span></div>
            <div class="segment-row"><span>{t('dash.card.date')}</span><span>{entered_dual}</span></div>
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
            st.plotly_chart(fig, width="stretch")

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
        st.plotly_chart(fig, width="stretch")

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
        st.plotly_chart(fig, width="stretch")

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
                fig, width="stretch",
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
            st.plotly_chart(fig, width="stretch")

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
        st.plotly_chart(fig, width="stretch")

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
        st.plotly_chart(fig, width="stretch")

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
        st.plotly_chart(fig, width="stretch")

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
                fig, width="stretch",
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
            st.plotly_chart(fig, width="stretch")
        with col_n2:
            fig = go.Figure(
                go.Bar(
                    x=daily_pnl["trade_day"], y=daily_pnl["pnl_net"],
                    marker_color=[GREEN if v >= 0 else RED for v in daily_pnl["pnl_net"]],
                    hovertemplate="<b>%{x}</b><br>PnL: $%{y:,.2f}<extra></extra>",
                )
            )
            fig.update_layout(**PLOTLY_LAYOUT, height=300, title=t("dash.daily_net_title"))
            st.plotly_chart(fig, width="stretch")

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
            st.plotly_chart(fig, width="stretch")
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
            st.plotly_chart(fig, width="stretch")

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
            st.dataframe(g_show, width="stretch", hide_index=True, height=280)

    # --- Aderência ao plano matinal (M5 — fusão Trade_Agent) ----------------
    with st.expander(t("dash.adherence_expander"), expanded=False):
        st.caption(t("dash.adherence_caption"))

        a_total = adherence["total_groups"]
        a_compliant = adherence["compliant"]
        a_unplanned = adherence["unplanned"]
        a_size = adherence["size_exceeded"]
        a_against = adherence.get("against_plan", 0)
        a_creep = adherence.get("size_creep_day", 0)
        a_score = adherence["score_pct"]

        score_cls = (
            color_class(1.0) if a_score >= 80
            else (color_class(0.0) if a_score >= 50 else color_class(-1.0))
        )
        score_html = (
            f'<div class="segment-box">'
            f'<h4 style="color:{TEXT}">{t("dash.adherence.score")}</h4>'
            f'<div class="segment-row" style="font-size:2em">'
            f'<span class="{score_cls}">{a_score:.1f}%</span></div>'
            f'<div class="segment-row"><span>{t("dash.adherence.total_ops")}</span>'
            f'<span>{a_total}</span></div>'
            f'<div class="segment-row"><span>{t("dash.adherence.compliant")}</span>'
            f'<span class="pos">{a_compliant}</span></div>'
            f'<div class="segment-row"><span>{t("dash.adherence.unplanned")}</span>'
            f'<span class="neg">{a_unplanned}</span></div>'
            f'<div class="segment-row"><span>{t("dash.adherence.size_exceeded")}</span>'
            f'<span class="neg">{a_size}</span></div>'
            f'<div class="segment-row"><span>{t("dash.adherence.against_plan")}</span>'
            f'<span class="neg">{a_against}</span></div>'
            f'<div class="segment-row"><span>{t("dash.adherence.size_creep_day")}</span>'
            f'<span class="neg">{a_creep}</span></div>'
            f'</div>'
        )
        c_score, c_legend = st.columns([1, 2])
        c_score.markdown(score_html, unsafe_allow_html=True)
        with c_legend:
            st.markdown(t("dash.adherence.legend"))
            if a_total > 0:
                # Stacked bar horizontal mostrando a proporcao das 5 categorias.
                # Visualmente mais rapido de ler do que so os numeros do card.
                cat_data = [
                    ("compliant", a_compliant, GREEN,
                     t("dash.adherence.compliant")),
                    ("unplanned", a_unplanned, RED,
                     t("dash.adherence.unplanned")),
                    ("size_exceeded", a_size, "#f4a261",
                     t("dash.adherence.size_exceeded")),
                    ("against_plan", a_against, "#9b2226",
                     t("dash.adherence.against_plan")),
                    ("size_creep_day", a_creep, "#e9c46a",
                     t("dash.adherence.size_creep_day")),
                ]
                fig_adh = go.Figure()
                for key, count, color, label in cat_data:
                    if count <= 0:
                        continue
                    pct = (count / a_total) * 100
                    fig_adh.add_trace(go.Bar(
                        x=[count], y=[""], name=label,
                        orientation="h", marker_color=color,
                        hovertemplate=(
                            f"<b>{label}</b><br>{count} / {a_total} "
                            f"({pct:.1f}%)<extra></extra>"
                        ),
                        text=[f"{count}"] if pct >= 8 else [""],
                        textposition="inside",
                        insidetextanchor="middle",
                        textfont=dict(color="#0e1117", size=12),
                    ))
                fig_adh.update_layout(
                    paper_bgcolor=BG,
                    plot_bgcolor=BG,
                    font=dict(color=TEXT, family="Inter, system-ui, sans-serif"),
                    height=90, barmode="stack",
                    showlegend=False,
                    margin=dict(l=10, r=10, t=10, b=10),
                    xaxis=dict(visible=False),
                    yaxis=dict(visible=False),
                )
                st.plotly_chart(
                    fig_adh, width="stretch",
                    key="chart_adherence_breakdown",
                )

        violations = adherence.get("violations", pd.DataFrame())
        if violations is None or violations.empty:
            st.success(t("dash.adherence.all_clear"))
        else:
            v_show = violations.sort_values("group_start", ascending=False)[
                [
                    "violation_type", "trade_day", "contract_name", "type",
                    "total_size", "plan_max_size",
                    "group_start", "total_points", "total_net_pnl",
                ]
            ].rename(
                columns={
                    "violation_type": t("dash.adherence.col.violation"),
                    "trade_day": t("dash.adherence.col.day"),
                    "contract_name": t("dash.groups.col.contract"),
                    "type": t("dash.groups.col.type"),
                    "total_size": t("dash.adherence.col.real_size"),
                    "plan_max_size": t("dash.adherence.col.plan_size"),
                    "group_start": t("dash.groups.col.start"),
                    "total_points": t("dash.groups.col.total_pts"),
                    "total_net_pnl": t("dash.groups.col.pnl_net"),
                }
            )
            violation_label = {
                "unplanned": t("dash.adherence.label.unplanned"),
                "size_exceeded": t("dash.adherence.label.size_exceeded"),
                "against_plan": t("dash.adherence.label.against_plan"),
                "size_creep_day": t("dash.adherence.label.size_creep_day"),
            }
            v_show[t("dash.adherence.col.violation")] = (
                v_show[t("dash.adherence.col.violation")]
                .map(lambda v: violation_label.get(v, v))
            )
            st.dataframe(v_show, width="stretch", hide_index=True, height=280)

    st.divider()

    # --- Tabela de trades ---------------------------------------------------
    st.subheader(t("dash.trades_title", n=len(df)))
    trades_view = df_with_groups.sort_values("entered_at", ascending=False).copy()
    trades_view["entered_at_dual"] = trades_view["entered_at"].apply(
        lambda ts: timezones.fmt_dual(pd.to_datetime(ts))
    )
    show = trades_view[
        [
            "id", "trade_day", "entered_at_dual", "contract_name", "type", "size",
            "entry_price", "exit_price", "points", "pnl", "fees",
            "commissions", "pnl_net", "trade_duration", "group_id",
        ]
    ].rename(
        columns={
            "id": t("dash.trades.col.id"),
            "trade_day": t("dash.trades.col.day"),
            "entered_at_dual": t("dash.trades.col.entered_at"),
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
    st.dataframe(show, width="stretch", hide_index=True, height=380)


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
            width="stretch",
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
                if st.button(label, key="toggle_prompt", width="stretch"):
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
                    width="stretch",
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
        st.dataframe(show, width="stretch", hide_index=True)

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
            st.plotly_chart(fig, width="stretch")

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
            st.plotly_chart(fig, width="stretch")

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


@st.cache_data(ttl=30)
def _load_day_plans(plan_date_iso: str) -> pd.DataFrame:
    plan_date = date.fromisoformat(plan_date_iso) if plan_date_iso else None
    return daily_plan.list_plans(plan_date=plan_date)


@st.cache_data(ttl=30)
def _load_all_plans() -> pd.DataFrame:
    """Devolve todos os planos do usuário autenticado (cache compartilhado
    com o Dashboard para calcular aderência ao plano)."""
    return daily_plan.list_plans(plan_date=None)


def render_day_plan() -> None:
    st.subheader(t("dayplan.title"))
    st.caption(t("dayplan.caption"))

    today_chicago = pd.Timestamp.now(tz="America/Chicago").date()
    if "_day_plan_date" not in st.session_state:
        st.session_state["_day_plan_date"] = today_chicago

    col_date, col_today, col_copy, _ = st.columns([2, 1, 1, 3])
    selected_date = col_date.date_input(
        t("dayplan.date_label"),
        value=st.session_state["_day_plan_date"],
        format="DD/MM/YYYY",
        key="_day_plan_date_input",
    )
    if col_today.button(
        t("dayplan.btn_today_chicago"),
        width="stretch",
        key="day_plan_today_chicago",
    ):
        st.session_state["_day_plan_date"] = today_chicago
        st.rerun()
    if col_copy.button(
        t("dayplan.btn_copy_prev"),
        width="stretch",
        key="day_plan_copy_prev",
        help=t("dayplan.btn_copy_prev_help"),
    ):
        try:
            prev_date = daily_plan.last_planned_date_before(selected_date)
        except Exception as e:
            st.error(t("dayplan.err_load", msg=str(e)))
            prev_date = None
        if prev_date is None:
            st.info(t("dayplan.copy_none"))
        else:
            with st.spinner(t("dayplan.copying", src=prev_date.isoformat())):
                result = daily_plan.copy_plans(prev_date, selected_date)
            if result["ok"]:
                if result["copied"] == 0 and result["skipped"] == 0:
                    st.info(t("dayplan.copy_none"))
                else:
                    st.success(t(
                        "dayplan.copy_ok",
                        src=prev_date.isoformat(),
                        copied=result["copied"],
                        skipped=result["skipped"],
                    ))
                _load_day_plans.clear()
                st.rerun()
            else:
                st.error(t("dayplan.copy_err", err=result["error"]))
    st.session_state["_day_plan_date"] = selected_date

    try:
        original = _load_day_plans(selected_date.isoformat())
    except Exception as e:
        msg = str(e)
        if "daily_plans" in msg or "does not exist" in msg.lower():
            st.error(t("dayplan.err_table_missing"))
        else:
            st.error(t("dayplan.err_load", msg=msg))
        return

    plans_count = len(original)
    total_max_size = int(original["max_size"].fillna(0).sum()) if not original.empty else 0

    def _row_usd(row: pd.Series, points_col: str) -> float | None:
        return daily_plan.compute_usd(
            row.get(points_col), row.get("max_size"), row.get("contract_name"),
        )

    if not original.empty:
        total_stop_usd = sum(
            v for v in (
                _row_usd(r, "stop_points") for _, r in original.iterrows()
            ) if v is not None
        )
        total_target_usd = sum(
            v for v in (
                _row_usd(r, "target_points") for _, r in original.iterrows()
            ) if v is not None
        )
    else:
        total_stop_usd = 0.0
        total_target_usd = 0.0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(t("dayplan.kpi.plans_count"), plans_count)
    k2.metric(t("dayplan.kpi.total_max_size"), total_max_size)
    k3.metric(t("dayplan.kpi.total_stop_usd"), f"${total_stop_usd:,.2f}")
    k4.metric(t("dayplan.kpi.total_target_usd"), f"${total_target_usd:,.2f}")

    st.caption(t("dayplan.usd_hint"))

    st.session_state["_day_plan_original"] = original.copy()

    long_label = t("dayplan.direction.long")
    short_label = t("dayplan.direction.short")

    display = original.copy()
    if not display.empty:
        display["direction"] = display["direction"].map(
            lambda v: long_label if v == "Long" else (short_label if v == "Short" else v)
        )
        display["stop_usd"] = display.apply(
            lambda r: _row_usd(r, "stop_points"), axis=1,
        )
        display["target_usd"] = display.apply(
            lambda r: _row_usd(r, "target_points"), axis=1,
        )
    else:
        display["stop_usd"] = pd.Series(dtype="float64")
        display["target_usd"] = pd.Series(dtype="float64")

    edited = st.data_editor(
        display,
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        column_order=[
            "contract_name", "direction", "max_size",
            "entry_trigger",
            "stop_points", "stop_usd",
            "target_points", "target_usd",
            "notes",
        ],
        column_config={
            "contract_name": st.column_config.TextColumn(
                t("dayplan.col.contract"), required=True, width="small",
            ),
            "direction": st.column_config.SelectboxColumn(
                t("dayplan.col.direction"),
                options=[long_label, short_label],
                required=True, width="small",
            ),
            "max_size": st.column_config.NumberColumn(
                t("dayplan.col.max_size"), min_value=1, step=1,
                required=True, width="small",
            ),
            "entry_trigger": st.column_config.TextColumn(
                t("dayplan.col.entry_trigger"), width="medium",
            ),
            "stop_points": st.column_config.NumberColumn(
                t("dayplan.col.stop_points"), step=0.25, width="small",
            ),
            "stop_usd": st.column_config.NumberColumn(
                t("dayplan.col.stop_usd"),
                format="$%.2f", disabled=True, width="small",
                help=t("dayplan.col.stop_usd_help"),
            ),
            "target_points": st.column_config.NumberColumn(
                t("dayplan.col.target_points"), step=0.25, width="small",
            ),
            "target_usd": st.column_config.NumberColumn(
                t("dayplan.col.target_usd"),
                format="$%.2f", disabled=True, width="small",
                help=t("dayplan.col.target_usd_help"),
            ),
            "notes": st.column_config.TextColumn(
                t("dayplan.col.notes"), width="medium",
            ),
            "id": None, "plan_date": None, "created_at": None, "updated_at": None,
        },
        key=f"day_plan_editor_{selected_date.isoformat()}",
    )

    col_save, col_reload, col_clear, _ = st.columns([1, 1, 1, 3])
    save_clicked = col_save.button(
        t("dayplan.btn_save"),
        type="primary",
        width="stretch",
        key="day_plan_save",
    )
    reload_clicked = col_reload.button(
        t("dayplan.btn_reload"),
        width="stretch",
        key="day_plan_reload",
    )
    clear_clicked = col_clear.button(
        t("dayplan.btn_clear"),
        width="stretch",
        disabled=original.empty,
        help=t("dayplan.btn_clear_help"),
        key="day_plan_clear",
    )

    if clear_clicked:
        st.session_state["_day_plan_pending_clear"] = selected_date.isoformat()
        st.rerun()

    pending_clear = st.session_state.get("_day_plan_pending_clear")
    if pending_clear == selected_date.isoformat() and not original.empty:
        st.warning(
            t("dayplan.clear_confirm", n=plans_count, day=selected_date.isoformat())
        )
        cc1, cc2, _ = st.columns([1, 1, 4])
        if cc1.button(
            t("dayplan.btn_clear_confirm"),
            type="primary",
            width="stretch",
            key="day_plan_clear_confirm",
        ):
            with st.spinner(t("dayplan.clearing")):
                result = daily_plan.delete_plans_for_date(selected_date)
            st.session_state.pop("_day_plan_pending_clear", None)
            if result["ok"]:
                st.success(t("dayplan.clear_ok", n=result["deleted"]))
                _load_day_plans.clear()
                st.rerun()
            else:
                st.error(t("dayplan.clear_err", err=result["error"]))
        if cc2.button(
            t("dayplan.btn_clear_cancel"),
            width="stretch",
            key="day_plan_clear_cancel",
        ):
            st.session_state.pop("_day_plan_pending_clear", None)
            st.rerun()

    if reload_clicked:
        _load_day_plans.clear()
        st.rerun()

    if save_clicked:
        edited_for_save = edited.copy()
        if not edited_for_save.empty:
            edited_for_save["direction"] = edited_for_save["direction"].map(
                lambda v: "Long" if v == long_label else ("Short" if v == short_label else v)
            )
        with st.spinner(t("dayplan.saving")):
            result = daily_plan.upsert_plans(
                original, edited_for_save, default_date=selected_date,
            )
        if result["ok"]:
            st.success(
                t("dayplan.save_ok",
                  ins=result['inserted'], upd=result['updated'], dele=result['deleted'])
            )
            _load_day_plans.clear()
            st.rerun()
        else:
            st.error(t("dayplan.save_err", err=result['error']))


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
        width="stretch",
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
    save_clicked = col_save.button(t("plan.btn_save"), type="primary", width="stretch")
    reload_clicked = col_reload.button(t("plan.btn_reload"), width="stretch")

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


@st.cache_data(ttl=3600, show_spinner=False)
def _load_contracts_catalog() -> list[dict]:
    return risk_plan.list_contracts()


@st.cache_data(ttl=600, show_spinner=False)
def _run_monte_carlo(
    balance, mll, dll, risk_usd, win_rate, avg_r, tpd, horizon,
    mode, buffer, n_sims, seed,
) -> dict:
    return risk_engine.monte_carlo(
        balance_usd=balance, mll_threshold_usd=mll, daily_loss_limit_usd=dll,
        risk_usd_per_trade=risk_usd, win_rate=win_rate, avg_r=avg_r,
        trades_per_day=tpd, horizon_days=horizon, trailing_mode=mode,
        balance_buffer=buffer, n_sims=n_sims, seed=seed,
    )


def render_risk_planner(user: dict, plan: dict | None) -> None:
    st.subheader(t("riskplanner.title"))
    st.caption(t("riskplanner.caption"))

    if not billing.has_feature(plan, "risk_planner"):
        st.warning(t("paywall.riskplanner.blocked"), icon="🔒")
        st.caption(t("paywall.riskplanner.cta"))
        return

    # Explicador do fluxo: o trader precisa enxergar que isto e' um caminho
    # (conta -> risco -> ativos -> simulacao), nao um amontoado de inputs.
    st.info(t("riskplanner.how_it_works"), icon="🧭")

    # Dia alvo = amanhã em ET (consistente com trade_day_et do resto do sistema).
    tomorrow_et = (pd.Timestamp.now(tz=timezones.PRIMARY_TZ) + pd.Timedelta(days=1)).date()
    if "_risk_plan_date" not in st.session_state:
        st.session_state["_risk_plan_date"] = tomorrow_et
    sel_date = st.date_input(
        t("riskplanner.date_label"),
        value=st.session_state["_risk_plan_date"],
        format="DD/MM/YYYY",
        key="_risk_plan_date_input",
    )
    st.session_state["_risk_plan_date"] = sel_date

    existing = risk_plan.get_plan(sel_date) or {}
    rs = risk_settings.get_settings() or {}

    # ===== 1. Sua conta =====
    st.markdown(f"#### {t('riskplanner.section.account')}")
    st.caption(t("riskplanner.section.account_hint"))

    account_types = risk_settings.ACCOUNT_TYPES
    default_at = existing.get("account_type") or rs.get("account_type") or account_types[0]
    at_index = account_types.index(default_at) if default_at in account_types else 0

    c1, c2 = st.columns(2)
    account_type = c1.selectbox(
        t("riskplanner.account_type"), account_types, index=at_index,
        key="_rp_account_type",
    )
    trailing_mode = c2.selectbox(
        t("riskplanner.trailing_mode"), ["combine", "xfa"],
        index=0 if (existing.get("trailing_mode") or "combine") == "combine" else 1,
        format_func=lambda m: t(f"riskplanner.trailing.{m}"),
        key="_rp_trailing_mode",
    )

    size_key = risk_engine.plan_size_key(account_type)
    buffer = risk_engine.TRAILING_BUFFER.get(size_key) if size_key else None
    dll_default = float(
        existing.get("daily_loss_limit_usd")
        or rs.get("daily_loss_limit_usd")
        or (risk_engine.DLL_DEFAULT.get(size_key) if size_key else 0.0)
        or 0.0
    )
    balance_default = float(existing.get("balance_usd") or 50000.0)
    mll_default = float(
        existing.get("mll_threshold_usd")
        or (balance_default - buffer if buffer else balance_default)
    )

    b1, b2, b3 = st.columns(3)
    balance = b1.number_input(
        t("riskplanner.balance"), min_value=0.0, value=balance_default,
        step=500.0, key="_rp_balance",
    )
    mll = b2.number_input(
        t("riskplanner.mll"), min_value=0.0, value=mll_default, step=250.0,
        help=t("riskplanner.mll_help"), key="_rp_mll",
    )
    dll = b3.number_input(
        t("riskplanner.dll"), min_value=0.0, value=dll_default, step=100.0,
        help=t("riskplanner.dll_help"), key="_rp_dll",
    )
    # Mostra de onde veio o MLL sugerido (buffer TopStep do tamanho da conta).
    if buffer and size_key:
        st.caption(t("riskplanner.buffer_auto", size=size_key, buffer=f"{buffer:,.0f}"))

    # ===== 2. Seu risco por trade =====
    st.markdown(f"#### {t('riskplanner.section.risk')}")
    st.caption(t("riskplanner.section.risk_hint"))
    r1, r2, r3 = st.columns(3)
    risk_mode = r1.radio(
        t("riskplanner.risk_mode"), ["pct", "usd"],
        format_func=lambda m: t(f"riskplanner.risk_mode.{m}"),
        horizontal=True, key="_rp_risk_mode",
    )
    risk_value = r2.number_input(
        t("riskplanner.risk_value"), min_value=0.0,
        value=float(existing.get("risk_value") or (1.0 if risk_mode == "pct" else 250.0)),
        step=0.25 if risk_mode == "pct" else 50.0, key="_rp_risk_value",
    )
    stop_points = r3.number_input(
        t("riskplanner.stop_points"), min_value=0.0, value=10.0, step=0.25,
        key="_rp_stop_points",
    )

    # ----- validações -----
    if balance <= 0:
        st.warning(t("riskplanner.err.balance_mll"))
        return
    distance = risk_engine.distance_to_blowout(balance, mll)
    if distance <= 0:
        st.error(t("riskplanner.err.distance_negative"))
    if stop_points <= 0:
        st.warning(t("riskplanner.err.stop"))
        return
    if size_key is None and dll <= 0:
        st.warning(t("riskplanner.err.custom_dll"))

    risk_usd = risk_engine.risk_dollars_per_trade(balance, risk_mode, risk_value)
    # Fecha o elo input->numero: o risco $/trade e' a base de todo o sizing.
    st.markdown(
        f"<p class='pos' style='font-weight:600'>→ {t('riskplanner.risk_derived', risk=f'{risk_usd:,.2f}')}</p>",
        unsafe_allow_html=True,
    )

    # ----- KPIs com leitura em linguagem simples -----
    n_tr = risk_engine.trades_to_dll(dll, risk_usd)
    days_bo = risk_engine.days_to_blowout(distance, dll)
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric(t("riskplanner.kpi.risk_per_trade"), f"${risk_usd:,.2f}",
                  help=t("riskplanner.kpi.risk_per_trade.help"))
        st.caption(t("riskplanner.kpi.risk_per_trade.read"))
    with k2:
        st.metric(t("riskplanner.kpi.trades_to_dll"), n_tr if n_tr else "—",
                  help=t("riskplanner.kpi.trades_to_dll.help"))
        if n_tr and n_tr <= 2:
            st.markdown(
                f"<span class='neg'>⚠ {t('riskplanner.kpi.trades_to_dll.read', n=n_tr)}</span>",
                unsafe_allow_html=True)
        elif n_tr:
            st.caption(t("riskplanner.kpi.trades_to_dll.read", n=n_tr))
    with k3:
        st.metric(t("riskplanner.kpi.distance"), f"${distance:,.2f}",
                  help=t("riskplanner.kpi.distance.help"))
        st.caption(t("riskplanner.kpi.distance.read"))
    with k4:
        st.metric(
            t("riskplanner.kpi.days_to_blowout"),
            int(days_bo) if days_bo is not None else "—",
            help=t("riskplanner.kpi.days_to_blowout.help"))
        if days_bo is not None:
            st.caption(t("riskplanner.kpi.days_to_blowout.read", n=int(days_bo)))

    # ===== 3. Que ativos cabem no seu risco =====
    contracts = _load_contracts_catalog()
    if not contracts:
        st.info(t("riskplanner.no_contracts"))
        return
    comp = risk_engine.compare_assets(
        contracts=contracts, balance_usd=balance, mll_threshold_usd=mll,
        daily_loss_limit_usd=dll, risk_mode=risk_mode, risk_value=risk_value,
        default_stop_points=stop_points, account_type=account_type,
        max_position_size=rs.get("max_position_size"),
    )
    st.markdown(f"#### {t('riskplanner.section.assets')}")
    st.caption(t("riskplanner.compare.hint"))

    comp_display = comp.copy()
    comp_display.insert(0, "selected", False)
    # Recomendado = maior max_contracts (compare_assets ja ordena desc). Marca a
    # linha topo por padrao e anuncia, para o trader saber por onde comecar.
    if not comp_display.empty and float(comp_display.iloc[0]["max_contracts"] or 0) > 0:
        comp_display.iloc[0, comp_display.columns.get_loc("selected")] = True
        st.caption(t("riskplanner.compare.recommended",
                     name=str(comp_display.iloc[0]["contract_name"])))
    # Altura fixa que comporta todas as linhas (catalogo e' pequeno, ~12) para
    # nao aparecer barra de rolagem vertical sobre a ultima coluna. Cap em 25
    # linhas se o catalogo crescer. ~35px/linha + cabecalho.
    _editor_h = min(len(comp_display) + 1, 26) * 35 + 3
    edited = st.data_editor(
        comp_display,
        width="stretch", hide_index=True, height=_editor_h,
        column_order=[
            "selected", "contract_name", "point_value_usd", "max_contracts",
            "max_stop_points", "risk_usd", "n_trades_to_dll",
        ],
        column_config={
            "selected": st.column_config.CheckboxColumn(
                t("riskplanner.col.selected"), width="small"),
            "contract_name": st.column_config.TextColumn(
                t("riskplanner.col.contract"), disabled=True),
            "point_value_usd": st.column_config.NumberColumn(
                t("riskplanner.col.point_value"), format="$%.2f", disabled=True),
            "max_contracts": st.column_config.NumberColumn(
                t("riskplanner.col.max_contracts"), disabled=True),
            "max_stop_points": st.column_config.NumberColumn(
                t("riskplanner.col.max_stop"), format="%.2f", disabled=True),
            "risk_usd": st.column_config.NumberColumn(
                t("riskplanner.col.risk_usd"), format="$%.2f", disabled=True),
            "n_trades_to_dll": st.column_config.NumberColumn(
                t("riskplanner.col.n_trades"), width="medium", disabled=True),
            "is_micro": None, "p_blowout": None,
        },
        key=f"_rp_compare_editor_{sel_date.isoformat()}",
    )

    selected = edited[edited["selected"]] if "selected" in edited.columns else edited.iloc[0:0]
    total_sel = int(selected["max_contracts"].fillna(0).sum()) if not selected.empty else 0

    # regras TopStep sobre a seleção
    for rule in risk_engine.check_rules(
        account_type=account_type, planned_total_contracts=total_sel,
    ):
        if not rule["ok"]:
            warn_fn = st.error if rule["severity"] == "critical" else st.warning
            warn_fn(t(rule["detail_key"], **rule["ctx"]))

    cps, cpush, cdir, cov = st.columns([1, 1, 1, 1])
    # Sizing e' direcao-agnostico; deixa o trader escolher para que lado gravar
    # no Plano do Dia. "Ambas" evita que operar Short caia em falso "sem plano".
    push_dir = cdir.selectbox(
        t("riskplanner.push_direction"), ["both", "long", "short"],
        format_func=lambda d: t(f"riskplanner.push_dir.{d}"),
        key="_rp_push_dir",
    )
    overwrite = cov.checkbox(t("riskplanner.overwrite"), key="_rp_overwrite")
    _dir_map = {"both": ("Long", "Short"), "long": ("Long",), "short": ("Short",)}

    if cps.button(t("riskplanner.btn.save"), type="primary", width="stretch", key="_rp_save"):
        header = {
            "plan_date": sel_date.isoformat(), "account_type": account_type,
            "balance_usd": float(balance), "mll_threshold_usd": float(mll),
            "daily_loss_limit_usd": float(dll) or None, "trailing_mode": trailing_mode,
            "risk_mode": risk_mode, "risk_value": float(risk_value),
        }
        res = risk_plan.upsert_plan(header)
        if res["ok"] and res["id"]:
            asset_rows = [{
                "contract_name": r["contract_name"], "direction": "Long",
                "max_contracts": int(r["max_contracts"]),
                "risk_usd": float(r["risk_usd"]) if pd.notna(r["risk_usd"]) else None,
                "max_stop_points": float(r["max_stop_points"]) if pd.notna(r["max_stop_points"]) else None,
                "n_trades_to_dll": int(r["n_trades_to_dll"]) if pd.notna(r["n_trades_to_dll"]) else None,
                "selected": bool(r["selected"]),
            } for _, r in edited.iterrows()]
            risk_plan.save_assets(res["id"], asset_rows)
            st.success(t("riskplanner.save_ok"))
        else:
            st.error(t("riskplanner.save_err", err=res.get("error")))

    if cpush.button(
        t("riskplanner.btn_push"), width="stretch", key="_rp_push",
        disabled=selected.empty,
    ):
        with st.spinner(t("riskplanner.pushing")):
            res = risk_plan.push_to_daily_plans(
                sel_date, selected, overwrite=overwrite,
                directions=_dir_map[push_dir])
        if res["ok"]:
            if res["inserted"] == 0 and res["updated"] == 0 and res.get("skipped", 0) > 0:
                st.info(t("riskplanner.push_skipped", n=res["skipped"]))
            else:
                st.success(t(
                    "riskplanner.push_ok",
                    ins=res["inserted"], upd=res["updated"], skip=res.get("skipped", 0),
                ))
            _load_day_plans.clear()
        else:
            st.error(t("riskplanner.push_err", err=res.get("error")))

    # ===== 4. Simulação Monte Carlo de blowout =====
    st.markdown(f"#### {t('riskplanner.section.mc')}")
    st.caption(t("riskplanner.mc.hint"))
    m1, m2, m3, m4 = st.columns(4)
    win_rate = m1.number_input(
        t("riskplanner.mc.win_rate"), min_value=0.0, max_value=1.0,
        value=0.5, step=0.05, help=t("riskplanner.mc.win_rate.help"),
        key="_rp_win_rate")
    avg_r = m2.number_input(
        t("riskplanner.mc.avg_r"), min_value=0.1, value=1.5, step=0.1,
        help=t("riskplanner.mc.avg_r.help"), key="_rp_avg_r")
    tpd = m3.number_input(
        t("riskplanner.mc.trades_per_day"), min_value=1,
        value=int(n_tr) if n_tr else 5, step=1, key="_rp_trades_per_day")
    horizon = m4.number_input(
        t("riskplanner.mc.horizon"), min_value=1, value=20, step=1,
        key="_rp_horizon_days")
    # Parametros tecnicos (precisao estatistica + determinismo) — irrelevantes
    # para o trader no dia-a-dia; escondidos num expander com defaults solidos.
    with st.expander(t("riskplanner.mc.advanced")):
        st.caption(t("riskplanner.mc.advanced_hint"))
        a1, a2 = st.columns(2)
        n_sims = a1.number_input(
            t("riskplanner.mc.n_sims"), min_value=100, max_value=50000,
            value=10000, step=1000, help=t("riskplanner.mc.n_sims.help"),
            key="_rp_mc_simulations")
        seed = a2.number_input(
            t("riskplanner.mc.seed"), min_value=0, value=42, step=1,
            help=t("riskplanner.mc.seed.help"), key="_rp_mc_seed")

    if st.button(t("riskplanner.mc.btn_run"), type="primary", width="stretch", key="_rp_run_mc"):
        if risk_usd <= 0:
            st.warning(t("riskplanner.err.stop"))
        else:
            with st.spinner(t("riskplanner.mc.running")):
                mc = _run_monte_carlo(
                    float(balance), float(mll), float(dll), float(risk_usd),
                    float(win_rate), float(avg_r), int(tpd), int(horizon),
                    trailing_mode, size_key, int(n_sims), int(seed),
                )
            if mc.get("degenerate"):
                st.warning(t("riskplanner.mc.degenerate"))
            else:
                # Veredito em linguagem simples: P(blowout) vira ALTO/MEDIO/BAIXO
                # para o trader nao precisar interpretar a probabilidade crua.
                p_bo = mc["p_blowout"]
                if p_bo >= 0.20:
                    vk, vcls = "high", "neg"
                elif p_bo >= 0.05:
                    vk, vcls = "mid", ""
                else:
                    vk, vcls = "low", "pos"
                st.markdown(
                    f"<h4 class='{vcls}'>{t(f'riskplanner.mc.verdict.{vk}', days=int(horizon))}</h4>",
                    unsafe_allow_html=True)
                st.caption(t("riskplanner.mc.verdict.hint"))
                x1, x2, x3, x4 = st.columns(4)
                x1.metric(t("riskplanner.mc.p_blowout"), f"{mc['p_blowout']*100:.1f}%")
                x2.metric(t("riskplanner.mc.p_dll"), f"{mc['p_hit_dll_any_day']*100:.1f}%")
                x3.metric(t("riskplanner.mc.p_profit"), f"{mc['p_profit']*100:.1f}%")
                x4.metric(t("riskplanner.mc.expected_equity"), f"${mc['expected_final_equity']:,.0f}")
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    y=mc["equity_curve_p50"], mode="lines", name="P50",
                    line=dict(color="#5b9bd5", width=2)))
                fig.add_hline(
                    y=mll, line_dash="dash", line_color="#e08585",
                    annotation_text=t("riskplanner.mll"), annotation_position="top left")
                fig.update_layout(
                    template="plotly_dark", title=t("riskplanner.mc.curve_title"),
                    height=300, margin=dict(t=40, b=20, l=20, r=20),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig, width="stretch")
                snap = {k: mc[k] for k in (
                    "p_blowout", "p_hit_dll_any_day", "p_profit",
                    "expected_final_equity", "equity_pctiles",
                    "max_drawdown_pctiles", "n_sims", "seed",
                )}
                risk_plan.upsert_plan({
                    "plan_date": sel_date.isoformat(), "account_type": account_type,
                    "balance_usd": float(balance), "mll_threshold_usd": float(mll),
                    "daily_loss_limit_usd": float(dll) or None,
                    "trailing_mode": trailing_mode, "risk_mode": risk_mode,
                    "risk_value": float(risk_value), "win_rate": float(win_rate),
                    "avg_r": float(avg_r), "trades_per_day": int(tpd),
                    "horizon_days": int(horizon), "mc_simulations": int(n_sims),
                    "mc_seed": int(seed), "result_snapshot": snap,
                })


@st.cache_data(ttl=60, show_spinner=False)
def _load_risk_plans_range(user_id: str, start, end):
    del user_id  # chave de cache (RLS isola no servidor); evita vazar entre users
    return risk_plan.list_plans_range(start, end)


def render_risk_review(user: dict, plan: dict | None, groups, plans) -> None:
    """Avaliação de Risco retrospectiva (M15): confronta os trades importados
    (já filtrados pela sidebar) contra o plano de cada dia e diz se o trader
    cumpriu e onde errou. Backend puro em metrics.compute_risk_review."""
    st.subheader(t("riskreview.title"))
    st.caption(t("riskreview.caption"))

    if not billing.has_feature(plan, "risk_planner"):
        st.warning(t("paywall.riskplanner.blocked"), icon="🔒")
        st.caption(t("paywall.riskplanner.cta"))
        return

    if groups is None or groups.empty:
        st.info(t("riskreview.no_trades"))
        return

    gdays = pd.to_datetime(groups["group_start"]).dt.tz_convert(
        timezones.PRIMARY_TZ).dt.date
    start, end = gdays.min(), gdays.max()
    risk_plans = _load_risk_plans_range(user["id"], start, end)
    point_values = {
        str(c.get("symbol")): c.get("point_value_usd")
        for c in _load_contracts_catalog() if c.get("symbol")
    }
    review = metrics.compute_risk_review(groups, plans, risk_plans, point_values)

    st.caption(t("riskreview.mae_note"))

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric(t("riskreview.kpi.score"),
              f"{review['clean_days']}/{review['total_days']}",
              help=t("riskreview.kpi.score.help"))
    k2.metric(t("riskreview.kpi.stop"), review["stop_furado"])
    k3.metric(t("riskreview.kpi.risk"), review["risco_excedido"])
    k4.metric(t("riskreview.kpi.dll"), review["dll_furado"])
    k5.metric(t("riskreview.kpi.blowout"), review["blowout"])

    by_day = review["by_day"]
    if by_day.empty:
        st.info(t("riskreview.no_trades"))
        return

    vmap = {
        "stop_furado": t("riskreview.v.stop_furado"),
        "risco_excedido": t("riskreview.v.risco_excedido"),
        "dll_furado": t("riskreview.v.dll_furado"),
        "blowout": t("riskreview.v.blowout"),
    }

    def _verdict(row) -> str:
        if not row["has_plan"]:
            return t("riskreview.verdict.no_plan")
        if row["clean"]:
            return "✓ " + t("riskreview.verdict.ok")
        keys = [k for k in str(row["violations"]).split(",") if k]
        return " · ".join(vmap.get(k, k) for k in keys)

    disp = by_day.copy()
    disp["verdict"] = disp.apply(_verdict, axis=1)
    st.markdown(f"#### {t('riskreview.byday.title')}")
    st.dataframe(
        disp, width="stretch", hide_index=True,
        height=min(len(disp) + 1, 16) * 35 + 3,
        column_order=["trade_day", "n_ops", "realized_pnl", "planned_dll", "verdict"],
        column_config={
            "trade_day": st.column_config.DateColumn(
                t("riskreview.col.day"), format="DD/MM/YYYY"),
            "n_ops": st.column_config.NumberColumn(t("riskreview.col.ops")),
            "realized_pnl": st.column_config.NumberColumn(
                t("riskreview.col.pnl"), format="$%.2f"),
            "planned_dll": st.column_config.NumberColumn(
                t("riskreview.col.dll"), format="$%.0f"),
            "verdict": st.column_config.TextColumn(
                t("riskreview.col.verdict"), width="large"),
        },
    )

    ops = review["op_violations"]
    st.markdown(f"#### {t('riskreview.ops.title')}")
    if ops.empty:
        st.success(t("riskreview.all_clean"))
    else:
        st.dataframe(
            ops, width="stretch", hide_index=True,
            height=min(len(ops) + 1, 12) * 35 + 3,
            column_order=["trade_day", "contract_name", "direction",
                          "realized_loss", "planned_risk", "excess"],
            column_config={
                "trade_day": st.column_config.DateColumn(
                    t("riskreview.ops.col.day"), format="DD/MM/YYYY"),
                "contract_name": st.column_config.TextColumn(
                    t("riskreview.ops.col.contract")),
                "direction": st.column_config.TextColumn(
                    t("riskreview.ops.col.dir")),
                "realized_loss": st.column_config.NumberColumn(
                    t("riskreview.ops.col.realized"), format="$%.2f"),
                "planned_risk": st.column_config.NumberColumn(
                    t("riskreview.ops.col.planned"), format="$%.2f"),
                "excess": st.column_config.NumberColumn(
                    t("riskreview.ops.col.excess"), format="$%.2f"),
            },
        )


def render_import(user_id: str) -> None:
    st.subheader(t("import.subheader"))
    st.caption(t("import.caption"))

    # Gating por plano: usuários no plano free (trial expirado sem assinar)
    # não conseguem importar. Mostra paywall e retorna antes de renderizar
    # o uploader, para evitar a impressão de que o upload está disponível.
    if not billing.has_feature(_plan, "import"):
        st.warning(t("paywall.import.blocked"), icon="🔒")
        st.caption(t("paywall.import.cta"))
        return

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
            width="stretch",
            hide_index=True,
        )

    files = st.file_uploader(
        t("import.uploader"),
        type="csv",
        accept_multiple_files=True,
        key=uploader_key,
    )
    col_btn, _ = st.columns([1, 3])
    if files and col_btn.button(t("import.btn"), type="primary", width="stretch"):
        client = auth.get_client()
        total = 0
        rows_log: list[dict] = []
        n_files = len(files)
        # st.status transmite progresso por arquivo em tempo real (cada st.write
        # é "flushed" durante o run) em vez de um spinner mudo. Os upserts são
        # sequenciais e cada um custa ~rede; sem feedback granular o usuário acha
        # que travou enquanto a fila de arquivos processa.
        with st.status(t("import.processing", n=n_files), expanded=True) as status:
            prog = st.progress(0.0)
            for i, f in enumerate(files, 1):
                st.write(t("import.processing_file", i=i, n=n_files, name=f.name))
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
                prog.progress(i / n_files)
            status.update(
                label=t("import.done", n=total), state="complete", expanded=False
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
    if st.button(t("auth.sign_out"), width="stretch", key="btn_sign_out"):
        auth.sign_out()
    st.divider()

# Toast imediato de import recém-concluído, ANTES do reload pesado dos trades:
# o usuário vê a confirmação sem esperar o load_trades terminar. O banner +
# tabela detalhada aparecem na aba Import via csv_import_last_result (pop lá).
_imp_res = st.session_state.get("csv_import_last_result")
if _imp_res:
    st.toast(t("import.done", n=_imp_res["total"]), icon="✅")

with st.spinner(t("app.loading")):
    df_all = load_trades(_user["id"])

st.title(t("app.title"))
st.caption(t("app.caption_logged", email=_user.get('email') or _user['id']))

billing.render_trial_banner(_plan)

# Feedback do Stripe Checkout (success/cancel) — fora das abas para que
# o usuário veja a confirmação mesmo voltando para a aba Dashboard.
account.handle_checkout_return()

# Sem trades ainda: pula filtros, mas ainda mostra Import e Account.
if df_all.empty:
    st.warning(t("app.empty_no_trades"))
    _tab_imp, _tab_acc = st.tabs([t("tab.import"), t("tab.account")])
    with _tab_imp:
        render_import(_user["id"])
    with _tab_acc:
        account.render_account_tab(_user, _plan)
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
    if sel_days and st.button(t("sidebar.clear_days"), width="stretch"):
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
try:
    plans_all = _load_all_plans()
except Exception:
    # Se a tabela daily_plans ainda não foi criada no Supabase, segue sem
    # quebrar o dashboard. O expander de aderência mostra estado vazio.
    plans_all = pd.DataFrame()
adherence = metrics.compute_plan_adherence(groups, plans_all)

# --- Abas --------------------------------------------------------------------

_show_risk = billing.has_feature(_plan, "risk_planner")
_show_live = billing.has_feature(_plan, "live_monitor")
_tab_names = [t("tab.dashboard"), t("tab.coach"), t("tab.dayplan"), t("tab.plan"),
              t("tab.import")]
if _show_risk:
    _tab_names.append(t("tab.riskplanner"))
    _tab_names.append(t("tab.riskreview"))
if _show_live:
    _tab_names.append(t("tab.live"))
_tab_names.extend([t("tab.settings"), t("tab.account")])

_tabs = st.tabs(_tab_names)
tab_dash, tab_coach, tab_dayplan, tab_plan, tab_import = _tabs[:5]
_idx = 5
tab_risk = None
tab_review = None
if _show_risk:
    tab_risk = _tabs[_idx]
    _idx += 1
    tab_review = _tabs[_idx]
    _idx += 1
tab_live = None
if _show_live:
    tab_live = _tabs[_idx]
    _idx += 1
tab_settings, tab_account = _tabs[_idx], _tabs[_idx + 1]

with tab_dash:
    render_dashboard(
        df, df_with_groups, groups, pts_kpis, segments, daily, overview, adherence,
    )

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

with tab_dayplan:
    render_day_plan()

with tab_plan:
    render_action_plan()

with tab_import:
    render_import(_user["id"])

if tab_risk is not None:
    with tab_risk:
        render_risk_planner(_user, _plan)

if tab_review is not None:
    with tab_review:
        render_risk_review(_user, _plan, groups, plans_all)

if tab_live is not None:
    with tab_live:
        live_tab.render_live_tab(_user, _plan)

with tab_settings:
    user_settings.render_settings_tab(_user, _plan)

with tab_account:
    account.render_account_tab(_user, _plan)

# --- Rodapé ------------------------------------------------------------------

st.markdown(
    f"<div style='text-align:center; color:{MUTED}; font-size:0.8rem; "
    f"margin-top:2rem; padding-top:1rem; border-top:1px solid {GREY};'>"
    f"{t('app.footer')}</div>",
    unsafe_allow_html=True,
)
