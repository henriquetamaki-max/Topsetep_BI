-- M15 — risk_reviews: snapshots da Avaliação de Risco (score por período).
-- Permite acompanhar a evolução do score ao longo do tempo.
--
-- Idempotente (create if not exists; sem DROP — preserva dados). RLS por user_id.
-- Rodar 1x no Supabase SQL Editor, DEPOIS de saas_schema.sql (usa o helper
-- public.touch_updated_at() criado lá).

create table if not exists public.risk_reviews (
    id              bigserial primary key,
    user_id         uuid not null references auth.users(id) on delete cascade,
    period_start    date not null,
    period_end      date not null,
    total_days      integer not null default 0,
    clean_days      integer not null default 0,
    score_pct       numeric(5,2) not null default 0,
    stop_furado     integer not null default 0,
    risco_excedido  integer not null default 0,
    dll_furado      integer not null default 0,
    blowout         integer not null default 0,
    snapshot        jsonb,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (user_id, period_start, period_end)
);

create index if not exists risk_reviews_user_period_idx
    on public.risk_reviews (user_id, period_end);

-- RLS: cada usuário vê e escreve apenas suas próprias linhas.
alter table public.risk_reviews enable row level security;
drop policy if exists risk_reviews_owner_all on public.risk_reviews;
create policy risk_reviews_owner_all on public.risk_reviews
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- updated_at automático no UPDATE (helper de saas_schema.sql).
drop trigger if exists touch_risk_reviews on public.risk_reviews;
create trigger touch_risk_reviews
    before update on public.risk_reviews
    for each row execute function public.touch_updated_at();
