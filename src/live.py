"""
Aba "Live" — monitor em tempo real (plano Pro).

Sub-secoes:
1. Status da conexao — ultimo snapshot recebido em live_snapshots.
2. Posicao atual — extraida do ultimo snapshot (contrato, size, side, PnL).
3. Alertas recentes — ultimos 20 da tabela alerts.
4. Instalacao da extensao — link de download (.zip) + passo a passo.

Realtime via st_autorefresh (3s). Web Notifications API e CRUD de alertas
(mark_read/dismissed) entram na Fase 3 (T3.3, T3.4, T3.5).

Visivel apenas para usuarios com feature `live_monitor` ativa.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import alerts as alerts_mod
import app_releases
import auth
import billing
import timezones
from i18n import t


@st.cache_data(ttl=2, show_spinner=False)
def _fetch_last_snapshot(_user_id: str) -> dict[str, Any] | None:
    """Ultimo snapshot do usuario corrente.

    `_user_id` so existe como chave de cache; a query usa o JWT do cliente
    autenticado (RLS filtra naturalmente).
    """
    try:
        r = (
            auth.get_client().table("live_snapshots")
            .select("*")
            .order("snapshot_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        return rows[0] if rows else None
    except Exception:
        return None


@st.cache_data(ttl=2, show_spinner=False)
def _fetch_recent_alerts(_user_id: str, limit: int = 20) -> pd.DataFrame:
    return alerts_mod.list_recent(limit=limit)


def _section_status(snap: dict | None) -> None:
    st.markdown(f"#### {t('live.section.status')}")
    if not snap:
        st.info(t("live.status.no_snapshot"), icon="⏳")
        return
    snap_ts = pd.to_datetime(snap["snapshot_at"], utc=True)
    now = pd.Timestamp.now(tz=timezone.utc)
    age_s = int((now - snap_ts).total_seconds())

    cols = st.columns([1, 1, 2])
    age_color = "pos" if age_s < 60 else ("warn" if age_s < 180 else "neg")
    cols[0].metric(t("live.status.last_snapshot_age"), f"{age_s}s")
    cols[1].metric(t("live.status.account_id"), snap.get("account_id") or "—")
    cols[2].caption(t("live.status.timestamp",
                      ts=timezones.fmt_dual(snap_ts)))
    if age_s >= 180:
        st.warning(t("live.status.stale", age=age_s), icon="🔌")
    # Suprime warning de variavel naoutilizada do linter
    _ = age_color


def _section_position(snap: dict | None) -> None:
    st.markdown(f"#### {t('live.section.position')}")
    if not snap or (snap.get("position_size") or 0) == 0:
        st.caption(t("live.position.flat"))
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(t("live.position.contract"), snap.get("position_contract") or "—")
    c2.metric(t("live.position.size"), int(snap.get("position_size") or 0))
    c3.metric(t("live.position.side"), snap.get("position_side") or "—")
    avg = snap.get("position_avg_price")
    c4.metric(t("live.position.avg_price"),
              f"{float(avg):,.4f}" if avg is not None else "—")
    upnl = float(snap.get("unrealized_pnl") or 0)
    c5.metric(t("live.position.unrealized"), f"${upnl:,.2f}")


def _section_pnl(snap: dict | None) -> None:
    st.markdown(f"#### {t('live.section.pnl')}")
    if not snap:
        st.caption(t("live.pnl.no_data"))
        return
    c1, c2, c3 = st.columns(3)
    c1.metric(t("live.pnl.realized"), f"${float(snap.get('realized_pnl') or 0):,.2f}")
    c2.metric(t("live.pnl.day"), f"${float(snap.get('day_pnl') or 0):,.2f}")
    c3.metric(t("live.pnl.drawdown"), f"${float(snap.get('drawdown') or 0):,.2f}")


def _section_alerts(df: pd.DataFrame) -> None:
    st.markdown(f"#### {t('live.section.alerts')}")
    if df.empty:
        st.caption(t("live.alerts.empty"))
        return

    # Action bar: marcar todos como lidos
    unread = int(df["read_at"].isna().sum()) if "read_at" in df.columns else 0
    c_meta, c_act = st.columns([3, 1])
    c_meta.caption(t("live.alerts.unread_count", n=unread))
    if c_act.button(t("live.alerts.mark_all_read"),
                    key="live_mark_all_read",
                    disabled=(unread == 0),
                    use_container_width=True):
        alerts_mod.mark_all_read()
        _fetch_recent_alerts.clear()
        st.rerun()

    # Lista compacta com badge e botoes por linha. Limita renderizacao a 20
    # linhas para nao engordar o rerun.
    visible = df.head(20)
    for _, row in visible.iterrows():
        sev = (row.get("severity") or "info").lower()
        sev_color = {
            "critical": "#e08585",
            "warn": "#e0b585",
            "info": "#65b5ff",
        }.get(sev, "#9aa0a6")
        when_str = timezones.fmt_dual(pd.to_datetime(row["created_at"], utc=True))
        unread_marker = "" if pd.notna(row.get("read_at")) else "•"
        dismissed_marker = "🗑" if pd.notna(row.get("dismissed_at")) else ""
        title = row.get("title") or ""
        body = row.get("body") or ""

        with st.container(border=True):
            c1, c2 = st.columns([6, 1])
            with c1:
                st.markdown(
                    f"<div style='display:flex;gap:10px;align-items:center'>"
                    f"<span style='color:{sev_color};font-weight:600;text-transform:uppercase;font-size:11px'>"
                    f"{sev}</span>"
                    f"<span style='color:#9aa0a6;font-size:11px'>{when_str}</span>"
                    f"<span style='color:#e08585;font-size:12px'>{unread_marker}</span>"
                    f"<span style='font-size:12px'>{dismissed_marker}</span>"
                    f"</div>"
                    f"<div style='font-weight:600;margin-top:4px'>{title}</div>"
                    f"<div style='font-size:12px;color:#cfd2d6'>{body}</div>",
                    unsafe_allow_html=True,
                )
            with c2:
                aid = int(row["id"])
                if pd.isna(row.get("read_at")):
                    if st.button(t("live.alerts.mark_read"),
                                 key=f"al_read_{aid}",
                                 use_container_width=True):
                        alerts_mod.mark_read(aid)
                        _fetch_recent_alerts.clear()
                        st.rerun()
                if pd.isna(row.get("dismissed_at")):
                    if st.button(t("live.alerts.dismiss"),
                                 key=f"al_dis_{aid}",
                                 use_container_width=True):
                        alerts_mod.mark_dismissed(aid)
                        _fetch_recent_alerts.clear()
                        st.rerun()


def _inject_web_notifications(user_id: str) -> None:
    """Componente JS embutido que escuta postgres_changes em public.alerts
    e dispara Web Notification quando uma nova linha de severity 'critical'
    e' inserida. Permissao e' pedida no primeiro load (Notification.requestPermission).

    O JWT da sessao e' passado para o supabase-js do navegador para autenticar
    a subscription Realtime — RLS no banco continua filtrando para que apenas
    o proprio user_id receba eventos.
    """
    supa_url = auth._read_secret("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL")
    supa_anon = auth._read_secret("SUPABASE_ANON_KEY", "NEXT_PUBLIC_SUPABASE_ANON_KEY")
    sess = st.session_state.get("session") or {}
    jwt = sess.get("access_token", "")
    if not (supa_url and supa_anon and jwt and user_id):
        return

    # Os valores aqui sao todos de fontes confiaveis (Supabase Auth + secrets
    # do app), mas usamos json.dumps + escape de `</` para `<\/` para evitar
    # que qualquer valor futuro contendo `</script>` quebre o bloco. Padrao
    # SSR de React/Next.js para inline JSON em <script>.
    cfg_literal = json.dumps({
        "supaUrl": supa_url,
        "supaAnon": supa_anon,
        "jwt": jwt,
        "userId": user_id,
        "channel": f"bi-alerts-{user_id}",
        "filter": f"user_id=eq.{user_id}",
    }).replace("</", "<\\/")

    components.html(
        f"""
        <div id="bi-notif-status" style="font:11px/1.4 monospace;color:#666"></div>
        <script type="module">
        const CFG = {cfg_literal};
        try {{
            const {{ createClient }} = await import("https://esm.sh/@supabase/supabase-js@2.45.4");
            const sb = createClient(CFG.supaUrl, CFG.supaAnon, {{
                realtime: {{ params: {{ apikey: CFG.supaAnon }} }},
            }});
            // Autentica o canal Realtime com o JWT do usuario (role:authenticated).
            // Sem isso, o canal cai no apiKey (role:anon) e RLS rejeita os eventos
            // — postgres_changes nunca chega no client, mesmo com publication ok.
            sb.realtime.setAuth(CFG.jwt);
            const setStatus = (m) => {{
                const el = document.getElementById("bi-notif-status");
                if (el) el.textContent = m;
            }};
            if (typeof Notification !== "undefined") {{
                if (Notification.permission === "default") {{
                    try {{ await Notification.requestPermission(); }} catch (_) {{}}
                }}
                setStatus("Web Notifications: " + Notification.permission);
            }} else {{
                setStatus("Web Notifications: not supported");
            }}
            sb.channel(CFG.channel)
              .on("postgres_changes",
                  {{ event: "INSERT", schema: "public", table: "alerts",
                     filter: CFG.filter }},
                  (p) => {{
                      const a = p.new || {{}};
                      const sev = (a.severity || "info").toLowerCase();
                      if (sev !== "critical" && sev !== "warn") return;
                      if (typeof Notification === "undefined") return;
                      if (Notification.permission !== "granted") return;
                      try {{
                          new Notification(a.title || "BI TopStep alert", {{
                              body: a.body || "",
                              tag: "bi-alert-" + (a.id || Date.now()),
                              requireInteraction: sev === "critical",
                          }});
                      }} catch (_) {{}}
                  }})
              .subscribe();
        }} catch (e) {{
            const el = document.getElementById("bi-notif-status");
            if (el) el.textContent = "Realtime err: " + (e?.message || e);
        }}
        </script>
        """,
        height=24,
    )


def _section_install() -> None:
    st.markdown(f"#### {t('live.section.install')}")
    st.caption(t("live.install.caption"))

    latest = app_releases.get_latest("extension")
    if latest:
        ver = latest.get("version", "?")
        released = str(latest.get("released_at", ""))[:10]
        # Badge com versao mais recente publicada
        st.info(
            t("live.install.latest_version", version=ver, released=released),
            icon="⬇️",
        )
        notes = latest.get("release_notes")
        if notes:
            with st.expander(t("live.install.release_notes_label")):
                st.markdown(notes)
        dl = latest.get("download_url")
        if dl:
            st.markdown(
                f"[{t('live.install.download_btn', version=ver)}]({dl})"
            )

    st.markdown(t("live.install.steps"))


def render_live_tab(user: dict, plan: dict | None) -> None:
    st.subheader(t("live.title"))
    st.caption(t("live.caption"))

    if not billing.has_feature(plan, "live_monitor"):
        st.warning(t("live.locked_by_plan"), icon="🔒")
        st.caption(t("paywall.import.cta"))
        return

    # Polling automatico: 3s. Se streamlit_autorefresh nao estiver instalado,
    # cai num botao "Atualizar" — graceful degradation para nao quebrar o app.
    try:
        from streamlit_autorefresh import st_autorefresh  # noqa: PLC0415
        st_autorefresh(interval=3000, key="live_refresh")
    except Exception:
        if st.button(t("live.refresh"), key="live_manual_refresh"):
            _fetch_last_snapshot.clear()
            _fetch_recent_alerts.clear()
            st.rerun()

    user_id = user["id"]

    # Componente JS embutido que dispara Web Notifications quando uma nova
    # linha em `alerts` chega via Supabase Realtime. Render UMA vez por
    # carregamento da aba — o st_autorefresh recria o iframe a cada 3s mas
    # o supabase-js dentro dele resubscreve sem custo.
    _inject_web_notifications(user_id)

    snap = _fetch_last_snapshot(user_id)
    alerts_df = _fetch_recent_alerts(user_id, limit=20)

    _section_status(snap)
    st.divider()
    _section_position(snap)
    _section_pnl(snap)
    st.divider()
    _section_alerts(alerts_df)
    st.divider()
    _section_install()
