-- M16 — risk_day_notes: comentário/autoavaliação do trader por dia.
--
-- Idempotente (create if not exists; sem DROP). RLS por user_id. Rodar 1x no
-- Supabase SQL Editor, DEPOIS de saas_schema.sql (usa public.touch_updated_at()).

create table if not exists public.risk_day_notes (
    id          bigserial primary key,
    user_id     uuid not null references auth.users(id) on delete cascade,
    trade_day   date not null,
    comment     text not null default '',
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    unique (user_id, trade_day)
);

create index if not exists risk_day_notes_user_day_idx
    on public.risk_day_notes (user_id, trade_day);

alter table public.risk_day_notes enable row level security;
drop policy if exists risk_day_notes_owner_all on public.risk_day_notes;
create policy risk_day_notes_owner_all on public.risk_day_notes
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop trigger if exists touch_risk_day_notes on public.risk_day_notes;
create trigger touch_risk_day_notes
    before update on public.risk_day_notes
    for each row execute function public.touch_updated_at();
