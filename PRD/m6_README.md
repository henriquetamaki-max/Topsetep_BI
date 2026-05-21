# M6 — Schemas Postgres da fusão Trade_Agent → BI TopStep

Sete migrations, aplicar **na ordem listada** no Supabase SQL Editor (após `schema.sql` + `saas_schema.sql` + `m5_daily_plans.sql` já estarem aplicados).

> **Atenção:** as 6 migrations de tabelas (todas exceto `m6_contracts.sql`) começam com `DROP TABLE ... CASCADE` para garantir convergência limpa do schema. **Isso apaga qualquer dado existente** nessas tabelas. Esperado: tabelas vazias (ambiente de fusão MVP). Se já houver dados reais em outro ambiente, comentar a linha do DROP e migrar manualmente. Mesmo padrão do `m5_daily_plans.sql`.

## Ordem de aplicação

1. `m6_contracts.sql` — Catálogo multi-contrato (substitui dict hardcoded em `daily_plan.py`).
2. `m6_live_snapshots.sql` — Snapshots da extensão Chrome + job `pg_cron` de purge 7 dias.
3. `m6_alerts.sql` — Tabela de alertas + 3 enums (`alert_type`, `alert_severity`, `alert_source`).
4. `m6_tilt_patterns.sql` — Histórico de detecções de padrões comportamentais (reusa `alert_severity` do passo 3).
5. `m6_payouts.sql` — CRUD manual de payouts TopStep.
6. `m6_risk_settings.sql` — Limites operacionais do Risk Guard (1 linha por usuário).
7. `m6_accounts.sql` — Mapeamento `account_id` TopStep → `user_id`.

Os arquivos 4–7 são independentes entre si, mas o 4 depende do enum criado no 3.

**Migrations subsequentes** (também aplicar nesta ordem):

8. `m9_features.sql` — habilita `features.live_monitor=true` em pro/admin/trial.
9. `m10_risk_guard_trigger.sql` — trigger `risk_guard_eval` em `live_snapshots`.
10. `m11_live_snapshots_unique.sql` — UNIQUE para idempotência da Edge Function `live-ingest`.

## Após aplicar as 7 migrations

No Supabase SQL Editor, rodar **uma vez** para agendar o purge de `live_snapshots`:

```sql
select cron.schedule(
    'purge_live_snapshots',
    '0 3 * * *',
    $$select public.purge_old_live_snapshots()$$
);
```

Verificar: `select * from cron.job where jobname='purge_live_snapshots';` deve retornar 1 linha.

## Verificação

```sql
select tablename
  from pg_tables
 where schemaname = 'public'
 order by tablename;
```

Deve listar: `accounts, action_items, admin_users, alerts, coach_analyses, contracts, daily_plans, feature_flags, live_snapshots, payouts, plans, risk_settings, subscriptions, tilt_patterns, trades`.

RLS sanity check (substituir `<uid>` por um user_id real):

```sql
set local "request.jwt.claim.sub" = '<uid>';
select count(*) from public.live_snapshots;
-- Deve retornar apenas linhas com user_id = <uid> (zero se nada inserido ainda).
```

## Próxima fase

Após Fase 1.3 (este arquivo), seguir para Fase 1.2 (dual-timezone + aba Configurações) — ver `../guia_execucao.md` seção 5.
