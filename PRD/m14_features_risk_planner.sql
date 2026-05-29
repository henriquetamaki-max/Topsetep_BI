-- BI TopStep / X-Metrics — M14 — Feature flag risk_planner nos planos
-- Rodar 1x no Supabase SQL Editor, após saas_schema.sql + m9_features.sql.
--
-- Marca os planos `pro` (+ `admin`/`trial` para testes) com a feature
-- `risk_planner`. Usado por billing.has_feature(plan, "risk_planner") em app.py
-- para mostrar/esconder a aba Risk Planner. Mesmo padrão de m9_features.sql.
--
-- Idempotente: jsonb || merge sobrescreve apenas a chave risk_planner.

update public.plans
   set features = features || '{"risk_planner": true}'::jsonb
 where slug in ('pro', 'admin', 'trial');

-- Garante que basic/free NAO tem risk_planner.
update public.plans
   set features = features - 'risk_planner'
 where slug in ('basic', 'free');
