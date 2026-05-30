-- m14_subscriptions_index.sql
-- Backlog do audit RLS/schema (2026-05-30, audit #3).
--
-- subscriptions já tem índices (status) e (stripe_customer_id) para busca
-- administrativa, mas falta o composto (user_id, status) — o padrão de query
-- mais comum no app é "minha assinatura ativa" (filtra por auth.uid()=user_id
-- + status in ('active','trialing')). Sem o composto, o planner cai no índice
-- de status e filtra user_id em memória.
--
-- Idempotente: create index if not exists. Sem risco de dados (só metadado).

create index if not exists subscriptions_user_status_idx
  on public.subscriptions (user_id, status);
