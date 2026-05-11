-- BI TopStep — tabela de trades
-- Rodar 1x no Supabase SQL Editor
-- ATENÇÃO: o DROP abaixo apaga a tabela trades existente. Como ela está vazia
-- (nenhum dado real ainda), é seguro. Se já houver dados em produção, remova o DROP.

drop table if exists public.trades cascade;

create table public.trades (
    id              bigint primary key,
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
    ingested_at     timestamptz not null default now()
);

create index if not exists trades_trade_day_idx on public.trades (trade_day);
create index if not exists trades_contract_idx  on public.trades (contract_name);

-- Pontos por trade (Long: exit - entry; Short: entry - exit).
-- Coluna gerada: o Postgres calcula automaticamente, qualquer consumidor enxerga.
alter table public.trades
    add column if not exists points numeric(18,6)
    generated always as (
        case when type = 'Long'  then exit_price - entry_price
             when type = 'Short' then entry_price - exit_price
        end
    ) stored;
