"""
Aba "Configurações" — preferências de UI do usuário.

Sub-seções:
1. Idioma — reusa `i18n.language_selector()`.
2. Fuso horário — primário ET (read-only) + secundário do usuário (auto-detect
   no primeiro load + override manual, persistido em `user_metadata.preferred_tz`).
3. Risk Guard — stub. Implementação real em T3.1 (CRUD `risk_settings`).
4. Contas TopStep — stub. Implementação real em Fase 3.

Persistência:
- preferred_language → user_metadata.preferred_language (já existe em i18n.py).
- preferred_tz       → user_metadata.preferred_tz (novo aqui).
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

import auth
import billing
import i18n
import risk_settings as risk_settings_mod
import timezones
from i18n import t


# IANA short list mostrada no select. "auto" representa o detectado por JS.
COMMON_TIMEZONES: list[str] = [
    "America/Sao_Paulo",
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "America/Argentina/Buenos_Aires",
    "America/Mexico_City",
    "Europe/London",
    "Europe/Madrid",
    "Europe/Lisbon",
    "Europe/Berlin",
    "Europe/Paris",
    "UTC",
]


def _persist_user_tz(tz: str) -> tuple[bool, str | None]:
    """Atualiza preferred_tz em user_metadata. Best-effort."""
    try:
        client = auth.get_client()
        client.auth.update_user({"data": {"preferred_tz": tz}})
        st.session_state["user_tz"] = tz
        # Atualiza snapshot em memória do session para evitar refazer login.
        sess = st.session_state.get("session")
        if sess and isinstance(sess.get("user"), dict):
            meta = sess["user"].setdefault("user_metadata", {})
            meta["preferred_tz"] = tz
        return True, None
    except Exception as e:
        return False, str(e)


def _detect_browser_tz_component() -> None:
    """Injeta JS que pede ao navegador o fuso resolvido e o repassa por query
    param `_tz` na URL. Streamlit relê a URL e settings.py captura no próximo
    rerun. Idempotente: só dispara se ainda não houver `user_tz` em session.
    """
    if st.session_state.get("user_tz"):
        return
    # Já recebemos? Lê query param.
    qp = st.query_params
    detected = qp.get("_tz")
    if detected:
        st.session_state["user_tz"] = detected
        qp.clear()
        return
    # Dispara detecção via top-level redirect com query param.
    components.html(
        """
        <script>
        (function () {
            try {
                var tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
                var url = new URL(window.parent.location.href);
                if (!url.searchParams.get("_tz")) {
                    url.searchParams.set("_tz", tz);
                    window.parent.location.replace(url.toString());
                }
            } catch (e) { /* silently ignore */ }
        })();
        </script>
        """,
        height=0,
    )


def _render_section_language() -> None:
    st.markdown(f"#### {t('settings.section.language')}")
    i18n.language_selector(key="settings_language_selector")
    st.caption(t("settings.lang.hint"))


def _render_section_timezone() -> None:
    st.markdown(f"#### {t('settings.section.timezone')}")
    _detect_browser_tz_component()

    current = timezones.user_tz()
    st.text_input(
        t("settings.tz.primary_label"),
        value=f"{timezones.PRIMARY_TZ} ({timezones.PRIMARY_TZ_SHORT})",
        disabled=True,
        key="settings_tz_primary_ro",
    )

    options = list(dict.fromkeys([current, *COMMON_TIMEZONES]))
    idx = options.index(current) if current in options else 0
    selected = st.selectbox(
        t("settings.tz.secondary_label"),
        options=options,
        index=idx,
        format_func=lambda tz: f"{tz} ({timezones._tz_short(tz)})",
        key="settings_tz_secondary",
        help=t("settings.tz.auto_detected", tz=current),
    )

    if st.button(t("settings.tz.save_btn"), key="settings_tz_save"):
        ok, err = _persist_user_tz(selected)
        if ok:
            st.success(t("settings.tz.save_ok", tz=selected))
            st.rerun()
        else:
            st.error(t("settings.tz.save_err", err=err))


def _render_section_risk_guard(plan: dict | None) -> None:
    st.markdown(f"#### {t('settings.section.risk_guard')}")
    if not billing.has_feature(plan, "live_monitor"):
        st.info(t("settings.risk.locked_by_plan"), icon="🔒")
        return
    st.caption(t("settings.risk.caption"))

    current = risk_settings_mod.get_settings() or {}
    account_types = risk_settings_mod.ACCOUNT_TYPES
    current_type = current.get("account_type") or account_types[0]
    if current_type not in account_types:
        # tipo customizado armazenado — coloca no topo
        account_types = [current_type, *account_types]
        idx = 0
    else:
        idx = account_types.index(current_type)

    sel_type = st.selectbox(
        t("settings.risk.account_type"),
        options=account_types,
        index=idx,
        key="rs_account_type",
    )

    # Aplica defaults quando o usuário troca de tipo, sem sobrescrever
    # valores existentes do tipo atual.
    if sel_type != current.get("account_type"):
        defaults = risk_settings_mod.apply_account_defaults(sel_type)
        default_dll = defaults["daily_loss_limit_usd"]
        default_td = defaults["trailing_drawdown_usd"]
    else:
        default_dll = float(current.get("daily_loss_limit_usd") or 0.0)
        default_td = float(current.get("trailing_drawdown_usd") or 0.0)

    c1, c2 = st.columns(2)
    dll = c1.number_input(
        t("settings.risk.dll"),
        min_value=0.0,
        value=default_dll,
        step=100.0,
        format="%.2f",
        key="rs_dll",
        help=t("settings.risk.dll_help"),
    )
    td = c2.number_input(
        t("settings.risk.trailing_dd"),
        min_value=0.0,
        value=default_td,
        step=100.0,
        format="%.2f",
        key="rs_td",
        help=t("settings.risk.trailing_dd_help"),
    )

    c3, c4 = st.columns(2)
    mps = c3.number_input(
        t("settings.risk.max_size"),
        min_value=0,
        value=int(current.get("max_position_size") or 0),
        step=1,
        key="rs_max_size",
        help=t("settings.risk.max_size_help"),
    )
    wpct = c4.number_input(
        t("settings.risk.warning_pct"),
        min_value=10.0,
        max_value=100.0,
        value=float(current.get("warning_threshold_pct") or 80.0),
        step=5.0,
        format="%.1f",
        key="rs_warning_pct",
        help=t("settings.risk.warning_pct_help"),
    )

    if st.button(t("settings.risk.save_btn"), key="rs_save", type="primary"):
        payload = {
            "account_type": sel_type,
            "daily_loss_limit_usd": float(dll) if dll > 0 else None,
            "trailing_drawdown_usd": float(td) if td > 0 else None,
            "max_position_size": int(mps) if mps > 0 else None,
            "warning_threshold_pct": float(wpct),
        }
        r = risk_settings_mod.upsert_settings(payload)
        if r["ok"]:
            st.success(t("settings.risk.save_ok"))
            st.rerun()
        else:
            st.error(t("settings.risk.save_err", err=r["error"]))


def _render_section_accounts(plan: dict | None) -> None:
    if not billing.has_feature(plan, "live_monitor"):
        return
    st.markdown(f"#### {t('settings.section.accounts')}")
    st.caption(t("settings.accounts.stub_caption"))
    # Implementação completa em Fase 3.


def render_settings_tab(user: dict, plan: dict | None) -> None:
    st.subheader(t("settings.title"))
    st.caption(t("settings.caption"))

    _render_section_language()
    st.divider()
    _render_section_timezone()
    st.divider()
    _render_section_risk_guard(plan)
    st.divider()
    _render_section_accounts(plan)
