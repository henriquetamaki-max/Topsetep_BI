// Stripe webhook → Supabase
//
// Recebe eventos da assinatura no Stripe, verifica a assinatura HMAC e
// atualiza public.subscriptions usando service_role (bypassa RLS).
//
// Eventos tratados:
//   - checkout.session.completed     → primeira assinatura: linka customer/sub
//   - customer.subscription.updated  → mudança de plano, renovação, cancel agendado
//   - customer.subscription.deleted  → cancelamento efetivo
//   - invoice.payment_failed         → marca past_due
//   - invoice.payment_succeeded      → confirma active e atualiza período
//
// Endpoint público (no-verify-jwt): a autenticidade vem da assinatura
// Stripe, não do JWT do Supabase Auth.
//
// Secrets esperados (configurar via `supabase secrets set ...`):
//   STRIPE_SECRET_KEY
//   STRIPE_WEBHOOK_SECRET
//   STRIPE_PRICE_BASIC
//   STRIPE_PRICE_PRO
//   SUPABASE_URL                (injetada automaticamente pela plataforma)
//   SUPABASE_SERVICE_ROLE_KEY   (injetada automaticamente pela plataforma)

import Stripe from "https://esm.sh/stripe@14.25.0?target=deno";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

const STRIPE_SECRET_KEY      = Deno.env.get("STRIPE_SECRET_KEY")!;
const STRIPE_WEBHOOK_SECRET  = Deno.env.get("STRIPE_WEBHOOK_SECRET")!;
const STRIPE_PRICE_BASIC     = Deno.env.get("STRIPE_PRICE_BASIC") ?? "";
const STRIPE_PRICE_PRO       = Deno.env.get("STRIPE_PRICE_PRO") ?? "";
const SUPABASE_URL           = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_KEY   = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const stripe = new Stripe(STRIPE_SECRET_KEY, {
  apiVersion: "2024-06-20",
  httpClient: Stripe.createFetchHttpClient(),
});

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY, {
  auth: { persistSession: false },
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function priceToSlug(priceId: string | null | undefined): string | null {
  if (!priceId) return null;
  if (priceId === STRIPE_PRICE_BASIC) return "basic";
  if (priceId === STRIPE_PRICE_PRO)   return "pro";
  return null;
}

// Mapeia o status do Stripe para nosso enum public.subscription_status.
// `trialing` aqui significa "trial gerenciado pelo Stripe" (não usamos hoje
// — nosso trial é gerenciado por trigger no Postgres). Tratamos como active.
function mapStatus(stripeStatus: string): string {
  switch (stripeStatus) {
    case "active":
    case "trialing":
      return "active";
    case "past_due":
    case "unpaid":
      return "past_due";
    case "canceled":
    case "incomplete_expired":
      return "canceled";
    default:
      // incomplete, paused → mantém como past_due defensivamente
      return "past_due";
  }
}

function tsFromUnix(unix: number | null | undefined): string | null {
  if (!unix) return null;
  return new Date(unix * 1000).toISOString();
}

async function userIdFromCustomer(customerId: string): Promise<string | null> {
  // Primeiro tenta achar pela coluna stripe_customer_id (já linkado em algum
  // evento anterior).
  const { data, error } = await supabase
    .from("subscriptions")
    .select("user_id")
    .eq("stripe_customer_id", customerId)
    .maybeSingle();
  if (!error && data?.user_id) return data.user_id;

  // Fallback: lê metadata.user_id direto do Customer no Stripe.
  try {
    const cust = await stripe.customers.retrieve(customerId);
    if (cust && !("deleted" in cust)) {
      const uid = (cust.metadata?.user_id ?? "") as string;
      if (uid) return uid;
    }
  } catch (_e) {
    // ignore
  }
  return null;
}

async function upsertSubscription(
  userId: string,
  sub: Stripe.Subscription,
): Promise<void> {
  const item = sub.items.data[0];
  const priceId = item?.price?.id ?? null;
  const planSlug = priceToSlug(priceId);
  if (!planSlug) {
    console.warn("Unknown price_id, skipping upsert:", priceId);
    return;
  }

  const status = mapStatus(sub.status);
  const payload: Record<string, unknown> = {
    user_id: userId,
    plan_slug: planSlug,
    status,
    stripe_customer_id: sub.customer as string,
    stripe_subscription_id: sub.id,
    current_period_start: tsFromUnix(sub.current_period_start),
    current_period_end:   tsFromUnix(sub.current_period_end),
    cancel_at_period_end: sub.cancel_at_period_end ?? false,
  };

  const { error } = await supabase
    .from("subscriptions")
    .upsert(payload, { onConflict: "user_id" });

  if (error) {
    console.error("upsertSubscription error", error);
    throw error;
  }
}

// ---------------------------------------------------------------------------
// Handlers por tipo de evento
// ---------------------------------------------------------------------------

async function handleCheckoutCompleted(
  s: Stripe.Checkout.Session,
): Promise<void> {
  let userId = (s.metadata?.user_id ?? "") as string;
  if (!userId && s.customer) {
    userId = (await userIdFromCustomer(s.customer as string)) ?? "";
  }
  if (!userId) {
    console.warn("checkout.session.completed without user_id, skipping");
    return;
  }
  if (!s.subscription) {
    console.warn("checkout.session.completed without subscription, skipping");
    return;
  }
  const sub = await stripe.subscriptions.retrieve(s.subscription as string);
  await upsertSubscription(userId, sub);
}

async function handleSubscriptionChange(
  sub: Stripe.Subscription,
): Promise<void> {
  let userId = (sub.metadata?.user_id ?? "") as string;
  if (!userId) {
    userId = (await userIdFromCustomer(sub.customer as string)) ?? "";
  }
  if (!userId) {
    console.warn("subscription event without user_id, skipping:", sub.id);
    return;
  }
  await upsertSubscription(userId, sub);
}

async function handleSubscriptionDeleted(
  sub: Stripe.Subscription,
): Promise<void> {
  const userId = await userIdFromCustomer(sub.customer as string);
  if (!userId) return;
  const { error } = await supabase
    .from("subscriptions")
    .update({
      status: "canceled",
      cancel_at_period_end: false,
    })
    .eq("user_id", userId);
  if (error) {
    console.error("handleSubscriptionDeleted error", error);
    throw error;
  }
}

async function handleInvoicePaymentFailed(
  inv: Stripe.Invoice,
): Promise<void> {
  if (!inv.customer) return;
  const userId = await userIdFromCustomer(inv.customer as string);
  if (!userId) return;
  const { error } = await supabase
    .from("subscriptions")
    .update({ status: "past_due" })
    .eq("user_id", userId);
  if (error) {
    console.error("handleInvoicePaymentFailed error", error);
    throw error;
  }
}

async function handleInvoicePaymentSucceeded(
  inv: Stripe.Invoice,
): Promise<void> {
  // O subscription.updated já costuma chegar junto. Aqui só garantimos que
  // o status volte a `active` caso o usuário estivesse past_due.
  if (!inv.subscription) return;
  const sub = await stripe.subscriptions.retrieve(inv.subscription as string);
  await handleSubscriptionChange(sub);
}

// ---------------------------------------------------------------------------
// Entrypoint
// ---------------------------------------------------------------------------

Deno.serve(async (req) => {
  if (req.method !== "POST") {
    return new Response("method not allowed", { status: 405 });
  }

  const signature = req.headers.get("stripe-signature");
  if (!signature) {
    return new Response("missing signature", { status: 400 });
  }

  const body = await req.text();

  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(
      body,
      signature,
      STRIPE_WEBHOOK_SECRET,
      undefined,
      Stripe.createSubtleCryptoProvider(),
    );
  } catch (err) {
    console.error("signature verification failed:", err);
    return new Response("invalid signature", { status: 400 });
  }

  try {
    switch (event.type) {
      case "checkout.session.completed":
        await handleCheckoutCompleted(event.data.object as Stripe.Checkout.Session);
        break;
      case "customer.subscription.created":
      case "customer.subscription.updated":
        await handleSubscriptionChange(event.data.object as Stripe.Subscription);
        break;
      case "customer.subscription.deleted":
        await handleSubscriptionDeleted(event.data.object as Stripe.Subscription);
        break;
      case "invoice.payment_failed":
        await handleInvoicePaymentFailed(event.data.object as Stripe.Invoice);
        break;
      case "invoice.payment_succeeded":
        await handleInvoicePaymentSucceeded(event.data.object as Stripe.Invoice);
        break;
      default:
        // Aceita silenciosamente para não causar retries no Stripe.
        break;
    }
    return new Response(JSON.stringify({ received: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  } catch (err) {
    console.error("handler error:", err);
    // 500 para o Stripe retentar.
    return new Response("handler error", { status: 500 });
  }
});
