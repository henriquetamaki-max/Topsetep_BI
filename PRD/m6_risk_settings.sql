-- BI TopStep — M6 — Tabela risk_settings (limites operacionais do Risk Guard)
-- Rodar 1x no Supabase SQL Editor.
--
-- 1 linha por usuario (PK = user_id). Sem linha = Risk Guard inativo.
-- Trigger risk_guard_eval (criado em M10) le esta tabela para decidir
-- quando inserir em public.alerts. Reusa public.touch_updated_at().
--
-- ATENCAO: DROP TABLE abaixo apaga risk_settings existente (esperado vazio).

set client_min_messages = warning;

drop table if exists public.risk_settings cascade;

create table public.risk_settings (
    user_id                uuid primary key references auth.users(id) on delete cascade,
    account_type           text,
    daily_loss_limit_usd   numeric(12,2),
    trailing_drawdown_usd  numeric(12,2),
    max_position_size      integer,
    warning_threshold_pct  numeric(5,2) not null default 80.0,
    updated_at             timestamptz not null default now()
);

alter table public.risk_settings enable row level security;
create policy risk_settings_owner_all on public.risk_settings
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create trigger risk_settings_touch_updated_at
    before update on public.risk_settings
    for each row execute function public.touch_updated_at();
