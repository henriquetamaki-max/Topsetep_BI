-- BI TopStep — M9 — Feature flag live_monitor nos planos
-- Rodar 1x no Supabase SQL Editor, apos saas_schema.sql.
--
-- Marca plano `pro` (e tambem `admin` + `trial` para testes) com a feature
-- `live_monitor`. Usado por billing.has_feature(plan, "live_monitor") em
-- account.py, settings.py e live.py para mostrar/esconder UI da extensao.
--
-- Idempotente: jsonb || merge sobrescreve apenas a chave live_monitor.

update public.plans
   set features = features || '{"live_monitor": true}'::jsonb
 where slug in ('pro', 'admin', 'trial');

-- Garante que basic/free NAO tem live_monitor (caso uma versao anterior tenha
-- setado por engano).
update public.plans
   set features = features - 'live_monitor'
 where slug in ('basic', 'free');
