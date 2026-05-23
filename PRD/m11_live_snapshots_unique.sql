-- BI TopStep — M11 — UNIQUE em live_snapshots (idempotencia)
-- Rodar 1x no Supabase SQL Editor, apos m6_live_snapshots.sql.
--
-- Motivacao (security review pos-Fase 4, item M-3):
-- - Edge Function `live-ingest` faz INSERT a cada heartbeat (30s) + push
--   por evento da extensao. Sem UNIQUE, retries de rede ou reinstall da
--   extensao podem inflar duplicatas com `snapshot_at` identico.
-- - UNIQUE (user_id, account_id, snapshot_at) permite que a Edge Function
--   troque INSERT por UPSERT (`ON CONFLICT DO NOTHING`) e fique idempotente.
--
-- IDEMPOTENTE: idempotente em si — `add constraint if not exists` nao existe
-- nativo, por isso o DO block.

set client_min_messages = warning;

do $u$
begin
    if not exists (
        select 1 from pg_constraint
         where conname = 'live_snapshots_uniq'
           and conrelid = 'public.live_snapshots'::regclass
    ) then
        alter table public.live_snapshots
            add constraint live_snapshots_uniq
            unique (user_id, account_id, snapshot_at);
    end if;
end $u$;
