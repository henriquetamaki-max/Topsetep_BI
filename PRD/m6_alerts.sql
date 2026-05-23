-- BI TopStep — M6 — Tabela alerts + enums
-- Rodar 1x no Supabase SQL Editor, apos m6_live_snapshots.sql.
--
-- Eventos gerados pelo Risk Guard (trigger em live_snapshots — ver M10),
-- por metricas batch ou manualmente. Ciclo de vida: created -> read_at
-- -> dismissed_at. RLS isola por user_id.
--
-- ATENCAO: o enum alert_severity tambem e' usado por tilt_patterns
-- (m6_tilt_patterns). Aplicar este arquivo ANTES do tilt_patterns.
--
-- ATENCAO 2: DROP TABLE abaixo apaga alerts existente (esperado vazio).

set client_min_messages = warning;

-- Enums (idempotente — preserva valores existentes em ambientes que ja
-- rodaram). NAO sao dropados mesmo que a tabela seja.
do $enums$
begin
    if not exists (select 1 from pg_type where typname = 'alert_type') then
        create type public.alert_type as enum (
            'daily_loss_limit',
            'trailing_drawdown',
            'max_position_size',
            'unplanned_addition',
            'plan_unplanned_contract',
            'manual'
        );
    end if;
    if not exists (select 1 from pg_type where typname = 'alert_severity') then
        create type public.alert_severity as enum ('info','warn','critical');
    end if;
    if not exists (select 1 from pg_type where typname = 'alert_source') then
        create type public.alert_source as enum ('risk_guard','metric','manual');
    end if;
end $enums$;

drop table if exists public.alerts cascade;

create table public.alerts (
    id            bigserial primary key,
    user_id       uuid not null references auth.users(id) on delete cascade,
    created_at    timestamptz not null default now(),
    alert_type    public.alert_type not null,
    severity      public.alert_severity not null default 'warn',
    source        public.alert_source not null default 'risk_guard',
    title         text not null,
    body          text,
    payload       jsonb,
    read_at       timestamptz,
    dismissed_at  timestamptz
);

create index alerts_user_created_idx
    on public.alerts (user_id, created_at desc);
create index alerts_unread_idx
    on public.alerts (user_id) where read_at is null;

alter table public.alerts enable row level security;
create policy alerts_owner_all on public.alerts
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
