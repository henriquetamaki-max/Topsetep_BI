-- BI TopStep — M6 — Tabela live_snapshots (estado da conta em tempo real)
-- Rodar 1x no Supabase SQL Editor, apos m6_contracts.sql.
--
-- Cada linha = um snapshot enviado pela extensao Chrome (a cada 30s
-- heartbeat + push imediato por evento). RLS isola por user_id. Coluna
-- `raw` jsonb guarda o payload completo para evolucao sem migration.
-- Retencao: 7 dias via pg_cron (instrucao de agendamento em m6_README.md).
--
-- ATENCAO: o DROP TABLE abaixo apaga qualquer live_snapshots existente
-- (esperado: tabela vazia ou de teste, sem dados de producao). Se ja
-- houver dados reais em outro ambiente, COMENTAR a linha do DROP antes
-- de rodar e fazer migracao manual. Mesmo padrao do m5_daily_plans.sql.

set client_min_messages = warning;

drop table if exists public.live_snapshots cascade;

-- ---------------------------------------------------------------------------
-- Tabela
-- ---------------------------------------------------------------------------
create table public.live_snapshots (
    id                 bigserial primary key,
    user_id            uuid not null references auth.users(id) on delete cascade,
    account_id         text not null,
    snapshot_at        timestamptz not null,
    position_size      integer not null default 0,
    position_avg_price numeric(18,6),
    position_contract  text,
    position_side      text check (position_side in ('Long','Short')),
    unrealized_pnl     numeric(18,4) not null default 0,
    realized_pnl       numeric(18,4) not null default 0,
    day_pnl            numeric(18,4) not null default 0,
    drawdown           numeric(18,4) not null default 0,
    raw                jsonb,
    created_at         timestamptz not null default now()
);

create index live_snapshots_user_at_idx
    on public.live_snapshots (user_id, snapshot_at desc);

alter table public.live_snapshots enable row level security;
create policy live_snapshots_owner_all on public.live_snapshots
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- Funcao de purge (> 7 dias). security definer para o pg_cron rodar como dona.
-- O agendamento (cron.schedule) e' feito separadamente — ver PRD/m6_README.md.
-- ---------------------------------------------------------------------------
drop function if exists public.purge_old_live_snapshots();
create function public.purge_old_live_snapshots()
returns void
language sql
security definer
set search_path = public
as $fn$
    delete from public.live_snapshots where snapshot_at < now() - interval '7 days';
$fn$;
