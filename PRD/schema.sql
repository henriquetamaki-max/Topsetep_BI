-- BI TopStep — schema multi-tenant com isolamento por usuário (Supabase Auth + RLS)
-- Rodar 1x no Supabase SQL Editor.
-- ATENÇÃO: o DROP abaixo apaga a tabela trades existente. Como ela está vazia
-- (nenhum dado real ainda), é seguro. Se já houver dados em produção, remova o
-- DROP e migre `user_id` manualmente antes de aplicar a PK composta.

drop table if exists public.trades cascade;

create table public.trades (
    user_id         uuid not null references auth.users(id) on delete cascade,
    id              bigint not null,
    contract_name   text not null,
    entered_at      timestamptz not null,
    exited_at       timestamptz not null,
    entry_price     numeric(18,6) not null,
    exit_price      numeric(18,6) not null,
    fees            numeric(18,4) not null default 0,
    commissions     numeric(18,4) not null default 0,
    pnl             numeric(18,4) not null,
    size            integer not null,
    type            text not null check (type in ('Long','Short')),
    trade_day       date not null,
    trade_duration  interval not null,
    pnl_net         numeric(18,4) generated always as (pnl - fees - commissions) stored,
    ingested_at     timestamptz not null default now(),
    primary key (user_id, id)
);

create index if not exists trades_user_day_idx  on public.trades (user_id, trade_day);
create index if not exists trades_user_contract_idx on public.trades (user_id, contract_name);

-- Pontos por trade (Long: exit - entry; Short: entry - exit).
alter table public.trades
    add column if not exists points numeric(18,6)
    generated always as (
        case when type = 'Long'  then exit_price - entry_price
             when type = 'Short' then entry_price - exit_price
        end
    ) stored;

-- RLS: cada usuário vê e escreve apenas suas próprias linhas.
alter table public.trades enable row level security;
drop policy if exists trades_owner_all on public.trades;
create policy trades_owner_all on public.trades
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);


-- Análises do Coach (mesma lógica de isolamento)
create table if not exists public.coach_analyses (
    id              bigserial primary key,
    user_id         uuid not null references auth.users(id) on delete cascade,
    created_at      timestamptz not null default now(),
    period_start    date not null,
    period_end      date not null,
    contracts       text[] not null,
    types           text[] not null,
    weekdays        text[] not null,
    result_filter   text not null,
    response_text   text not null
);

-- Para bases pré-existentes sem user_id, adicionar coluna (idempotente):
alter table public.coach_analyses
    add column if not exists user_id uuid references auth.users(id) on delete cascade;

create index if not exists coach_analyses_user_created_idx on public.coach_analyses (user_id, created_at desc);
create index if not exists coach_analyses_contracts_idx   on public.coach_analyses using gin (contracts);

alter table public.coach_analyses enable row level security;
drop policy if exists coach_analyses_owner_all on public.coach_analyses;
create policy coach_analyses_owner_all on public.coach_analyses
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);


-- Plano de Ação (mesma lógica de isolamento)
create table if not exists public.action_items (
    id           bigserial primary key,
    user_id      uuid not null references auth.users(id) on delete cascade,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    task         text not null,
    status       text not null default 'Pendente'
                 check (status in ('Pendente','Em andamento','Concluído')),
    priority     text not null default 'Média'
                 check (priority in ('Alta','Média','Baixa')),
    done         boolean not null default false,
    due_date     date
);

alter table public.action_items
    add column if not exists user_id uuid references auth.users(id) on delete cascade;

create index if not exists action_items_user_status_idx   on public.action_items (user_id, status);
create index if not exists action_items_user_due_date_idx on public.action_items (user_id, due_date);

alter table public.action_items enable row level security;
drop policy if exists action_items_owner_all on public.action_items;
create policy action_items_owner_all on public.action_items
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
