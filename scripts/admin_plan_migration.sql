-- =========================================================================
-- Migração: plano "admin" para gestor da plataforma
-- Rodar no Supabase SQL Editor (idempotente — pode rodar de novo).
-- =========================================================================

-- 1) Seed do plano admin (caso ainda não exista)
insert into public.plans (slug, name, stripe_price_id, monthly_trade_limit, price_cents, currency, features, is_public, sort_order)
values ('admin', 'Administrator', null, null, 0, 'USD',
        '{"import": true, "coach": true, "dashboard": "full"}'::jsonb, false, 99)
on conflict (slug) do nothing;


-- 2) current_user_plan() com override admin
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
        -- LEFT JOIN com is_admin garante que mesmo se `s` estiver vazio
        -- (caso anômalo: usuário sem linha em subscriptions) o admin ainda
        -- é detectado e devolvido com plano `admin`/`active`.
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


-- 3) Garante o gestor em admin_users (idempotente)
insert into public.admin_users (user_id)
select id from auth.users where email = 'henrique.tamaki@gmail.com'
on conflict do nothing;


-- =========================================================================
-- Verificação (esperado: 1 linha, plan_slug='admin', status='active')
-- =========================================================================
select * from public.current_user_plan(
    (select id from auth.users where email = 'henrique.tamaki@gmail.com')
);
