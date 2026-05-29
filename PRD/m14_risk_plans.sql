-- BI TopStep / X-Metrics — M14 — Risk Planner (plano de risco pré-trade)
-- Rodar 1x no Supabase SQL Editor, após schema.sql + saas_schema.sql + M5/M6.
--
-- Contexto: a release 3.0 muda o foco de risco retrospectivo (Risk Guard reativo
-- em live_snapshots) para PRÉ-TRADE. O trader informa saldo + limite de blowout
-- (MLL) da conta e o sistema simula, por ativo, sizing/stop/nº de trades/Monte
-- Carlo de blowout, gravando sugestões em daily_plans.
--
-- Duas tabelas:
--   risk_plans        — 1 linha por (user, plan_date): inputs do dia + snapshot
--                       agregado do resultado (Monte Carlo) em jsonb.
--   risk_plan_assets  — resultado por ativo (comparativo + seleção p/ Day Plan).
--
-- RLS por user_id (padrão do projeto). Reusa public.touch_updated_at()
-- (saas_schema.sql). Idempotente: usa `if not exists` + drop/recreate de policies
-- e triggers; NÃO dropa as tabelas (podem já ter dados de planejamento).

set client_min_messages = warning;

-- =========================================================================
-- risk_plans — header: 1 linha por (user, plan_date alvo)
-- =========================================================================
create table if not exists public.risk_plans (
    id                      bigserial primary key,
    user_id                 uuid not null references auth.users(id) on delete cascade,
    plan_date               date not null,                       -- dia ALVO (amanhã, em ET)
    account_type            text,                                -- 'Express 50K'..'Custom'
    -- inputs manuais diários (foco da 3.0)
    balance_usd             numeric(14,2) not null check (balance_usd >= 0),
    mll_threshold_usd       numeric(14,2) not null check (mll_threshold_usd >= 0), -- nível de blowout
    daily_loss_limit_usd    numeric(12,2),                       -- pode herdar de risk_settings
    trailing_mode           text not null default 'combine'
                              check (trailing_mode in ('combine','xfa')),
    -- política de risco por trade
    risk_mode               text not null default 'pct'
                              check (risk_mode in ('pct','usd')),
    risk_value              numeric(12,4) not null check (risk_value > 0), -- % (1.0) ou $ por trade
    -- parâmetros Monte Carlo
    win_rate                numeric(5,4) check (win_rate >= 0 and win_rate <= 1),
    avg_r                   numeric(8,4),                        -- R múltiplo médio do vencedor
    trades_per_day          integer check (trades_per_day > 0),
    horizon_days            integer check (horizon_days > 0),
    mc_simulations          integer not null default 10000 check (mc_simulations > 0),
    mc_seed                 integer not null default 42,
    -- snapshot agregado do resultado (probabilidades, percentis, regras)
    result_snapshot         jsonb,
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now(),
    unique (user_id, plan_date)
);

create index if not exists risk_plans_user_date_idx
    on public.risk_plans (user_id, plan_date);

alter table public.risk_plans enable row level security;
drop policy if exists risk_plans_owner_all on public.risk_plans;
create policy risk_plans_owner_all on public.risk_plans
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop trigger if exists risk_plans_touch_updated_at on public.risk_plans;
create trigger risk_plans_touch_updated_at
    before update on public.risk_plans
    for each row execute function public.touch_updated_at();

-- =========================================================================
-- risk_plan_assets — resultado por ativo (1 linha por contrato/direção)
-- =========================================================================
create table if not exists public.risk_plan_assets (
    id                  bigserial primary key,
    risk_plan_id        bigint not null references public.risk_plans(id) on delete cascade,
    user_id             uuid not null references auth.users(id) on delete cascade, -- desnorm. p/ RLS
    contract_name       text not null,
    direction           text not null default 'Long' check (direction in ('Long','Short')),
    max_contracts       integer not null check (max_contracts >= 0),
    risk_usd            numeric(14,2),     -- $ arriscado por trade neste ativo
    max_stop_points     numeric(18,6),     -- stop em pontos p/ max_contracts
    n_trades_to_dll     integer,           -- trades até bater o DLL
    p_blowout           numeric(6,5),      -- P(atingir MLL) do Monte Carlo (opcional)
    selected            boolean not null default false, -- marcado p/ gravar no daily_plans
    created_at          timestamptz not null default now(),
    unique (risk_plan_id, contract_name, direction)
);

create index if not exists risk_plan_assets_plan_idx
    on public.risk_plan_assets (risk_plan_id);
create index if not exists risk_plan_assets_user_idx
    on public.risk_plan_assets (user_id);

alter table public.risk_plan_assets enable row level security;
drop policy if exists risk_plan_assets_owner_all on public.risk_plan_assets;
create policy risk_plan_assets_owner_all on public.risk_plan_assets
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
