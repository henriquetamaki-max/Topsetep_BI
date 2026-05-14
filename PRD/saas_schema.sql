-- BI TopStep — Schema SaaS (planos, assinaturas, trial, admin)
-- Rodar 1x no Supabase SQL Editor, após schema.sql já estar aplicado.
-- Idempotente: pode rodar de novo em ambientes que já têm parte das estruturas.

-- =========================================================================
-- 1) plans — catálogo de planos (público, qualquer usuário autenticado lê)
-- =========================================================================
create table if not exists public.plans (
    slug                  text primary key,
    name                  text not null,
    stripe_price_id       text,                          -- null para trial/free
    monthly_trade_limit   integer,                       -- null = ilimitado; 0 = nenhum
    price_cents           integer not null default 0,
    currency              text not null default 'USD',
    features              jsonb not null default '{}'::jsonb,
    is_public             boolean not null default true,
    sort_order            integer not null default 0,
    created_at            timestamptz not null default now()
);

alter table public.plans enable row level security;
drop policy if exists plans_read_all on public.plans;
create policy plans_read_all on public.plans
    for select
    using (true);

-- Seed dos 4 planos públicos. ON CONFLICT garante idempotência.
insert into public.plans (slug, name, stripe_price_id, monthly_trade_limit, price_cents, currency, features, sort_order)
values
    ('trial', '14-day Trial', null, null, 0, 'USD',
        '{"import": true,  "coach": true,  "dashboard": "full"}'::jsonb, 0),
    ('free',  'Free (pós-trial)', null, 0, 0, 'USD',
        '{"import": false, "coach": false, "dashboard": "limited_30d"}'::jsonb, 1),
    ('basic', 'Basic', null, 100, 1900, 'USD',
        '{"import": true,  "coach": true,  "dashboard": "full"}'::jsonb, 2),
    ('pro',   'Pro',   null, null, 4900, 'USD',
        '{"import": true,  "coach": true,  "dashboard": "full"}'::jsonb, 3)
on conflict (slug) do nothing;

-- Plano interno do gestor da plataforma. `is_public=false` esconde do grid
-- de upgrade na aba Account. Atribuído via override em current_user_plan()
-- quando o usuário corrente está em public.admin_users (ver seção 4).
insert into public.plans (slug, name, stripe_price_id, monthly_trade_limit, price_cents, currency, features, is_public, sort_order)
values
    ('admin', 'Administrator', null, null, 0, 'USD',
        '{"import": true, "coach": true, "dashboard": "full"}'::jsonb, false, 99)
on conflict (slug) do nothing;


-- =========================================================================
-- 2) subscriptions — 1 linha por usuário, gerenciada pelo webhook Stripe
-- =========================================================================
do $$
begin
    if not exists (select 1 from pg_type where typname = 'subscription_status') then
        create type public.subscription_status as enum
            ('trialing', 'active', 'past_due', 'canceled', 'free');
    end if;
end $$;

create table if not exists public.subscriptions (
    user_id                  uuid primary key references auth.users(id) on delete cascade,
    plan_slug                text not null references public.plans(slug),
    status                   public.subscription_status not null default 'trialing',
    stripe_customer_id       text unique,
    stripe_subscription_id   text unique,
    current_period_start     timestamptz,
    current_period_end       timestamptz,
    trial_ends_at            timestamptz,
    cancel_at_period_end     boolean not null default false,
    created_at               timestamptz not null default now(),
    updated_at               timestamptz not null default now()
);

create index if not exists subscriptions_status_idx
    on public.subscriptions (status);
create index if not exists subscriptions_stripe_customer_idx
    on public.subscriptions (stripe_customer_id);

-- RLS: usuário lê só a própria linha. INSERT/UPDATE/DELETE só via service_role
-- (Edge Function do Stripe webhook), portanto não criamos policy de escrita.
alter table public.subscriptions enable row level security;
drop policy if exists subscriptions_select_own on public.subscriptions;
create policy subscriptions_select_own on public.subscriptions
    for select
    using (auth.uid() = user_id);

-- updated_at automático
create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at := now();
    return new;
end $$;

drop trigger if exists subscriptions_touch_updated_at on public.subscriptions;
create trigger subscriptions_touch_updated_at
    before update on public.subscriptions
    for each row execute function public.touch_updated_at();


-- =========================================================================
-- 3) Provisão automática de trial ao criar usuário em auth.users
-- =========================================================================
create or replace function public.handle_new_user_subscription()
returns trigger language plpgsql security definer set search_path = public as $$
begin
    insert into public.subscriptions (user_id, plan_slug, status, trial_ends_at)
    values (new.id, 'trial', 'trialing', new.created_at + interval '14 days')
    on conflict (user_id) do nothing;
    return new;
end $$;

drop trigger if exists on_auth_user_created_subscription on auth.users;
create trigger on_auth_user_created_subscription
    after insert on auth.users
    for each row execute function public.handle_new_user_subscription();


-- =========================================================================
-- 4) RPC current_user_plan() — resolve plano efetivo (degrada trial→free)
--    Usada pelo billing.py em todas as checagens de feature/limite.
-- =========================================================================
create or replace function public.current_user_plan(p_user uuid default auth.uid())
returns table (
    plan_slug              text,
    status                 public.subscription_status,
    monthly_trade_limit    integer,
    current_period_start   timestamptz,
    current_period_end     timestamptz,
    trial_ends_at          timestamptz,
    features               jsonb
)
language sql stable security invoker as $$
    with s as (
        select * from public.subscriptions where user_id = p_user
    ),
    is_admin as (
        select exists (select 1 from public.admin_users a where a.user_id = p_user) as v
    ),
    eff as (
        -- LEFT JOIN com is_admin garante 1 linha mesmo se `s` estiver vazio
        -- (usuário sem subscriptions), permitindo detectar admin sem trial.
        select
            case
                when ia.v                                              then 'admin'
                when s.status = 'trialing' and s.trial_ends_at < now() then 'free'
                when s.status = 'canceled'                              then 'free'
                else s.plan_slug
            end as plan_slug,
            case
                when ia.v                                              then 'active'::public.subscription_status
                when s.status = 'trialing' and s.trial_ends_at < now() then 'free'::public.subscription_status
                when s.status = 'canceled'                              then 'free'::public.subscription_status
                else s.status
            end as status,
            s.current_period_start,
            s.current_period_end,
            s.trial_ends_at
        from is_admin ia
        left join s on true
    )
    select
        e.plan_slug,
        e.status,
        p.monthly_trade_limit,
        e.current_period_start,
        e.current_period_end,
        e.trial_ends_at,
        p.features
    from eff e
    join public.plans p on p.slug = e.plan_slug;
$$;


-- =========================================================================
-- 5) RPC ensure_subscription() — safety net pós-login (caso trigger falhe)
-- =========================================================================
create or replace function public.ensure_subscription()
returns void
language plpgsql security definer set search_path = public as $$
declare
    v_user uuid := auth.uid();
    v_created timestamptz;
begin
    if v_user is null then
        return;
    end if;
    if exists (select 1 from public.subscriptions where user_id = v_user) then
        return;
    end if;
    select created_at into v_created from auth.users where id = v_user;
    insert into public.subscriptions (user_id, plan_slug, status, trial_ends_at)
    values (v_user, 'trial', 'trialing', coalesce(v_created, now()) + interval '14 days')
    on conflict (user_id) do nothing;
end $$;


-- =========================================================================
-- 6) admin_users — allowlist do painel admin
-- =========================================================================
create table if not exists public.admin_users (
    user_id     uuid primary key references auth.users(id) on delete cascade,
    created_at  timestamptz not null default now()
);

alter table public.admin_users enable row level security;
drop policy if exists admin_users_select_own on public.admin_users;
create policy admin_users_select_own on public.admin_users
    for select
    using (auth.uid() = user_id);

-- Seed manual após signup do gestor da plataforma (rodar 1x no SQL editor).
-- Substituir pelo email do gestor:
--   insert into public.admin_users (user_id)
--   select id from auth.users where email = 'henrique.tamaki@gmail.com'
--   on conflict do nothing;

-- Helper para checar admin em qualquer RPC SECURITY DEFINER
create or replace function public.is_admin(p_user uuid default auth.uid())
returns boolean
language sql stable security invoker as $$
    select exists (select 1 from public.admin_users where user_id = p_user);
$$;


-- =========================================================================
-- 7) feature_flags — flags globais e per-user (para releases progressivos)
-- =========================================================================
create table if not exists public.feature_flags (
    key         text not null,
    user_id     uuid references auth.users(id) on delete cascade,
    enabled     boolean not null default false,
    created_at  timestamptz not null default now(),
    -- chave composta: para flags globais, user_id é NULL; PK precisa lidar com NULL.
    -- Por isso usamos um índice único parcial em vez de PK direta.
    constraint feature_flags_unique unique (key, user_id)
);
create unique index if not exists feature_flags_global_unique
    on public.feature_flags (key) where user_id is null;

alter table public.feature_flags enable row level security;
drop policy if exists feature_flags_read on public.feature_flags;
create policy feature_flags_read on public.feature_flags
    for select
    using (user_id is null or auth.uid() = user_id);


-- =========================================================================
-- 8) View admin_mrr — métricas agregadas para o painel admin
--    Só service_role e RPCs SECURITY DEFINER acessam (RLS bloqueia leitura
--    direta por usuário comum porque a view não tem user_id).
-- =========================================================================
create or replace view public.admin_mrr as
select
    s.plan_slug,
    count(*) filter (where s.status = 'active')   as active_subs,
    count(*) filter (where s.status = 'trialing') as trialing,
    count(*) filter (where s.status = 'past_due') as past_due,
    sum(case when s.status = 'active' then p.price_cents else 0 end)::numeric / 100.0 as mrr_usd
from public.subscriptions s
join public.plans p on p.slug = s.plan_slug
group by s.plan_slug;


-- =========================================================================
-- 9) RPC admin_list_users() — usada pelo painel admin
--    SECURITY DEFINER: checa is_admin() no corpo.
-- =========================================================================
create or replace function public.admin_list_users()
returns table (
    user_id              uuid,
    email                text,
    created_at           timestamptz,
    plan_slug            text,
    status               public.subscription_status,
    trial_ends_at        timestamptz,
    current_period_end   timestamptz,
    stripe_customer_id   text
)
language plpgsql stable security definer set search_path = public as $$
begin
    if not public.is_admin(auth.uid()) then
        raise exception 'forbidden: admin only';
    end if;
    return query
        select
            u.id,
            u.email::text,
            u.created_at,
            s.plan_slug,
            s.status,
            s.trial_ends_at,
            s.current_period_end,
            s.stripe_customer_id
        from auth.users u
        left join public.subscriptions s on s.user_id = u.id
        order by u.created_at desc;
end $$;

create or replace function public.admin_mrr_snapshot()
returns setof public.admin_mrr
language plpgsql stable security definer set search_path = public as $$
begin
    if not public.is_admin(auth.uid()) then
        raise exception 'forbidden: admin only';
    end if;
    return query select * from public.admin_mrr;
end $$;

create or replace function public.admin_suspend_user(p_target uuid)
returns void
language plpgsql security definer set search_path = public as $$
begin
    if not public.is_admin(auth.uid()) then
        raise exception 'forbidden: admin only';
    end if;
    update public.subscriptions
       set status = 'canceled',
           cancel_at_period_end = true
     where user_id = p_target;
end $$;


-- =========================================================================
-- Notas operacionais
-- =========================================================================
-- - INSERT/UPDATE/DELETE em public.subscriptions só via service_role
--   (Edge Function supabase/functions/stripe-webhook). Nenhuma policy de
--   escrita é criada para usuários autenticados.
-- - Trial inicia em auth.users.created_at + 14 dias (trigger).
-- - Expiração de trial é resolvida em tempo de leitura por current_user_plan(),
--   sem necessidade de cron.
-- - Para semear o gestor da plataforma, rodar manualmente após o signup:
--     insert into public.admin_users (user_id)
--     select id from auth.users where email = 'henrique.tamaki@gmail.com'
--     on conflict do nothing;
--   current_user_plan() detecta a presença em admin_users e devolve o plano
--   'admin' com status 'active' — bypassa trial e libera todas as features.
