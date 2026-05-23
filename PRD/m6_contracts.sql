-- BI TopStep — M6 — Tabela contracts (catalogo multi-contrato)
-- Rodar 1x no Supabase SQL Editor, apos schema.sql + saas_schema.sql.
--
-- Substitui o dict POINT_VALUE_USD hardcoded em src/daily_plan.py. Permite
-- adicionar novos contratos sem deploy do app. Lido com RLS aberta (qualquer
-- usuario autenticado pode ler; escrita apenas via service_role/SQL Editor).

create table if not exists public.contracts (
    symbol           text primary key,
    description      text not null,
    point_value_usd  numeric(12,4) not null,
    tick_size        numeric(12,6) not null,
    currency         text not null default 'USD',
    is_micro         boolean not null default false,
    created_at       timestamptz not null default now()
);

-- Seed de 12 simbolos (micros + full-size dos indices/energia/ouro).
-- Source: especificacoes CME/NYMEX/COMEX.
insert into public.contracts (symbol, description, point_value_usd, tick_size, is_micro) values
    ('MNQ', 'Micro E-mini Nasdaq-100',     2.00, 0.25, true),
    ('NQ',  'E-mini Nasdaq-100',          20.00, 0.25, false),
    ('MES', 'Micro E-mini S&P 500',        5.00, 0.25, true),
    ('ES',  'E-mini S&P 500',             50.00, 0.25, false),
    ('M2K', 'Micro E-mini Russell 2000',   5.00, 0.10, true),
    ('RTY', 'E-mini Russell 2000',        50.00, 0.10, false),
    ('MYM', 'Micro E-mini Dow',            0.50, 1.00, true),
    ('YM',  'E-mini Dow',                  5.00, 1.00, false),
    ('MCL', 'Micro WTI Crude Oil',         1.00, 0.01, true),
    ('CL',  'WTI Crude Oil',              10.00, 0.01, false),
    ('MGC', 'Micro Gold',                  1.00, 0.10, true),
    ('GC',  'Gold',                       10.00, 0.10, false)
on conflict (symbol) do nothing;

alter table public.contracts enable row level security;
drop policy if exists contracts_read_all on public.contracts;
create policy contracts_read_all on public.contracts
    for select
    using (true);
