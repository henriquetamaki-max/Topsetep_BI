-- BI TopStep — M5 — Daily Plans (plano matinal por sessão)
-- Rodar 1x no Supabase SQL Editor, após schema.sql e saas_schema.sql.
--
-- Contexto: fusão com Trade_Agent trouxe o conceito de "plano matinal" para
-- detectar adições não-planejadas. Cada linha representa o que o trader
-- pretende fazer em um contrato/direção específico em uma data específica.
-- Operações reais (em public.trades) serão confrontadas contra estas linhas
-- em metrics.compute_plan_adherence() para gerar score de aderência.
--
-- ATENÇÃO: o DROP TABLE abaixo apaga daily_plans existente. Como esta tabela
-- ainda não foi colocada em produção (M5 acabou de nascer), é seguro. Se já
-- houver dados reais em outro ambiente, comente a linha do DROP antes de rodar.

-- =========================================================================
-- daily_plans — plano matinal do trader
-- =========================================================================
drop table if exists public.daily_plans cascade;

create table public.daily_plans (
    id              bigserial primary key,
    user_id         uuid not null references auth.users(id) on delete cascade,
    plan_date       date not null,
    contract_name   text not null,
    direction       text not null check (direction in ('Long','Short')),
    max_size        integer not null check (max_size > 0),
    entry_trigger   text,
    stop_points     numeric(18,6),
    target_points   numeric(18,6),
    notes           text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (user_id, plan_date, contract_name, direction)
);

create index if not exists daily_plans_user_date_idx
    on public.daily_plans (user_id, plan_date);

alter table public.daily_plans enable row level security;
drop policy if exists daily_plans_owner_all on public.daily_plans;
create policy daily_plans_owner_all on public.daily_plans
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
