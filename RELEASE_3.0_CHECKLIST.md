# Release 3.0 — Checklist de Deploy & QA (Risk Planner)

> Checklist enxuto para colocar o Risk Planner no ar. Linha 3.0 = branch `release/3.0` (worktree `E:\BD\X-Metrics 3.0`); 2.0 fica em `main` + tag `v2.0.0`. Código já implementado e testado (542 verdes); o que falta é deploy + validação manual.

## 0. Pré-requisitos

- Worktree `E:\BD\X-Metrics 3.0` na branch `release/3.0`.
- `.venv` e `Env/` configurados na worktree (ver setup abaixo se ausente).
- Acesso ao Supabase Dashboard do projeto `qjzouhdrhoxmtinidsqv`.
- `PRD/m6_contracts.sql` já aplicado (fonte do `point_value_usd`).

Setup da worktree (1x, se faltar `.venv`):

```
cd "E:\BD\X-Metrics 3.0"
```

```
python -m venv .venv
```

```
.venv\Scripts\activate
```

```
pip install -r requirements.txt
```

> Nota: o install completo pode falhar ao buildar `pyiceberg` (dep transitiva). Se ocorrer, instale o mínimo: `pip install streamlit supabase python-dotenv plotly numpy pandas stripe streamlit-autorefresh`.

## 1. Schema (Supabase SQL Editor)

Onde: Supabase Dashboard → SQL Editor.

1. Rodar o conteúdo de:

```
PRD/m14_risk_plans.sql
```

```
PRD/m14_features_risk_planner.sql
```

Esperado: tabelas `public.risk_plans` + `public.risk_plan_assets` criadas com RLS `owner_all`; planos `pro/admin/trial` com `features.risk_planner=true`.

Verificar:

```sql
select slug, features->'risk_planner' as rp from public.plans where slug in ('pro','basic');
```

Esperado: `pro` → `true`, `basic` → `null`.

```sql
select count(*) from information_schema.tables
 where table_schema='public' and table_name in ('risk_plans','risk_plan_assets');
```

Esperado: `2`.

Troubleshooting:
- `function public.touch_updated_at() does not exist` → rodar `PRD/saas_schema.sql` antes.
- `relation "contracts" ... empty` na aba → rodar `PRD/m6_contracts.sql`.
- Aba Risk Planner não aparece → confirmar `features.risk_planner=true` no plano do usuário logado (query acima).

## 2. Testes (worktree 3.0)

```
.venv\Scripts\python.exe -m pytest tests/ -q
```

Esperado: `542 passed` (37 `test_risk_engine` + 8 `test_risk_plan` + suíte existente).

## 3. Smoke E2E (golden path no browser)

Onde: `streamlit run src/app.py` na worktree 3.0, logado com plano Pro.

1. Aba **Risk Planner** aparece.
2. Informar: saldo `50000`, MLL `48000`, tipo `Express 50K`, trailing `Combine`, risco `1%`, stop `10` pontos.
3. KPIs: risco/trade ≈ `$500`, trades até DLL `2`, distância ao blowout `$2.000`, dias até blowout `2`.
4. Comparativo: **MNQ** (pv 2) permite muito mais contratos que **ES** (pv 50) com o mesmo risco.
5. Expandir **Monte Carlo** → win rate `0.5`, R `1.5` → "Rodar simulação" → P(blowout)/P(DLL)/P(lucro) plausíveis + curva P50 com linha do MLL.
6. Marcar MNQ + MES → **Gravar no Plano do Dia** → sucesso.
7. Abrir aba **Plano do Dia** (data = amanhã ET) → linhas MNQ/MES com `max_size` + `stop_points` e nota "Risk Planner".

Caso de erro:
- Tipo `Custom` sem DLL → aviso bloqueia o cálculo de trades/Monte Carlo.
- Saldo ≤ MLL → erro "distância negativa", Monte Carlo não roda.

## 4. Critérios de aceite (DoD)

- [ ] `m14_*.sql` aplicado; tabelas + flag conferidas pelas queries da §1.
- [ ] Suíte `pytest tests/` verde.
- [ ] Golden path da §3 completo (cálculo → Monte Carlo → grava no Day Plan → confere no Day Plan).
- [ ] Idempotência: gravar 2× sem "sobrescrever" não duplica (skip); com "sobrescrever" atualiza.
- [ ] `/security-review` rodado (toca RLS de 2 tabelas novas + feature-gating) — sem findings críticos.
- [ ] `MEMORIA.md` + `DECISOES.md` refletem a entrega (já feito).

## 5. Pós-deploy

- Backlog de melhorias em `guia_execucao_3.0.md` (rodável via `/headless-runner`).
- Para promover 3.0 a produção: decidir merge `release/3.0` → `main` + tag `v3.0.0` (não feito ainda).
