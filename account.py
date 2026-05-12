"""
account.py — Aba "Account" do dashboard.

Mostra ao usuário:
- Plano atual + status (trial / active / past_due / free).
- Dias de trial restantes ou data de renovação.
- Botões de upgrade (Basic/Pro) → Stripe Checkout.
- Botão "Manage subscription" → Stripe Customer Portal (só se já tem
  stripe_customer_id na linha de subscriptions).

Após pagar, o Stripe redireciona para o app com ?checkout=success. O
webhook do M4 é quem atualiza `subscriptions` no banco — esta aba só
sinaliza visualmente que o pagamento foi processado e pede pra recarregar.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st

import auth
import billing
from i18n import t


# ---------------------------------------------------------------------------
# Catálogo local de planos para o cartão de upgrade.
# Espelha public.plans no banco. price_cents idêntico ao seed do
# saas_schema.sql.
# ---------------------------------------------------------------------------
_PLAN_CATALOG = [
    {
        "slug": "basic",
        "name_key": "billing.plan.basic.name",
        "tagline_key": "billing.plan.basic.tagline",
        "price_cents": 1900,
        "trade_limit": 100,
    },
    {
        "slug": "pro",
        "name_key": "billing.plan.pro.name",
        "tagline_key": "billing.plan.pro.tagline",
        "price_cents": 4900,
        "trade_limit": None,
    },
]


def _fmt_money_cents(cents: int, currency: str = "USD") -> str:
    return f"${cents/100:,.2f} {currency}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_date(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone().strftime("%Y-%m-%d %H:%M")
    return str(value)


def _load_subscription_row(user_id: str) -> dict[str, Any] | None:
    """Lê a row de subscriptions do usuário corrente (com stripe_customer_id).

    `current_user_plan()` já dá o plano efetivo, mas NÃO retorna o
    stripe_customer_id — precisamos dele para abrir o Customer Portal.
    RLS garante que SELECT só retorna a row do próprio usuário."""
    try:
        r = (
            auth.get_client()
            .table("subscriptions")
            .select("plan_slug,status,stripe_customer_id,stripe_subscription_id,"
                    "current_period_start,current_period_end,trial_ends_at,"
                    "cancel_at_period_end")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        return r.data
    except Exception:
        return None


def _status_label(status: str | None) -> str:
    mapping = {
        "trialing":  "billing.status.trialing",
        "active":    "billing.status.active",
        "past_due":  "billing.status.past_due",
        "canceled":  "billing.status.canceled",
        "free":      "billing.status.free",
    }
    key = mapping.get(status or "")
    return t(key) if key else (status or "—")


def _handle_checkout_return() -> None:
    """Se o usuário voltou do Checkout, mostra feedback e limpa query params."""
    qp = st.query_params
    checkout = qp.get("checkout")
    if checkout == "success":
        st.success(t("billing.checkout.success"), icon="✅")
        st.info(t("billing.checkout.webhook_note"), icon="ℹ️")
        # Limpa para não repetir o aviso no próximo rerun.
        st.query_params.clear()
        # Invalida cache de plano para refletir mudança assim que webhook gravar.
        billing.invalidate_plan_cache()
    elif checkout == "cancel":
        st.warning(t("billing.checkout.canceled"), icon="↩️")
        st.query_params.clear()


def _render_plan_card(
    plan_def: dict,
    current_slug: str | None,
    user_id: str,
    email: str | None,
    customer_id: str | None,
) -> None:
    """Renderiza um cartão de plano (Basic ou Pro) com botão de upgrade."""
    name = t(plan_def["name_key"])
    tagline = t(plan_def["tagline_key"])
    price = _fmt_money_cents(plan_def["price_cents"])
    limit = plan_def["trade_limit"]
    limit_txt = (
        t("billing.plan.unlimited_imports")
        if limit is None
        else t("billing.plan.imports_per_cycle", n=limit)
    )

    is_current = current_slug == plan_def["slug"]

    with st.container(border=True):
        st.markdown(f"### {name}")
        st.caption(tagline)
        st.markdown(f"**{price}** / {t('billing.per_month')}")
        st.markdown(f"- {limit_txt}")
        st.markdown(f"- {t('billing.plan.feature.coach')}")
        st.markdown(f"- {t('billing.plan.feature.full_dashboard')}")

        if is_current:
            st.success(t("billing.current_plan"), icon="✓")
        else:
            label = t("billing.upgrade_to", plan=name)
            if st.button(label, key=f"upgrade_{plan_def['slug']}",
                         type="primary", use_container_width=True):
                _trigger_checkout(user_id, email, plan_def["slug"], customer_id)


def _trigger_checkout(
    user_id: str, email: str | None, plan_slug: str, customer_id: str | None,
) -> None:
    url, err = billing.create_checkout_session(
        user_id=user_id, email=email, plan_slug=plan_slug,
        existing_customer_id=customer_id,
    )
    if err:
        st.error(t("billing.checkout.error", err=err))
        return
    # Streamlit não tem redirect server-side; usamos meta refresh + botão fallback.
    st.markdown(
        f'<meta http-equiv="refresh" content="0; url={url}">',
        unsafe_allow_html=True,
    )
    st.link_button(t("billing.checkout.continue"), url, type="primary")
    st.stop()


def _trigger_portal(customer_id: str) -> None:
    url, err = billing.create_portal_session(customer_id)
    if err:
        st.error(t("billing.portal.error", err=err))
        return
    st.markdown(
        f'<meta http-equiv="refresh" content="0; url={url}">',
        unsafe_allow_html=True,
    )
    st.link_button(t("billing.portal.continue"), url, type="primary")
    st.stop()


def render_account_tab(user: dict, plan: dict | None) -> None:
    """Renderiza a aba Account completa.

    `user` vem de auth.current_user(); `plan` vem de billing.get_effective_plan().
    """
    st.subheader(t("account.title"))
    st.caption(t("account.caption"))

    _handle_checkout_return()

    if not billing.stripe_configured():
        st.warning(t("billing.not_configured"), icon="⚠️")

    sub = _load_subscription_row(user["id"]) or {}
    effective_slug = (plan or {}).get("plan_slug") or sub.get("plan_slug") or "trial"
    status = (plan or {}).get("status") or sub.get("status")
    customer_id = sub.get("stripe_customer_id")

    # --- Resumo atual --------------------------------------------------------
    col1, col2, col3 = st.columns(3)
    col1.metric(t("account.current_plan"), t(f"billing.plan.{effective_slug}.name"))
    col2.metric(t("account.status"), _status_label(status))

    if status == "trialing":
        days = billing.days_left_in_trial(plan)
        col3.metric(
            t("account.trial_ends"),
            t("account.days_left", n=days if days is not None else 0),
        )
    elif status == "active":
        col3.metric(
            t("account.renews_on"),
            _fmt_date((plan or {}).get("current_period_end")),
        )
    elif status == "past_due":
        col3.metric(t("account.status"), _status_label("past_due"))
    else:
        col3.metric(t("account.renews_on"), "—")

    if sub.get("cancel_at_period_end"):
        st.warning(
            t("billing.cancel_scheduled", date=_fmt_date(sub.get("current_period_end"))),
            icon="⏳",
        )

    st.divider()

    # --- Planos para upgrade -------------------------------------------------
    st.markdown(f"#### {t('account.upgrade_section')}")

    cols = st.columns(len(_PLAN_CATALOG))
    for col, plan_def in zip(cols, _PLAN_CATALOG):
        with col:
            _render_plan_card(
                plan_def=plan_def,
                current_slug=effective_slug,
                user_id=user["id"],
                email=user.get("email"),
                customer_id=customer_id,
            )

    # --- Gerenciar assinatura existente -------------------------------------
    if customer_id:
        st.divider()
        st.markdown(f"#### {t('account.manage_section')}")
        st.caption(t("account.manage_caption"))
        if st.button(t("billing.manage_subscription"),
                     key="open_portal", use_container_width=False):
            _trigger_portal(customer_id)
