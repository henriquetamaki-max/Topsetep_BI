-- BI TopStep — M10 — Trigger risk_guard_eval em live_snapshots
-- Rodar 1x no Supabase SQL Editor, apos todas as migrations M6 + M9.
--
-- O trigger AFTER INSERT le risk_settings + daily_plans e cria linhas em
-- alerts conforme as 4 regras do MVP:
--
-- 1) Daily Loss Limit (DLL)
--    - day_pnl <= -dll  -> alerta critical "daily_loss_limit"
--    - day_pnl entre -warning_threshold% e -dll -> alerta warn (proximo do limite)
--
-- 2) Trailing Drawdown
--    - drawdown >= trailing_dd -> alerta critical "trailing_drawdown"
--    - drawdown >= trailing_dd * warning% -> alerta warn (proximo)
--
-- 3) Max Position Size
--    - position_size > risk_settings.max_position_size -> alerta warn
--
-- 4) Unplanned Addition
--    - position_size > daily_plans.max_size do dia (NY/ET) e direcao casa
--      -> alerta warn "unplanned_addition"
--
-- IDEMPOTENTE: drop function + drop trigger antes de criar. Pode reaplicar.
-- security definer para o trigger executar como dono mesmo quando o INSERT
-- vem do service_role da Edge Function live-ingest.
--
-- Anti-spam: cooldown de 5min por (user_id, alert_type) — evita criar 100
-- alertas DLL na mesma sequencia de snapshots. Backlog: ttl configuravel.

set client_min_messages = warning;

drop trigger if exists trg_risk_guard on public.live_snapshots;
drop function if exists public.risk_guard_eval();

create function public.risk_guard_eval()
returns trigger
language plpgsql
security definer
set search_path = public
as $fn$
declare
    rs              record;
    cooldown_min    constant int := 5;
    plan_max        int;
    today_et        date;
    warn_pct        numeric;
    warn_floor_dll  numeric;
    warn_floor_td   numeric;
begin
    select * into rs
      from public.risk_settings
     where user_id = new.user_id;

    if not found then
        return new;
    end if;

    warn_pct := coalesce(rs.warning_threshold_pct, 80.0);

    -- ----- 1) Daily Loss Limit -----
    if rs.daily_loss_limit_usd is not null and rs.daily_loss_limit_usd > 0 then
        warn_floor_dll := - rs.daily_loss_limit_usd * (warn_pct / 100.0);

        if new.day_pnl <= - rs.daily_loss_limit_usd then
            perform _rg_insert_alert(
                new.user_id, 'daily_loss_limit', 'critical', cooldown_min,
                'Daily Loss Limit atingido',
                'PnL do dia: ' || new.day_pnl::text ||
                ' USD (limite: -' || rs.daily_loss_limit_usd::text || ').',
                jsonb_build_object(
                    'snapshot_id', new.id,
                    'day_pnl', new.day_pnl,
                    'limit', rs.daily_loss_limit_usd
                )
            );
        elsif new.day_pnl <= warn_floor_dll then
            perform _rg_insert_alert(
                new.user_id, 'daily_loss_limit', 'warn', cooldown_min,
                'Aproximando do Daily Loss Limit',
                'PnL do dia: ' || new.day_pnl::text ||
                ' USD. Threshold de aviso: ' || warn_floor_dll::text || '.',
                jsonb_build_object(
                    'snapshot_id', new.id,
                    'day_pnl', new.day_pnl,
                    'limit', rs.daily_loss_limit_usd,
                    'warn_pct', warn_pct
                )
            );
        end if;
    end if;

    -- ----- 2) Trailing Drawdown -----
    if rs.trailing_drawdown_usd is not null and rs.trailing_drawdown_usd > 0 then
        warn_floor_td := rs.trailing_drawdown_usd * (warn_pct / 100.0);

        if new.drawdown >= rs.trailing_drawdown_usd then
            perform _rg_insert_alert(
                new.user_id, 'trailing_drawdown', 'critical', cooldown_min,
                'Trailing Drawdown atingido',
                'Drawdown: ' || new.drawdown::text ||
                ' USD (limite: ' || rs.trailing_drawdown_usd::text || ').',
                jsonb_build_object(
                    'snapshot_id', new.id,
                    'drawdown', new.drawdown,
                    'limit', rs.trailing_drawdown_usd
                )
            );
        elsif new.drawdown >= warn_floor_td then
            perform _rg_insert_alert(
                new.user_id, 'trailing_drawdown', 'warn', cooldown_min,
                'Aproximando do Trailing Drawdown',
                'Drawdown: ' || new.drawdown::text ||
                ' USD. Threshold de aviso: ' || warn_floor_td::text || '.',
                jsonb_build_object(
                    'snapshot_id', new.id,
                    'drawdown', new.drawdown,
                    'limit', rs.trailing_drawdown_usd,
                    'warn_pct', warn_pct
                )
            );
        end if;
    end if;

    -- ----- 3) Max Position Size (limite do trader em risk_settings) -----
    if rs.max_position_size is not null and rs.max_position_size > 0
       and new.position_size > rs.max_position_size then
        perform _rg_insert_alert(
            new.user_id, 'max_position_size', 'warn', cooldown_min,
            'Tamanho de posicao acima do limite',
            'Posicao atual: ' || new.position_size::text ||
            ' (limite configurado: ' || rs.max_position_size::text || ').',
            jsonb_build_object(
                'snapshot_id', new.id,
                'position_size', new.position_size,
                'limit', rs.max_position_size
            )
        );
    end if;

    -- ----- 4) Unplanned Addition (cruza com daily_plans em NY/ET) -----
    if new.position_contract is not null
       and new.position_side in ('Long','Short')
       and new.position_size > 0 then

        today_et := (new.snapshot_at at time zone 'America/New_York')::date;

        select max_size into plan_max
          from public.daily_plans
         where user_id = new.user_id
           and plan_date = today_et
           and contract_name = new.position_contract
           and direction = new.position_side;

        if plan_max is not null and new.position_size > plan_max then
            perform _rg_insert_alert(
                new.user_id, 'unplanned_addition', 'warn', cooldown_min,
                'Adicao nao-planejada detectada',
                'Posicao em ' || new.position_contract || ' (' || new.position_side ||
                ') = ' || new.position_size::text ||
                ' contratos, acima do plano matinal (' || plan_max::text || ').',
                jsonb_build_object(
                    'snapshot_id', new.id,
                    'position_size', new.position_size,
                    'plan_max_size', plan_max,
                    'contract', new.position_contract,
                    'side', new.position_side,
                    'plan_date', today_et
                )
            );
        end if;
    end if;

    return new;
end
$fn$;


-- Helper privado: insere alert respeitando cooldown por (user_id, alert_type).
-- Evita 100 alertas DLL consecutivos quando trader fica perto do limite.
drop function if exists public._rg_insert_alert(uuid, public.alert_type, public.alert_severity, int, text, text, jsonb);

create function public._rg_insert_alert(
    p_user_id      uuid,
    p_type         public.alert_type,
    p_severity     public.alert_severity,
    p_cooldown_min int,
    p_title        text,
    p_body         text,
    p_payload      jsonb
) returns void
language plpgsql
security definer
set search_path = public
as $hh$
declare
    last_at timestamptz;
begin
    select max(created_at) into last_at
      from public.alerts
     where user_id = p_user_id
       and alert_type = p_type
       and source = 'risk_guard';

    if last_at is not null
       and last_at > now() - make_interval(mins => p_cooldown_min) then
        return;  -- cooldown ativo
    end if;

    insert into public.alerts (user_id, alert_type, severity, source, title, body, payload)
    values (p_user_id, p_type, p_severity, 'risk_guard', p_title, p_body, p_payload);
end
$hh$;


create trigger trg_risk_guard
    after insert on public.live_snapshots
    for each row execute function public.risk_guard_eval();
