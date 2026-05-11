"""
BI TopStep — Dashboard de trades.

Cross-filter estilo PowerBI: filtros na sidebar (período, contrato, tipo,
dia da semana) reaplicam em TODOS os gráficos abaixo.

Métricas em $ + em pontos (port do projeto TradePontos), com análise de
overlap grouping e segmentação por adições.

Rodar:   streamlit run app.py
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

import coach_ai
import metrics

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / "Env" / "Topstep_bi.env"

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
    page_title="BI TopStep",
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


# ----------------------------- Data loading ----------------------------------


@st.cache_data(ttl=60)
def load_trades() -> pd.DataFrame:
    load_dotenv(ENV_FILE)
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        st.error(
            "Credenciais Supabase não encontradas em Env/Topstep_bi.env "
            "(NEXT_PUBLIC_SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY)."
        )
        st.stop()
    client = create_client(url, key)
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

    st.subheader("KPIs em $")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total PnL líquido", fmt_money(total_pnl))
    c2.metric("Trade Win %", f"{win_rate:.1f}%")
    c3.metric(
        "Avg Win / Avg Loss",
        f"{fmt_money(overview['avg_winning_trade'])} / {fmt_money(overview['avg_losing_trade'])}",
    )
    c4.metric(
        "Day Win %",
        f"{overview['day_win_pct'] * 100:.1f}%",
        f"{overview['winning_days']}/{overview['total_days']} dias",
    )
    c5.metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞")
    c6.metric("Best Day % of Total Profit", f"{overview['best_day_pct_of_total'] * 100:.1f}%")

    # --- KPIs em $ (linha 2) — volume e direção ------------------------------
    c7, c8, c9, c10, c11, c12 = st.columns(6)
    best_day_date = overview["best_day"][0] if overview["best_day"] else None
    best_day_val = overview["best_day"][1] if overview["best_day"] else 0.0
    worst_day_date = overview["worst_day"][0] if overview["worst_day"] else None
    worst_day_val = overview["worst_day"][1] if overview["worst_day"] else 0.0

    c7.metric("Trades", f"{total_trades}")
    c8.metric("Total Lots Traded", f"{overview['total_lots']:,}")
    c9.metric("Avg Trade Duration", fmt_duration(overview["avg_trade_duration_sec"]))
    c10.metric("Avg Win Duration", fmt_duration(overview["avg_win_duration_sec"]))
    c11.metric("Melhor dia", fmt_money(best_day_val), f"{best_day_date}" if best_day_date else "")
    c12.metric("Pior dia", fmt_money(worst_day_val), f"{worst_day_date}" if worst_day_date else "")

    # --- Best/Worst Trade individual + Trade Direction -----------------------
    bt = overview["best_trade"]
    wt = overview["worst_trade"]

    def _trade_card(title: str, t: dict | None, accent: str) -> str:
        if not t:
            return f"<div class='segment-box'><h4 style='color:{accent}'>{title}</h4><p>—</p></div>"
        entered = pd.to_datetime(t["entered_at"]).tz_convert("America/Sao_Paulo")
        return f"""
        <div class="segment-box">
            <h4 style="color:{accent}">{title}</h4>
            <div class="segment-row"><span>PnL líquido</span>
                <span class="{color_class(t['pnl_net'])}">{fmt_money(t['pnl_net'])}</span></div>
            <div class="segment-row"><span>Contrato</span><span>{t['contract_name']} · {t['type']}</span></div>
            <div class="segment-row"><span>Qtd</span><span>{t['size']}</span></div>
            <div class="segment-row"><span>Entrada @</span><span>{t['entry_price']:,.2f}</span></div>
            <div class="segment-row"><span>Saída @</span><span>{t['exit_price']:,.2f}</span></div>
            <div class="segment-row"><span>Data</span><span>{entered.strftime('%d/%m %H:%M')}</span></div>
        </div>
        """

    bt_col, wt_col, dir_col = st.columns([1, 1, 1])
    bt_col.markdown(_trade_card("Best Trade", bt, GREEN), unsafe_allow_html=True)
    wt_col.markdown(_trade_card("Worst Trade", wt, RED), unsafe_allow_html=True)
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
                title="Trade Direction %", showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    # --- KPIs em pontos ------------------------------------------------------
    st.subheader("KPIs em pontos")
    p1, p2, p3, p4, p5, p6 = st.columns(6)
    p1.metric("Net Points", fmt_pts(pts_kpis["total_net_points"]))
    p2.metric("Avg Win Pts", f"{pts_kpis['avg_winning_trade_points']:.2f}")
    p3.metric("Avg Loss Pts", f"{pts_kpis['avg_losing_trade_points']:.2f}")
    p4.metric("R/R Average", f"{pts_kpis['rr_average']:.2f}")
    p5.metric("R/R Aggregate", f"{pts_kpis['rr_aggregate']:.2f}")
    p6.metric(
        "Operações (grupos)",
        f"{pts_kpis['total_grouped_operations']}",
        f"Win Rate: {fmt_pct(pts_kpis['win_rate_grouped'])}",
    )

    st.divider()

    # --- Equity curves ($ e pontos) -----------------------------------------
    st.subheader("Equity curve")
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
        fig.update_layout(**PLOTLY_LAYOUT, height=300, title="PnL líquido acumulado ($)")
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
        fig.update_layout(**PLOTLY_LAYOUT, height=300, title="Pontos acumulados")
        st.plotly_chart(fig, use_container_width=True)

    # --- Daily charts (TopStepX style) --------------------------------------
    col_d1, col_d2 = st.columns(2)

    with col_d1:
        st.subheader("Pontos diários")
        st.caption("Clique nas barras (ou desenhe um box) para filtrar dias.")
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
        st.subheader("Contratos por dia (size)")
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
        st.subheader("PnL por dia da semana")
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
        st.subheader("PnL por hora de entrada (BRT)")
        hr = df.groupby("entry_hour", as_index=False)["pnl_net"].sum()
        fig = go.Figure(
            go.Bar(
                x=hr["entry_hour"], y=hr["pnl_net"],
                marker_color=[GREEN if v >= 0 else RED for v in hr["pnl_net"]],
                hovertemplate="<b>%{x}h</b><br>PnL: $%{y:,.2f}<extra></extra>",
            )
        )
        fig.update_layout(**PLOTLY_LAYOUT, height=300, xaxis_title="Hora (BRT)")
        st.plotly_chart(fig, use_container_width=True)

    # --- PnL por contrato + heatmap calendário ------------------------------
    col_c, col_d = st.columns([1, 2])

    with col_c:
        st.subheader("PnL por contrato")
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
        st.subheader("Calendário de PnL diário")
        st.caption("Clique nas células (ou desenhe um box) para filtrar dias.")
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
                    x=["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
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
    st.subheader("Daily P&L ($)")
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
            fig.update_layout(**PLOTLY_LAYOUT, height=300, title="Daily Net Cumulative P&L")
            st.plotly_chart(fig, use_container_width=True)
        with col_n2:
            fig = go.Figure(
                go.Bar(
                    x=daily_pnl["trade_day"], y=daily_pnl["pnl_net"],
                    marker_color=[GREEN if v >= 0 else RED for v in daily_pnl["pnl_net"]],
                    hovertemplate="<b>%{x}</b><br>PnL: $%{y:,.2f}<extra></extra>",
                )
            )
            fig.update_layout(**PLOTLY_LAYOUT, height=300, title="Net Daily P&L")
            st.plotly_chart(fig, use_container_width=True)

    # --- Trade Duration Analysis + Win Rate Analysis -------------------------
    duration_buckets = metrics.compute_duration_buckets(df)
    if not duration_buckets.empty and duration_buckets["trades"].sum() > 0:
        col_du1, col_du2 = st.columns(2)
        with col_du1:
            st.subheader("Trade Duration Analysis")
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
            st.subheader("Win Rate Analysis")
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
    with st.expander("Análise de Adições (operações agrupadas)", expanded=False):
        st.caption(
            "Trades que se sobrepõem no tempo (mesmo contrato + tipo) viram uma "
            "**operação** (grupo). Adições = trades extras dentro da mesma operação."
        )

        def render_segment(title: str, seg: dict, accent: str = TEXT) -> str:
            pnl_cls = color_class(seg["total_pnl"])
            pts_cls = color_class(seg["total_points"])
            return f"""
            <div class="segment-box">
                <h4 style="color:{accent}">{title}</h4>
                <div class="segment-row"><span>Count</span><span>{seg['count']}</span></div>
                <div class="segment-row"><span>Total Pts</span>
                    <span class="{pts_cls}">{seg['total_points']:+,.2f}</span></div>
                <div class="segment-row"><span>Avg Pts</span>
                    <span class="{pts_cls}">{seg['avg_points']:+,.2f}</span></div>
                <div class="segment-row"><span>Total PnL</span>
                    <span class="{pnl_cls}">$ {seg['total_pnl']:,.2f}</span></div>
                <div class="segment-row"><span>Avg PnL</span>
                    <span class="{pnl_cls}">$ {seg['avg_pnl']:,.2f}</span></div>
                <div class="segment-row"><span>Win Rate</span>
                    <span>{seg['win_rate_by_group'] * 100:.1f}%</span></div>
                <div class="segment-row"><span>Avg Adições</span>
                    <span>{seg['avg_additions']:.2f}</span></div>
                <div class="segment-row"><span>Total Size</span>
                    <span>{int(seg['total_size'])}</span></div>
            </div>
            """

        s1, s2, s3, s4 = st.columns(4)
        s1.markdown(render_segment("Sem adição", segments["no_additions"], MUTED), unsafe_allow_html=True)
        s2.markdown(render_segment("Com adição", segments["with_additions"], BLUE), unsafe_allow_html=True)
        s3.markdown(render_segment("Adições vencedoras", segments["with_additions_winners"], GREEN), unsafe_allow_html=True)
        s4.markdown(render_segment("Adições perdedoras", segments["with_additions_losers"], RED), unsafe_allow_html=True)

        st.markdown("&nbsp;")
        st.markdown("**Operações agrupadas**")
        if not groups.empty:
            g_show = groups.sort_values("group_start", ascending=False)[
                [
                    "group_id", "contract_name", "type", "group_start", "group_end",
                    "trade_count", "additions_count", "total_points", "total_pnl",
                    "total_net_pnl", "duration_min", "points_status",
                ]
            ].rename(
                columns={
                    "group_id": "Grupo",
                    "contract_name": "Contrato",
                    "type": "Tipo",
                    "group_start": "Início",
                    "group_end": "Fim",
                    "trade_count": "# trades",
                    "additions_count": "# adições",
                    "total_points": "Total Pts",
                    "total_pnl": "PnL bruto",
                    "total_net_pnl": "PnL líquido",
                    "duration_min": "Duração (min)",
                    "points_status": "Status",
                }
            )
            st.dataframe(g_show, use_container_width=True, hide_index=True, height=280)

    st.divider()

    # --- Tabela de trades ---------------------------------------------------
    st.subheader(f"Trades ({len(df)})")
    show = df_with_groups.sort_values("entered_at", ascending=False)[
        [
            "id", "trade_day", "contract_name", "type", "size",
            "entry_price", "exit_price", "points", "pnl", "fees",
            "commissions", "pnl_net", "trade_duration", "group_id",
        ]
    ].rename(
        columns={
            "id": "Trade ID",
            "trade_day": "Dia",
            "contract_name": "Contrato",
            "type": "Tipo",
            "size": "Qtd",
            "entry_price": "Entrada",
            "exit_price": "Saída",
            "points": "Pts",
            "pnl": "PnL bruto",
            "fees": "Fees",
            "commissions": "Comissões",
            "pnl_net": "PnL líquido",
            "trade_duration": "Duração",
            "group_id": "Grupo",
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

    # --- Botão AI: análise narrativa via Claude Code CLI -----------------------
    ai_col1, ai_col2 = st.columns([1, 3])
    with ai_col1:
        run_ai = st.button(
            "🤖 Análise com Claude AI",
            type="primary",
            use_container_width=True,
            help=(
                "Envia um snapshot agregado dos trades filtrados ao Claude Code CLI "
                "(claude -p) e mostra a análise narrativa. Consome cota do seu plano."
            ),
        )
    with ai_col2:
        if not coach_ai.claude_available():
            st.warning("CLI `claude` não encontrado no PATH. Instale Claude Code para usar este botão.")
        else:
            st.caption("Roda em background (~30-60s). Spinner indica progresso.")

    if run_ai:
        with st.spinner("Claude analisando os trades…"):
            prompt = coach_ai.build_prompt(df, groups, df_all, filter_ctx)
            result = coach_ai.run_claude(prompt)
        st.session_state["coach_ai_last"] = result
        st.session_state["coach_ai_prompt"] = prompt

    if "coach_ai_last" in st.session_state:
        last = st.session_state["coach_ai_last"]
        if last["ok"]:
            with st.container(border=True):
                st.markdown("### 🤖 Análise Claude AI")
                # Escapa `$` para o Streamlit não interpretar valores monetários
                # como fórmulas LaTeX (que vinham renderizadas em itálico serifado).
                st.markdown(last["text"].replace("$", r"\$"))
            with st.expander("Ver prompt enviado (debug)", expanded=False):
                st.code(st.session_state.get("coach_ai_prompt", ""), language="markdown")
        else:
            st.error(f"Erro: {last['error']}")

    st.divider()

    st.subheader("Resumo executivo")
    for bullet in coach["headline"]:
        st.markdown(f"- {bullet}")

    st.divider()

    # --- Padrões comportamentais --------------------------------------------
    st.subheader("Padrões comportamentais")

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
                <h4 style="color:{rev_color}">Revenge trading</h4>
                <p>Trades abertos em até {metrics.REVENGE_WINDOW_MIN} min após uma perda
                acima da média.</p>
                <p><b>{rev['count']}</b> trades · PnL acumulado:
                <span class="{color_class(rev['pnl'])}">{fmt_money(rev['pnl'])}</span></p>
                <p>Avg PnL em revenge:
                <span class="{color_class(rev['revenge_avg_pnl'])}">{fmt_money(rev['revenge_avg_pnl'])}</span>
                · Avg PnL nos demais:
                <span class="{color_class(rev['baseline_avg_pnl'])}">{fmt_money(rev['baseline_avg_pnl'])}</span></p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        cut_color = RED if cut["flag"] else GREEN
        st.markdown(
            f"""
            <div class="coach-card">
                <h4 style="color:{cut_color}">Cortar ganhos / segurar perdas</h4>
                <p>Duração média de wins: <b>{fmt_duration(cut['avg_win_sec'])}</b></p>
                <p>Duração média de losses: <b>{fmt_duration(cut['avg_loss_sec'])}</b></p>
                <p>Razão loss/win: <b>{cut['ratio']:.2f}×</b>
                {'⚠️ assimetria alta' if cut['flag'] else '✓ razoável'}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with cc2:
        over_color = RED if over["tilt_avg_pnl"] < over["normal_avg_pnl"] else GREEN
        st.markdown(
            f"""
            <div class="coach-card">
                <h4 style="color:{over_color}">Overtrading</h4>
                <p>Threshold (p75 de trades/dia): <b>{over['threshold']}</b></p>
                <p><b>{over['tilt_days']}</b> dias acima do threshold</p>
                <p>Avg PnL em dias acima:
                <span class="{color_class(over['tilt_avg_pnl'])}">{fmt_money(over['tilt_avg_pnl'])}</span>
                · normais:
                <span class="{color_class(over['normal_avg_pnl'])}">{fmt_money(over['normal_avg_pnl'])}</span></p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        streak_text = ""
        if streak["start"] is not None:
            streak_text = (
                f"<p>De {streak['start'].strftime('%d/%m %H:%M')} a "
                f"{streak['end'].strftime('%d/%m %H:%M')}</p>"
            )
        st.markdown(
            f"""
            <div class="coach-card">
                <h4 style="color:{RED}">Maior sequência de perdas</h4>
                <p><b>{streak['length']}</b> losses consecutivos</p>
                <p>PnL acumulado:
                <span class="{color_class(streak['pnl'])}">{fmt_money(streak['pnl'])}</span></p>
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
            st.caption("Sem combinações com volume suficiente.")
            return
        show = sub.rename(
            columns={
                "contract_name": "Contrato",
                "weekday": "Dia",
                "entry_hour": "Hora",
                "trades": "# trades",
                "pnl": "PnL",
                "avg_pnl": "Avg PnL",
                "win_rate": "Win rate",
            }
        ).copy()
        show["PnL"] = show["PnL"].map(fmt_money)
        show["Avg PnL"] = show["Avg PnL"].map(fmt_money)
        show["Win rate"] = show["Win rate"].map(lambda v: f"{v*100:.0f}%")
        st.dataframe(show, use_container_width=True, hide_index=True)

    with col_leak:
        render_combo_table("Top vazamentos (contrato × dia × hora)", coach["leaks"], RED)

    with col_str:
        render_combo_table("Top pontos fortes (contrato × dia × hora)", coach["strengths"], GREEN)

    st.divider()

    # --- Tamanho de posição --------------------------------------------------
    col_sz, col_dist = st.columns(2)

    with col_sz:
        st.subheader("PnL médio por tamanho de posição")
        sz = coach["size_buckets"]
        if sz.empty:
            st.caption("Sem dados de size.")
        else:
            fig = go.Figure()
            fig.add_trace(
                go.Bar(
                    x=sz["size"].astype(str), y=sz["avg_pnl"],
                    marker_color=[GREEN if v >= 0 else RED for v in sz["avg_pnl"]],
                    text=[f"n={int(t)}" for t in sz["trades"]],
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
                xaxis_title="Size", yaxis_title="Avg PnL ($)",
            )
            st.plotly_chart(fig, use_container_width=True)

    with col_dist:
        st.subheader("Distribuição de pontos por trade")
        dist = coach["points_distribution"]
        if not dist["values"]:
            st.caption("Sem dados.")
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
                          annotation_text=f"média {dist['mean']:.2f}",
                          annotation_position="top right")
            fig.add_vline(x=dist["median"], line_color=RED, line_dash="dot",
                          annotation_text=f"mediana {dist['median']:.2f}",
                          annotation_position="top left")
            fig.update_layout(
                **PLOTLY_LAYOUT, height=320,
                xaxis_title="Pontos", yaxis_title="Frequência",
            )
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # --- Checklist acionável -------------------------------------------------
    st.subheader("Checklist acionável")
    if not coach["checklist"]:
        st.caption("Sem recomendações automáticas com os filtros atuais — parabéns ou amostra pequena.")
    else:
        for item in coach["checklist"]:
            st.markdown(f"<div class='coach-check'>• {item}</div>", unsafe_allow_html=True)


# ----------------------------- App -------------------------------------------

df_all = load_trades()

st.title("BI TopStep")
st.caption("Dashboard de trades — filtros aplicam em todos os gráficos.")

if df_all.empty:
    st.warning("Nenhum trade no banco ainda. Rode `python ingest.py` primeiro.")
    st.stop()

# --- Sidebar: filtros (cross-filter) -----------------------------------------

with st.sidebar:
    st.header("Filtros")

    min_d, max_d = df_all["trade_day"].min(), df_all["trade_day"].max()

    # Atalhos de período — calculados a partir de hoje (BRT), com clamp em min/max.
    today_brt = pd.Timestamp.now(tz="America/Sao_Paulo").date()
    shortcut_options = [
        "Personalizado",
        "Hoje",
        "Últimos 7 dias",
        "Semana atual",
        "Últimos 30 dias",
        "Mês atual",
        "Tudo",
    ]
    shortcut = st.radio(
        "Atalhos de período",
        options=shortcut_options,
        index=0,
        horizontal=False,
        key="date_shortcut",
    )

    def _clamp(d: date) -> date:
        return max(min_d, min(max_d, d))

    preset_range: tuple[date, date] | None = None
    if shortcut == "Hoje":
        preset_range = (_clamp(today_brt), _clamp(today_brt))
    elif shortcut == "Últimos 7 dias":
        preset_range = (_clamp(today_brt - timedelta(days=6)), _clamp(today_brt))
    elif shortcut == "Semana atual":
        # Semana = segunda a domingo da semana corrente.
        monday = today_brt - timedelta(days=today_brt.weekday())
        sunday = monday + timedelta(days=6)
        preset_range = (_clamp(monday), _clamp(sunday))
    elif shortcut == "Últimos 30 dias":
        preset_range = (_clamp(today_brt - timedelta(days=29)), _clamp(today_brt))
    elif shortcut == "Mês atual":
        first = today_brt.replace(day=1)
        # Último dia do mês = primeiro do próximo mês - 1.
        if first.month == 12:
            next_first = first.replace(year=first.year + 1, month=1)
        else:
            next_first = first.replace(month=first.month + 1)
        last = next_first - timedelta(days=1)
        preset_range = (_clamp(first), _clamp(last))
    elif shortcut == "Tudo":
        preset_range = (min_d, max_d)

    default_range = preset_range if preset_range is not None else (min_d, max_d)
    # `key` muda junto com o atalho para forçar o date_input a reler o `value`.
    date_range = st.date_input(
        "Período (trade day)",
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
    sel_contracts = st.multiselect("Contrato", contracts, default=contracts)

    types = sorted(df_all["type"].unique())
    sel_types = st.multiselect("Tipo (Long/Short)", types, default=types)

    weekdays_order = [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
    ]
    sel_weekdays = st.multiselect("Dia da semana", weekdays_order, default=weekdays_order)

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
        "Dias específicos (clique nos gráficos ou escolha aqui)",
        options=available_days,
        default=canonical,
        format_func=lambda d: d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d),
        key="selected_days_widget",
        on_change=_sync_selected_days,
        help="Vazio = todos os dias do período. Use os gráficos 'Pontos diários' "
             "ou 'Calendário de PnL' para selecionar via clique/box.",
    )
    if sel_days and st.button("Limpar seleção de dias", use_container_width=True):
        st.session_state["selected_days"] = []
        # Remover a key do widget força o multiselect a reler o `default` no
        # próximo run (caso contrário ele preserva o valor anterior).
        st.session_state.pop("selected_days_widget", None)
        st.rerun()

    result_filter = st.radio(
        "Resultado",
        options=["Todos", "Só ganhadores", "Só perdedores"],
        index=0,
        horizontal=False,
    )

    st.divider()
    if st.button("Recarregar dados do Supabase"):
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
if result_filter == "Só ganhadores":
    df = df[df["pnl_net"] > 0]
elif result_filter == "Só perdedores":
    df = df[df["pnl_net"] <= 0]

if df.empty:
    st.warning("Nenhum trade encontrado com os filtros atuais.")
    st.stop()

# --- Derivações: grupos, KPIs em pts, segmentos, daily -----------------------

df_with_groups, groups = metrics.compute_groups(df)
pts_kpis = metrics.compute_kpis(df_with_groups, groups)
segments = metrics.compute_segments(groups)
daily = metrics.compute_daily(df_with_groups)
overview = metrics.compute_overview(df_with_groups)

# --- Abas --------------------------------------------------------------------

tab_dash, tab_coach = st.tabs(["Dashboard", "Coach"])

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
