-- BI TopStep — M6 — Tabela payouts (rastreio manual de payouts TopStep)
-- Rodar 1x no Supabase SQL Editor.
--
-- CRUD manual pelo trader (sem integracao com API TopStep). Cada linha
-- representa 1 solicitacao de payout: requested_at -> paid_at -> status.
-- RLS isola por user_id. Reusa public.touch_updated_at() de saas_schema.sql.
--
-- ATENCAO: DROP TABLE abaixo apaga payouts existente (esperado vazio).

set client_min_messages = warning;

drop table if exists public.payouts cascade;

create table public.payouts (
    id            bigserial primary key,
    user_id       uuid not null references auth.users(id) on delete cascade,
    account_id    text,
    requested_at  date not null,
    paid_at       date,
    amount_usd    numeric(12,2) not null check (amount_usd >= 0),
    status        text not null default 'pending'
                  check (status in ('pending','approved','paid','denied')),
    notes         text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index payouts_user_requested_idx
    on public.payouts (user_id, requested_at desc);

alter table public.payouts enable row level security;
create policy payouts_owner_all on public.payouts
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create trigger payouts_touch_updated_at
    before update on public.payouts
    for each row execute function public.touch_updated_at();
