-- BI TopStep — M6 — Tabela accounts (mapeamento account_id TopStep -> user_id)
-- Rodar 1x no Supabase SQL Editor.
--
-- 1 conta ativa por usuario no MVP da fusao (flag active=true). Multiplas
-- contas ativas viram backlog. Usado para vincular live_snapshots,
-- alerts e payouts ao trader correto.
--
-- ATENCAO: DROP TABLE abaixo apaga accounts existente (esperado vazio).

set client_min_messages = warning;

drop table if exists public.accounts cascade;

create table public.accounts (
    id                 bigserial primary key,
    user_id            uuid not null references auth.users(id) on delete cascade,
    account_id         text not null,
    label              text,
    topstep_plan_size  text,
    active             boolean not null default true,
    created_at         timestamptz not null default now(),
    unique (user_id, account_id)
);

create index accounts_user_active_idx
    on public.accounts (user_id, active);

alter table public.accounts enable row level security;
create policy accounts_owner_all on public.accounts
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
