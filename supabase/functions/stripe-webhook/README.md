# Edge Function — `stripe-webhook`

Recebe webhooks do Stripe e sincroniza a tabela `public.subscriptions` no
Supabase. Faz parte do M4 do roadmap SaaS.

## Eventos tratados

| Evento Stripe | Efeito em `subscriptions` |
|---|---|
| `checkout.session.completed` | Linka `stripe_customer_id` + `stripe_subscription_id` ao `user_id` (vindo do `metadata.user_id` da Checkout Session). |
| `customer.subscription.created` | Upsert por `user_id`: define `plan_slug`, `status`, `current_period_*`, `cancel_at_period_end`. |
| `customer.subscription.updated` | Mesmo upsert (mudança de plano, renovação, cancelamento agendado). |
| `customer.subscription.deleted` | `status = 'canceled'`, `cancel_at_period_end = false`. |
| `invoice.payment_failed` | `status = 'past_due'`. |
| `invoice.payment_succeeded` | Re-upsert via `handleSubscriptionChange` (restaura `active` + período). |

Outros eventos retornam 200 sem ação (evita retries inúteis).

## Mapeamentos

- **Stripe `price_id` → `plan_slug`:** definido por env (`STRIPE_PRICE_BASIC`,
  `STRIPE_PRICE_PRO`). Preço desconhecido emite warn e pula upsert.
- **Stripe `status` → enum `subscription_status`:**
  - `active`, `trialing` → `active`
  - `past_due`, `unpaid` → `past_due`
  - `canceled`, `incomplete_expired` → `canceled`
  - default → `past_due` (defensivo).

## Segurança

- Endpoint público (sem JWT) — `verify_jwt = false` em `supabase/config.toml`.
- Validação HMAC obrigatória via `stripe.webhooks.constructEventAsync()` +
  `STRIPE_WEBHOOK_SECRET`.
- Cliente Supabase criado com `SUPABASE_SERVICE_ROLE_KEY` (bypassa RLS — webhook
  é o único caminho legítimo de escrita em `subscriptions`).
- Idempotência por upsert (`onConflict: "user_id"`). Não há tabela de eventos
  processados — reprocessamento de um mesmo `event.id` re-aplica o mesmo estado
  (idempotente em nível de dado, não de operação).

## Secrets necessários

Configurar via `supabase secrets set ...` (ver `.env.example` na mesma pasta).

| Secret | Origem | Obrigatório |
|---|---|---|
| `STRIPE_SECRET_KEY` | Stripe Dashboard → Developers → API keys | sim |
| `STRIPE_WEBHOOK_SECRET` | Stripe Dashboard → Webhooks → endpoint → Signing secret | sim |
| `STRIPE_PRICE_BASIC` | Stripe Dashboard → Products → Basic → Price ID | sim |
| `STRIPE_PRICE_PRO` | Stripe Dashboard → Products → Pro → Price ID | sim |
| `SUPABASE_URL` | injetado pelo runtime Supabase | auto |
| `SUPABASE_SERVICE_ROLE_KEY` | injetado pelo runtime Supabase | auto |

## Deploy

```bash
# Login (1x)
supabase login

# Linkar projeto (1x, na raiz do repo)
supabase link --project-ref <project-ref>

# Setar secrets (1x, ou quando rotacionar)
supabase secrets set STRIPE_SECRET_KEY=sk_live_...
supabase secrets set STRIPE_WEBHOOK_SECRET=whsec_...
supabase secrets set STRIPE_PRICE_BASIC=price_...
supabase secrets set STRIPE_PRICE_PRO=price_...

# Deploy
supabase functions deploy stripe-webhook --no-verify-jwt
```

URL pública: `https://<project-ref>.supabase.co/functions/v1/stripe-webhook`.
Cadastrar essa URL no Stripe Dashboard → Webhooks com os eventos listados acima.

## Teste local

```bash
# Terminal 1 — sobe a função local
supabase functions serve stripe-webhook --env-file supabase/functions/stripe-webhook/.env

# Terminal 2 — forwarda eventos do Stripe test mode para localhost
stripe listen --forward-to http://localhost:54321/functions/v1/stripe-webhook

# Terminal 3 — dispara eventos sintéticos
stripe trigger checkout.session.completed
stripe trigger customer.subscription.updated
stripe trigger invoice.payment_failed
```

Validar:
1. Linha em `public.subscriptions` espelha o estado esperado.
2. Logs da função (`supabase functions logs stripe-webhook`) mostram event.type e nenhum `Error:`.
3. UI do app (`/account`) reflete o novo plano após reload.

## Backlog (não bloqueia M4)

- Idempotência por `event.id` (tabela `stripe_webhook_events` com PK `event_id`).
- Eventos adicionais: refunds, disputes, payment method updates.
- Logging estruturado JSON (hoje é `console.warn/error` text).
- Testes automatizados (fixtures Stripe + `deno test`).
- Alerta quando taxa de `payment_failed` ultrapassa threshold.
