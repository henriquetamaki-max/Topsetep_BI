# Guia de Execução — M15: Avaliação de Risco (retrospectivo)

> Fecha o ciclo **plano → execução → feedback** do Risk Planner. Hoje o plano grava sugestões em `daily_plans` e a aderência (M5/M8) só confere **tamanho + direção**. Este milestone adiciona a avaliação retrospectiva das dimensões que são o propósito do Risk Planner: **stop, orçamento de risco $/trade, DLL diário e proximidade do blowout** — confrontando os trades importados contra o plano daquele dia e dizendo ao trader, por dia, **se cumpriu e onde errou**.

## 0. Contexto e estado atual

Branch: `release/3.0` (worktree `E:\BD\X-Metrics 3.0`).

O que já existe e será reusado (não recriar):
- `metrics.compute_groups(df) -> (df, groups)` — agrupa trades em operações. `groups` tem: `group_id, contract_name, type, group_start, group_end, trade_count, total_points, total_pnl, total_net_pnl, total_size, additions_count, duration_min, pnl_status`.
- `metrics.compute_plan_adherence(groups, plans)` — padrão a espelhar (classifica por `(dia_ET, contrato, direção)`, devolve contadores + `score_pct` + `violations` DataFrame).
- `daily_plan.list_plans(plan_date=None)` — todos os planos (ou de 1 dia). Linhas: `plan_date, contract_name, direction, max_size, stop_points, entry_trigger, target_points, notes`.
- `risk_plan.get_plan(date)` — header `risk_plans` do dia: `balance_usd, mll_threshold_usd, daily_loss_limit_usd, account_type, risk_mode, risk_value, trailing_mode`.
- `risk_plan.list_contracts()` — `symbol, point_value_usd, is_micro`.
- `trades` (schema): `entered_at, exited_at, entry_price, exit_price, pnl, pnl_net, size, type, contract_name, trade_day, points` (generated). **Sem MAE/MFE.**
- Padrão de UI: `render_*` em `app.py`, cards `segment-box`, i18n 3 locales sincronizados, gating `billing.has_feature(plan, "risk_planner")`.

## 1. Limitações assumidas (honestas)

1. **Sem preço intrabar (MAE/MFE).** "Respeitou o stop" = a **perda realizada** da operação não passou do risco planejado. Não detecta quem deixou o preço furar o stop e voltou no positivo. Documentar na UI ("baseado na perda realizada, não no pior momento da operação").
2. **`daily_plans` é direção `Long` por enquanto** (o push grava só Long). Operação Short cai em `unplanned`/`against_plan`. T-S4 corrige o push; até lá, a avaliação de direção herda o comportamento da aderência atual.
3. **Plano por dia é opcional.** Dias sem `daily_plans`/`risk_plans` entram como `sem_plano` (não penaliza score, só informa). O trader nem sempre planejou todo dia do CSV.
4. **Adições diluem o stop por-operação.** Com múltiplas entradas no grupo, compara-se o **risco em $ do grupo** (perda realizada vs `stop_points × point_value × max_size`), não ponto-a-ponto.

## 2. As 4 dimensões da avaliação (fórmulas mapeadas a colunas reais)

Tudo por **dia ET** (`group_start` → `tz_convert('America/New_York')`, igual à aderência).

| Dimensão | Referência (plano) | Realizado (trades) | Veredito |
|---|---|---|---|
| **Stop respeitado** | `daily_plans.stop_points × point_value_usd × max_size` (risco $ máx da operação) | operação perdedora: `abs(total_net_pnl)` | perda real > risco planejado → `stop_furado` |
| **Risco $/trade** | `risk_plans.risk_usd_per_trade` (de `risk_value`/`risk_mode` × saldo) | perda média das operações perdedoras do dia | média real > alvo → `risco_excedido` |
| **DLL diário** | `risk_plans.daily_loss_limit_usd` (fallback default do `account_type`) | soma `pnl_net` do dia | soma ≤ −DLL → `dll_furado` (crítico) |
| **Blowout / drawdown** | `balance_usd − mll_threshold_usd` (distância) | pior drawdown acumulado do período | drawdown ≥ distância → `blowout` (crítico) |

Saída por dia: `{date, compliant|violations[], realized_pnl, planned_dll, worst_op_loss, ...}`. Agregado: score % de dias limpos + contagem por tipo + DataFrame de violações pronto pra render (espelha `adherence["violations"]`).

## 3. Decisão de arquitetura

- **Sem schema novo no MVP.** Tudo computado on-the-fly de `trades` + `daily_plans` + `risk_plans`. Persistir snapshot em `risk_reviews` fica como T-S5 (backlog), só se quisermos histórico de score.
- **Métrica pura** em `metrics.py` (sem Streamlit/Supabase), testável isolada — mesmo contrato de `compute_plan_adherence`.
- **Nova aba "Avaliação"** (retrospectiva, depende de trades importados), gated pelo mesmo `risk_planner`. Separada do Risk Planner (que é forward-looking, "amanhã") e cross-link com o painel de Aderência do Dashboard.

## 4. Sub-PRs (padrão schema→métrica→UI; aqui métrica→leitura→UI)

### T-S1-001 — Métrica `compute_risk_review` + testes
**Objetivo:** função pura que recebe `groups`, `daily_plans`, `risk_plans` (por data), catálogo de contratos, e devolve veredito por dia + agregado + `violations`.
**Arquivos:** `src/metrics.py` (nova `compute_risk_review` + `_empty_risk_review`), `tests/test_metrics_risk_review.py`.
**Frentes:** atômica.
**Deps:** —.
**Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_metrics_risk_review.py -q
```
Testes mínimos: dia limpo (sem violação), `stop_furado` (perda > risco planejado), `dll_furado` (soma dia ≤ −DLL), `risco_excedido` (média perda > alvo), dia `sem_plano` não penaliza, drawdown/blowout, vazio devolve schema completo, fuso ET no agrupamento por dia.

### T-S1-002 — Leitura de planos no período
**Objetivo:** puxar `risk_plans` de um intervalo de datas (hoje só há `get_plan(date)` single) e expor catálogo de `point_value_usd` para a métrica.
**Arquivos:** `src/risk_plan.py` (`list_plans_range(start, end) -> DataFrame`), reuso de `daily_plan.list_plans()`.
**Frentes:** atômica.
**Deps:** —.
**Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_risk_plan.py -q
```
Novo teste: `list_plans_range` filtra por intervalo, devolve DataFrame vazio sem dados, injeta nada (RLS filtra por user).

### T-S1-003 — Aba "Avaliação" + i18n
**Objetivo:** `render_risk_review(user, plan)` em `app.py`, nova aba gated `risk_planner`. Seletor de período (reusa atalhos do Dashboard ou date range), score de dias limpos, cards por dimensão, **veredito por dia** (verde/amarelo/vermelho) e tabela de violações com a coluna "onde errou". Caption com a limitação MAE.
**Arquivos:** `src/app.py` (`render_risk_review` + registro da aba no bloco `_tab_names`/`_tabs`, padrão `_show_risk`), `locales/{en,es,pt_BR}.json`.
**Frentes:** F1 (UI app.py), F2 (i18n 3 locales). **Ordem:** F1 ∥ F2 (F2 trivial após chaves definidas).
**Deps:** T-S1-001, T-S1-002.
**Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -c "import ast; ast.parse(open('src/app.py',encoding='utf-8').read())"
.venv/Scripts/python.exe -m pytest tests/test_i18n_consistency.py -q
```
Manual: importar CSV de um dia com plano gravado → aba Avaliação → veredito do dia + violações coerentes.

### T-S1-004 — Corrigir direção no push (Long-only) [opcional]
**Objetivo:** `push_to_daily_plans` grava só `Long`; permitir a direção planejada (ou gravar ambas / marcar direção-agnóstica) para a avaliação de direção não gerar falso `against_plan` em quem opera Short.
**Arquivos:** `src/risk_plan.py` (`push_to_daily_plans`), `src/app.py` (input de direção no comparativo), `tests/test_risk_plan.py`.
**Frentes:** F1 (engine/push + teste) → F2 (UI). **Ordem:** F1 → F2.
**Deps:** —.
**Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_risk_plan.py -q
```

### T-S1-005 — Persistir snapshot `risk_reviews` [backlog]
**Objetivo:** opcional — gravar score/violações por período em `risk_reviews` (schema novo idempotente, RLS owner_all) para histórico e gráfico de evolução do score.
**Arquivos:** `PRD/m15_risk_reviews.sql`, `src/risk_plan.py` (save/get review), `src/app.py`.
**Frentes:** F1 (schema) ∥ F2 (CRUD). **Ordem:** F1 → F2 → UI.
**Deps:** T-S1-003.
**Modo:** plan.
**AC:** SQL aplicado + `select count(*) from risk_reviews` ok; teste de CRUD verde.

## 5. Ordem geral

`T-S1-001 ∥ T-S1-002` → `T-S1-003`. `T-S1-004` independente (qualquer hora). `T-S1-005` só se quisermos histórico.

MVP visível ao trader = **T-S1-001 → 002 → 003**. Itens de maior valor e menor custo: **DLL retrospectivo** e **stop furado** (saem direto de `trades` + `daily_plans`/`risk_plans`, sem schema).

## 6. Definition of Done (M15)

- [ ] `compute_risk_review` pura, testada (≥8 casos), suíte total verde.
- [ ] Aba "Avaliação" renderiza score + cards + veredito por dia + tabela "onde errou".
- [ ] i18n nos 3 locales (paridade `test_i18n_consistency`).
- [ ] Limitação MAE declarada na UI.
- [ ] Golden path: CSV com plano do dia → veredito coerente (1 dia limpo + 1 dia com violação).
- [ ] `MEMORIA.md` + (se houver decisão) `DECISOES.md` atualizados.
- [ ] Sem mudança sensível de RLS/auth (MVP não tem schema) — `/security-review` só se T-S1-005 entrar.
