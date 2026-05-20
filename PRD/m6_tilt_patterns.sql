-- BI TopStep — M6 — Tabela tilt_patterns (historico de deteccoes de padroes)
-- Rodar 1x no Supabase SQL Editor, apos m6_alerts.sql (reusa enum alert_severity).
--
-- 1 linha = 1 padrao detectado num momento. Permite analise historica de
-- tilt por trader (trend de revenge trading, overtrading, etc.). Population
-- inicial vem do compute_coach em metrics.py.
--
-- ATENCAO: DROP TABLE abaixo apaga tilt_patterns existente (esperado vazio).

set client_min_messages = warning;

do $enum$
begin
    if not exists (select 1 from pg_type where typname = 'tilt_pattern_type') then
        create type public.tilt_pattern_type as enum (
            'revenge',
            'overtrading',
            'cut_winners_hold_losers',
            'losing_streak',
            'size_creep',
            'plan_deviation'
        );
    end if;
end $enum$;

drop table if exists public.tilt_patterns cascade;

create table public.tilt_patterns (
    id                 bigserial primary key,
    user_id            uuid not null references auth.users(id) on delete cascade,
    detected_at        timestamptz not null default now(),
    pattern_type       public.tilt_pattern_type not null,
    severity           public.alert_severity not null default 'warn',
    context            jsonb,
    related_trade_ids  bigint[],
    related_group_id   bigint
);

create index tilt_patterns_user_detected_idx
    on public.tilt_patterns (user_id, detected_at desc);

alter table public.tilt_patterns enable row level security;
create policy tilt_patterns_owner_all on public.tilt_patterns
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
