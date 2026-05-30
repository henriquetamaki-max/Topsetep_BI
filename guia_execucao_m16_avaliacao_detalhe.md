# Guia de Execução — M16: Avaliação detalhada + journaling + dashboard de aderência

> Fase 2 da Avaliação de Risco (M15). O MVP já mostra o resumo por dia (✓/✗/— por dimensão). Esta fase adiciona: **(1)** drill-down de um dia selecionado com comparativo completo plano×realizado (delta, %, outros campos); **(2)** **comentário salvo por dia** para o trader avaliar a si mesmo; **(3)** mini-dashboard de cumprimento do plano em janelas diária / semanal / mensal.

## 0. Contexto e base reusável

Branch: `release/3.0` (worktree `E:\BD\X-Metrics 3.0`). Já entregue no M15:
- `metrics.compute_risk_review(groups, plans, risk_plans, point_values)` → `by_day` (1 linha/dia com `dim_trades/dim_loss/dim_size/dim_stop`, `n_ops`, `realized_pnl`, `planned_trades`, `planned_dll`, `has_plan`, `clean`) + `op_violations` + contadores.
- `risk_plan.list_plans_range`, `save_review/list_reviews`, `save_day_note` (a criar).
- UI `render_risk_review` em `app.py` (aba "Avaliação"), KPIs + tabela ✓/✗/— + tabela "onde errou" + salvar avaliação + evolução do score.
- Dados disponíveis: `groups` (total_size, total_net_pnl, total_points, group_start, contract, type, trade_count), `daily_plans` (max_size, stop_points, direction, target_points por contrato), `risk_plans` (trades_per_day, daily_loss_limit_usd, balance_usd, mll_threshold_usd, risk_mode, risk_value), `contracts` (point_value_usd).

## 1. Decisões travadas (usuário, 2026-05-30)

1. **Comentário: 1 por dia.** Tabela nova `risk_day_notes` (user_id, trade_day, comment), editável.
2. **Seleção de dia: `st.selectbox` de datas** acima do painel de detalhe (não clique na linha).
3. **Dashboard: os 4 componentes no MVP** — (a) barras de % semanal/mensal, (b) heatmap-calendário diário (verde/vermelho/cinza), (c) tendência de violações por tipo, (d) streak atual + melhores/piores.
4. **Métrica de aderência: score ponderado** por severidade (blowout/DLL pesam mais que size/trades) — ver §2.1.
5. **Detalhe = comparativo por dimensão** (planejado, realizado, delta, %, status) + contexto do dia. Limitação MAE herdada do M15 (stop = perda realizada). Score sobre dias-com-plano; dias sem plano aparecem mas não pontuam.

## 2. Modelo do detalhe por dia (comparativo plano × realizado)

Para o dia selecionado, uma tabela 1 linha por dimensão:

| Dimensão | Planejado | Realizado | Delta | % | Status |
|---|---|---|---|---|---|
| Max trades | `trades_per_day` | `n_ops` | real−plan | real/plan−1 | ✓/✗/— |
| Max loss (DLL) | `daily_loss_limit_usd` | `abs(min(0, PnL dia))` | real−plan | real/plan | ✓/✗/— |
| Max size | `max_size` (por contrato) | maior `total_size` do dia | real−plan | — | ✓/✗/— |
| Stop | risco $ planejado (`stop_pts×pv×max_size`) | pior perda de operação | real−plan | real/plan | ✓/✗/— |
| Risco/trade | `risk_usd` (pct×saldo ou fixo) | perda média das perdedoras | real−plan | real/plan | ✓/✗/— |
| Distância blowout | `balance−mll` | drawdown acumulado | real−plan | — | ✓/✗/— |

Campos de contexto do dia: contratos operados, nº operações, vencedoras/perdedoras, PnL líquido, melhor/pior operação. Dimensões sem referência mostram "—" no planejado e status "—".

### 2.1. Score ponderado (aderência por janela)

Pesos por severidade de violação (constantes configuráveis em `metrics.py`):

| Dimensão | Peso |
|---|---|
| blowout | 40 |
| max_loss (DLL) | 30 |
| stop | 15 |
| max_trades | 10 |
| max_size | 10 |

- **Score do dia** (só dias-com-plano) = `max(0, 100 − Σ pesos das dimensões violadas naquele dia)`. Dia limpo = 100; dia que furou DLL+stop = 100−45 = 55; dia que estourou tudo ≈ 0.
- **Score da janela** (semana ISO / mês) = média dos scores dos dias-com-plano na janela. Dias sem plano não entram.
- O número grande do topo (M15, `clean_days/total_days`) permanece; o score ponderado é a métrica do **dashboard temporal**, mais sensível à gravidade.

## 3. Schema — `PRD/m16_day_notes.sql`

```
risk_day_notes (
  id bigserial pk,
  user_id uuid not null references auth.users on delete cascade,
  trade_day date not null,
  comment text not null default '',
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (user_id, trade_day)
)
```
RLS `owner_all` (auth.uid()=user_id), índice `(user_id, trade_day)`, trigger `touch_updated_at`. Idempotente (sem DROP). **Aplicação manual no Supabase + `/security-review`** (toca RLS).

## 4. Sub-PRs

### T-S1-001 — Detalhe por dia (métrica pura)
**Objetivo:** `metrics.compute_day_detail(groups, plans, risk_plans, point_values, day) -> dict` com a tabela de dimensões (planejado/realizado/delta/pct/status) + contexto do dia. Reusa a mesma lógica de referência do `compute_risk_review` (extrair helpers comuns para não duplicar).
**Arquivos:** `src/metrics.py`, `tests/test_metrics_day_detail.py`.
**Frentes:** atômica. **Deps:** —. **Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_metrics_day_detail.py -q
```
Testes: cada dimensão com plano (delta/pct corretos), dimensão sem referência (planejado None, status "—"), dia sem trades, sinais de delta.

### T-S1-002 — Aderência temporal (métrica pura, score ponderado)
**Objetivo:** `metrics.compute_adherence_timeseries(by_day) -> dict` com:
- `daily`: por dia → score ponderado (§2.1), status (limpo/violado/sem plano).
- `weekly` / `monthly`: score ponderado médio + nº de dias + violações por tipo.
- `streak`: sequência atual de dias-com-plano limpos + melhor/pior dia (por score).
- `violations_trend`: contagem por tipo (max_trades/max_loss/max_size/stop/blowout) por janela.
Constantes de peso `RISK_WEIGHTS` em `metrics.py`. Fuso ET (já em `by_day.trade_day`).
**Arquivos:** `src/metrics.py`, `tests/test_metrics_adherence_ts.py`.
**Frentes:** atômica. **Deps:** —. **Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_metrics_adherence_ts.py -q
```
Testes: score do dia (limpo=100, DLL+stop=55), agregação semanal (ISO week)/mensal, streak reseta em violação, dias sem plano fora do denominador, vazio.

### T-S1-003 — Schema + CRUD de comentários
**Objetivo:** `PRD/m16_day_notes.sql` + `risk_plan.save_day_note(trade_day, comment)` (upsert user+day) e `list_day_notes_range(start, end) -> DataFrame`.
**Arquivos:** `PRD/m16_day_notes.sql`, `src/risk_plan.py`, `tests/test_risk_plan.py`.
**Frentes:** F1 (schema) ∥ F2 (CRUD+teste). **Ordem:** F1 → aplicar SQL → F2. **Deps:** —. **Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_risk_plan.py -q
```
Manual: aplicar SQL no Supabase; `select count(*) from risk_day_notes` = 0.

### T-S1-004 — UI drill-down + comentário
**Objetivo:** `st.selectbox` de datas (todas as do período; opção de filtrar só com plano) acima de um painel com o comparativo (T-S1-001), indicador 📝 na lista para dias com nota, e `st.text_area` do comentário do dia + botão salvar (T-S1-003). Carrega a nota existente ao selecionar a data.
**Arquivos:** `src/app.py` (`render_risk_review`), `locales/{en,es,pt_BR}.json`.
**Frentes:** F1 (UI) ∥ F2 (i18n). **Deps:** T-S1-001, T-S1-003. **Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -c "import ast; ast.parse(open('src/app.py',encoding='utf-8').read())"
.venv/Scripts/python.exe -m pytest tests/test_i18n_consistency.py -q
```
Manual: clicar num dia → detalhe coerente; escrever comentário → salvar → reabrir → comentário persiste.

### T-S1-005 — UI dashboard de aderência (4 componentes)
**Objetivo:** seção "Cumprimento do plano" na aba com os 4 componentes (T-S1-002):
(a) barras de score ponderado semanal/mensal; (b) heatmap-calendário diário (verde=limpo, vermelho=violação, cinza=sem plano); (c) tendência de violações por tipo (barras empilhadas por semana/mês); (d) cartões de streak atual + melhor/pior dia. Todos plotly_dark, tema-aware.
**Arquivos:** `src/app.py`, `locales/*`.
**Frentes:** F1 (UI plotly) ∥ F2 (i18n). **Deps:** T-S1-002. **Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -c "import ast; ast.parse(open('src/app.py',encoding='utf-8').read())"
.venv/Scripts/python.exe -m pytest tests/test_i18n_consistency.py -q
```
Manual: dashboard reflete os dias com plano; janelas diária/semanal/mensal batem com a tabela.

## 5. Ordem geral

`T-S1-001 ∥ T-S1-002 ∥ T-S1-003(F1+SQL)` → `T-S1-003(F2)` → `T-S1-004` ∥ `T-S1-005`.

Bloco de maior valor primeiro: **T-S1-001 + T-S1-004** (drill-down + comentário, o que o trader mais sentiu falta). Dashboard (T-S1-002/005) em seguida.

## 6. Definition of Done (M16)

- [ ] `compute_day_detail` e `compute_adherence_timeseries` puras, testadas; suíte total verde.
- [ ] `m16_day_notes.sql` aplicado no Supabase (tabela + RLS conferidas).
- [ ] Drill-down: clicar num dia abre comparativo planejado×realizado com delta/% e status por dimensão.
- [ ] Comentário por dia salva e recarrega; indicador 📝 na lista.
- [ ] Dashboard diário/semanal/mensal coerente com a tabela.
- [ ] i18n 3 locales (paridade `test_i18n_consistency`).
- [ ] Limitação MAE declarada onde aplicável.
- [ ] `/security-review` (toca RLS de `risk_day_notes`) sem findings críticos.
- [ ] `MEMORIA.md` + (se houver decisão) `DECISOES.md` atualizados.

## 7. Decisões travadas

1. Comentário: **1 por dia**.
2. Seleção: **`st.selectbox` de datas**.
3. Dashboard MVP: **os 4 componentes** (% semanal/mensal, heatmap-calendário, tendência de violações, streak/KPIs).
4. Aderência: **score ponderado** por severidade (§2.1).
