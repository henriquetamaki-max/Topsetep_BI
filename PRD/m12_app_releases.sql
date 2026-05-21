-- BI TopStep — M12 — Catalogo de releases (extensao Chrome / app)
-- Rodar 1x no Supabase SQL Editor, em qualquer ordem apos saas_schema.sql.
--
-- Motivacao:
-- - Hoje o operador empacota um .zip da extensao e sobe no Storage. Nao ha
--   forma de o trader saber qual versao esta' instalada vs qual e' a mais
--   recente — atualizacao silenciosa fica invisivel.
-- - Tabela app_releases serve como catalogo publico de versoes: trader le'
--   na aba Live "ultima versao disponivel: v0.1.0". Operador (admin) marca
--   uma release como `is_latest=true` para promove-la.
--
-- Dois componentes versionaveis: 'extension' (Chrome MV3) e 'app' (Streamlit).
-- Sem user_id: releases sao publicas. RLS permite SELECT a qualquer auth;
-- write apenas a quem estiver em public.admin_users.
--
-- IDEMPOTENTE: DROP + CREATE com cuidado pois nao ha dados reais ainda.

set client_min_messages = warning;

drop table if exists public.app_releases cascade;

create table public.app_releases (
    id              bigserial primary key,
    component       text not null check (component in ('extension', 'app')),
    version         text not null,
    released_at     timestamptz not null default now(),
    release_notes   text,
    download_url    text,
    is_latest       boolean not null default false,
    created_by      uuid references auth.users(id) on delete set null,
    unique (component, version)
);

-- Garante 1 unica linha latest=true por componente. Promover uma nova versao
-- significa UPDATE app_releases SET is_latest=false WHERE component=X
-- (transacao) e depois UPDATE app_releases SET is_latest=true WHERE id=N.
create unique index app_releases_one_latest_per_component
    on public.app_releases (component)
    where is_latest = true;

create index app_releases_component_released_at
    on public.app_releases (component, released_at desc);

alter table public.app_releases enable row level security;

-- Leitura: todos autenticados (RLS) — releases sao publicas.
create policy app_releases_read_all
    on public.app_releases
    for select
    to authenticated
    using (true);

-- Escrita: somente admin (linha em public.admin_users).
create policy app_releases_admin_write
    on public.app_releases
    for all
    to authenticated
    using (exists (select 1 from public.admin_users where user_id = auth.uid()))
    with check (exists (select 1 from public.admin_users where user_id = auth.uid()));

-- Seed: registra a versao atual da extensao (v0.1.0) como latest.
-- Operador (henrique) que rodar este SQL fica como `created_by` se logado.
insert into public.app_releases (component, version, release_notes, download_url, is_latest)
values (
    'extension',
    '0.1.0',
    'Versao inicial. Live Monitor TopstepX -> snapshots para BI TopStep.',
    null,  -- preencher manualmente com URL do .zip no Storage apos upload
    true
)
on conflict (component, version) do nothing;
