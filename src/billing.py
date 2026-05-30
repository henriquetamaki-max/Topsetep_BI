"""
billing.py — resolução de plano, trial e gating de features.

Estado de assinatura vive em `public.subscriptions` no Supabase. A RPC
`current_user_plan()` aplica a degradação trial→free em tempo de leitura
(quando `trial_ends_at < now()`), portanto basta chamá-la para obter o
plano efetivo do usuário corrente.

Fluxo:
- `ensure_subscription(client)` — chama a RPC de safety net pós-login.
- `get_effective_plan(client, user_id)` — retorna dict com plan_slug,
  status, monthly_trade_limit, current_period_start/end, trial_ends_at,
  features. Cacheado por 60s por user_id.
- `has_feature(plan, key)` — checa booleano/string em plan["features"].
- `days_left_in_trial(plan)` — int >= 0 se trialing, None caso contrário.
- `render_trial_banner(plan)` — banner Streamlit visível em todas as
  páginas durante o trial e quando ele expira sem assinatura.

Stripe (Checkout/Portal) é introduzido em M3; este módulo é a base.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st


def ensure_subscription(client) -> None:
    """Garante que o usuário logado tenha uma linha em `subscriptions`.

    Em condições normais a trigger `on_auth_user_created_subscription`
    provisiona o trial; esta RPC é safety net (ex: usuário criado antes
    da trigger ser instalada).
    """
    try:
        client.rpc("ensure_subscription").execute()
    except Exception:
        # Best-effort: a próxima chamada a get_effective_plan vai falhar
        # de forma visível se a row realmente não existir.
        pass


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_plan(user_id: str) -> dict[str, Any] | None:
    """Chama a RPC current_user_plan() e retorna o primeiro registro.

    `user_id` (string, hasheável) entra na chave de cache para isolar
    tenants no mesmo processo Streamlit. NÃO usar prefixo `_`: ele faz o
    Streamlit ignorar o arg no hash, e dois traders passam a compartilhar
    a entrada por todo o TTL — vazamento de plano/assinatura e bypass de
    feature-gate (gotcha 2026-05-23 em MEMORIA.md). A RPC continua usando
    `auth.uid()` do JWT; o arg é só chave de cache.
    """
    # Import tardio para não criar ciclo com app.py.
    import auth  # noqa: PLC0415

    client = auth.get_client()
    r = client.rpc("current_user_plan").execute()
    rows = r.data or []
    return rows[0] if rows else None


def get_effective_plan(user_id: str) -> dict[str, Any] | None:
    """Plano efetivo do usuário (com degradação trial→free aplicada).

    Retorna dict com chaves:
        plan_slug, status, monthly_trade_limit,
        current_period_start, current_period_end, trial_ends_at, features
    Ou None se não houver linha em `subscriptions` (caso anômalo).
    """
    return _fetch_plan(user_id)


def invalidate_plan_cache() -> None:
    """Limpa o cache de _fetch_plan. Chamar após upgrade/cancelamento
    para refletir mudanças sem esperar TTL de 60s."""
    _fetch_plan.clear()


def has_feature(plan: dict[str, Any] | None, key: str) -> bool:
    """True se features[key] é truthy. Para chaves que guardam strings
    (ex: dashboard='limited_30d'), use `feature_value()`."""
    if not plan:
        return False
    feats = plan.get("features") or {}
    v = feats.get(key)
    return bool(v)


def feature_value(plan: dict[str, Any] | None, key: str, default: Any = None) -> Any:
    if not plan:
        return default
    feats = plan.get("features") or {}
    return feats.get(key, default)


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def days_left_in_trial(plan: dict[str, Any] | None) -> int | None:
    """Dias restantes (>=0) se status='trialing'. None se não está em trial."""
    if not plan or plan.get("status") != "trialing":
        return None
    ends = _parse_ts(plan.get("trial_ends_at"))
    if ends is None:
        return None
    delta = ends - datetime.now(timezone.utc)
    seconds = max(0, int(delta.total_seconds()))
    return seconds // 86400


def is_trial_expired(plan: dict[str, Any] | None) -> bool:
    """True se status é 'free' por trial expirado sem assinar."""
    if not plan:
        return False
    return plan.get("status") == "free" and plan.get("plan_slug") == "free"


def is_admin(user_id: str) -> bool:
    """Checa se o usuário corrente está em `public.admin_users`."""
    import auth  # noqa: PLC0415

    client = auth.get_client()
    try:
        r = client.table("admin_users").select("user_id").eq("user_id", user_id).execute()
        return bool(r.data)
    except Exception:
        return False


def render_trial_banner(plan: dict[str, Any] | None) -> None:
    """Banner visível durante o trial e quando ele expira sem assinatura.

    - trialing com >3 dias: info azul, contagem regressiva.
    - trialing com <=3 dias: warning amarelo, CTA upgrade.
    - free (trial expirado): erro vermelho com CTA assinar.
    - active/past_due/canceled em ciclo de cobrança: sem banner.
    """
    if not plan:
        return

    if plan.get("plan_slug") == "admin":
        return

    from i18n import t  # noqa: PLC0415

    status = plan.get("status")

    if status == "trialing":
        days = days_left_in_trial(plan)
        if days is None:
            return
        if days <= 3:
            st.warning(t("trial.banner.ending_soon", days=days), icon="⏳")
        else:
            st.info(t("trial.banner.active", days=days), icon="🎁")
        return

    if is_trial_expired(plan):
        st.error(t("trial.banner.expired"), icon="🔒")
        return

    if status == "past_due":
        st.error(t("billing.past_due"), icon="💳")
        return


# ---------------------------------------------------------------------------
# Stripe Checkout / Customer Portal
# ---------------------------------------------------------------------------


def _stripe_secret() -> str | None:
    """Lê a chave secreta do Stripe via auth._read_secret (cache_resource-safe)."""
    import auth  # noqa: PLC0415
    return auth._read_secret("STRIPE_SECRET_KEY")


def _app_base_url() -> str:
    """URL base usada nos success/cancel URLs do Checkout."""
    import auth  # noqa: PLC0415
    return auth._read_secret("APP_BASE_URL", "APP_URL") or "http://localhost:8501"


@st.cache_data(ttl=300, show_spinner=False)
def stripe_price_id(plan_slug: str) -> str | None:
    """Resolve o Stripe Price ID para um plano pago.

    Fonte de verdade: secrets STRIPE_PRICE_BASIC / STRIPE_PRICE_PRO. Trial e
    free não têm preço (retornam None)."""
    import auth  # noqa: PLC0415
    if plan_slug == "basic":
        return auth._read_secret("STRIPE_PRICE_BASIC")
    if plan_slug == "pro":
        return auth._read_secret("STRIPE_PRICE_PRO")
    return None


def _init_stripe():
    """Configura stripe.api_key e devolve o módulo stripe (ou None se não houver chave).

    Import tardio: stripe é pesado (~centenas de ms no boot) e só é usado
    quando o usuário entra na aba Account e clica em upgrade/portal."""
    key = _stripe_secret()
    if not key:
        return None
    import stripe  # noqa: PLC0415
    stripe.api_key = key
    return stripe


def create_checkout_session(
    user_id: str,
    email: str | None,
    plan_slug: str,
    existing_customer_id: str | None = None,
) -> tuple[str | None, str | None]:
    """Cria uma Stripe Checkout Session de assinatura e retorna a URL.

    Retorna `(url, err)`. Em caso de sucesso, `err` é None.

    Comportamento:
    - Modo `subscription` com 1 item recorrente (price_id do plano).
    - `metadata.user_id` no Session E na Subscription (via
      `subscription_data.metadata`) — essencial para o webhook ligar a
      assinatura ao usuário Supabase.
    - Se `existing_customer_id` for fornecido, reusa o Customer (evita
      duplicar customers no Stripe entre upgrades).
    - success/cancel URLs voltam para a aba Account.
    """
    stripe = _init_stripe()
    if stripe is None:
        return None, "stripe_secret_missing"

    price_id = stripe_price_id(plan_slug)
    if not price_id:
        return None, "price_id_missing"

    base = _app_base_url().rstrip("/")
    metadata = {"user_id": user_id, "plan_slug": plan_slug}

    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "metadata": metadata,
        "subscription_data": {"metadata": metadata},
        "success_url": f"{base}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base}/?checkout=cancel",
        "allow_promotion_codes": True,
        "billing_address_collection": "auto",
    }
    if existing_customer_id:
        params["customer"] = existing_customer_id
    elif email:
        params["customer_email"] = email

    try:
        session = stripe.checkout.Session.create(**params)
        return session.url, None
    except Exception as e:
        return None, str(e)


def create_portal_session(stripe_customer_id: str) -> tuple[str | None, str | None]:
    """Cria uma Stripe Customer Portal Session e retorna a URL.

    Retorna `(url, err)`."""
    stripe = _init_stripe()
    if stripe is None:
        return None, "stripe_secret_missing"
    if not stripe_customer_id:
        return None, "no_customer"

    base = _app_base_url().rstrip("/")
    try:
        portal = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=f"{base}/?portal=return",
        )
        return portal.url, None
    except Exception as e:
        return None, str(e)


def stripe_configured() -> bool:
    """True se a secret key do Stripe está disponível (para esconder
    botões de upgrade quando o app está mal configurado)."""
    return bool(_stripe_secret())
