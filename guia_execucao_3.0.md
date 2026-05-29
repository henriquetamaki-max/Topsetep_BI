# Guia de Execução — Release 3.0 (Backlog do Risk Planner)

> Documento operacional do **backlog** da 3.0. O MVP do Risk Planner (PR-A→D) já está entregue em `release/3.0`; este guia cobre só as melhorias incrementais. Cada tarefa é auto-contida: objetivo, arquivos, frentes, dependências, critério de pronto (AC verificável) e modo. Compatível com `/headless-runner` (AC = comando que retorna 0).

## 0. Contexto

Branch: `release/3.0` (worktree `E:\BD\X-Metrics 3.0`). Base entregue:
- `src/risk_engine.py` (puro, 37 testes), `src/risk_plan.py` (CRUD + `push_to_daily_plans`), `render_risk_planner` em `src/app.py`, schema `PRD/m14_risk_plans.sql`, 64 chaves i18n.

Convenção: cada tarefa vira 1 commit atômico. AC roda da raiz da worktree com a venv 3.0 (ou venv 2.0 quando o teste importa streamlit/supabase). Sintaxe i18n e Python sempre validadas antes de fechar.

Sufixo de AC padrão (sintaxe Python + paridade i18n) quando a tarefa toca esses arquivos:
```
.venv/Scripts/python.exe -c "import ast; ast.parse(open('src/<arq>.py',encoding='utf-8').read())"
.venv/Scripts/python.exe -m pytest tests/test_i18n_consistency.py -q
```

---

## S1 — Backlog Risk Planner

### T-S1-001 — Reabrir plano salvo (header + seleção + snapshot)

**Objetivo:** ao abrir uma data que já tem `risk_plans`, recarregar não só os inputs (já feito por `get_plan`) mas também a **seleção de ativos** (`risk_plan_assets.selected`) no `data_editor` e o `result_snapshot` do Monte Carlo (mostrar sem recomputar).
**Arquivos:** `src/risk_plan.py` (já tem `list_assets`), `src/app.py` (`render_risk_planner`).
**Frentes:** atômica.
**Deps:** —.
**Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_risk_plan.py -q
```
Manual: abrir data salva → seleção e métricas MC reaparecem sem clicar "Rodar".

### T-S1-002 — Teto de contratos por equivalentes micro/mini

**Objetivo:** o teto TopStep é por contrato-equivalente (1 mini = 10 micros). Hoje `check_rules` aplica o cap por-ativo independente. Somar a seleção em equivalentes-mini e validar contra o cap do tamanho.
**Arquivos:** `src/risk_engine.py` (`check_rules`, helper de equivalência), `tests/test_risk_engine.py`.
**Frentes:** atômica.
**Deps:** —.
**Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_risk_engine.py -q
```
Novo teste: 3 minis MNQ + 20 micros (=2 minis-equiv) em 50K → total 5 equiv = no limite; 6 → estoura.

### T-S1-003 — p_blowout por ativo no comparativo (toggle)

**Objetivo:** expor a coluna `p_blowout` no comparativo via um checkbox "calcular P(blowout) por ativo" (roda `compare_assets` com `mc_params` de `n_sims` reduzido, ex. 2000). Default desligado (custo: 1 MC/linha).
**Arquivos:** `src/app.py` (`render_risk_planner`), i18n (`riskplanner.compare.with_mc`).
**Frentes:** F1 (UI app.py), F2 (i18n 3 locales) [trivial]. **Ordem:** F1 ∥ F2.
**Deps:** —.
**Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -c "import ast; ast.parse(open('src/app.py',encoding='utf-8').read())"
.venv/Scripts/python.exe -m pytest tests/test_i18n_consistency.py -q
```

### T-S1-004 — Slippage + comissão no Monte Carlo

**Objetivo:** subtrair custo por trade (comissão por contrato de `contracts` + slippage configurável em ticks) no modelo R-múltiplo do `monte_carlo`, deixando P(blowout) mais realista.
**Arquivos:** `src/risk_engine.py` (`monte_carlo` ganha `cost_per_trade_usd`), `src/app.py` (input slippage/comissão), `tests/test_risk_engine.py`.
**Frentes:** F1 (engine + testes), F2 (UI app.py + i18n). **Ordem:** F1 → F2 (UI consome assinatura nova).
**Deps:** —.
**Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_risk_engine.py -q
```
Novo teste: `cost_per_trade_usd>0` reduz `expected_final_equity` e aumenta `p_blowout` vs custo zero (mesma seed).

### T-S1-005 — Fan-chart P5–P95 da curva de equity

**Objetivo:** `monte_carlo` hoje devolve só `equity_curve_p50`. Adicionar curvas por percentil (`equity_curve_p5/p25/p75/p95`) e plotar banda sombreada no gráfico (fan-chart) além da mediana.
**Arquivos:** `src/risk_engine.py` (retorno + percentis por dia), `src/app.py` (plotly fill), `tests/test_risk_engine.py`.
**Frentes:** F1 (engine + testes) → F2 (UI). **Ordem:** F1 → F2.
**Deps:** —.
**Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_risk_engine.py -q
```
Novo teste: `len(equity_curve_p95)==horizon_days` e `p95[d] >= p50[d] >= p5[d]` para todo d.

### T-S1-006 — Auto-fill saldo/MLL do último live_snapshot

**Objetivo:** botão "Puxar da extensão" que pré-preenche saldo (e drawdown→MLL) a partir do último `live_snapshots` do usuário, com override manual preservado. Só visível se houver snapshot recente.
**Arquivos:** `src/risk_plan.py` (`last_snapshot()` ou reuso de `live._fetch_last_snapshot`), `src/app.py`, i18n.
**Frentes:** F1 (leitura snapshot — reuso), F2 (UI + i18n). **Ordem:** F1 → F2.
**Deps:** extensão ativa gravando `live_snapshots` (já existe).
**Modo:** explore (confirmar campos disponíveis no snapshot: `day_pnl`, `drawdown`; saldo pode não vir direto).
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_risk_plan.py -q
```

### T-S1-007 — check_rules com consistência/dia-vencedor reais

**Objetivo:** alimentar `check_rules` com `cycle_profit_usd` e `best_day_usd` reais (do dashboard/`payouts`) para ativar de fato as regras `consistency_50` e `min_winning_day` (hoje só `contract_cap` é exercida na UI).
**Arquivos:** `src/app.py` (passar métricas reais a `check_rules`), possivelmente `src/metrics.py` (expor melhor dia/lucro do ciclo).
**Frentes:** F1 (métrica em metrics.py + teste), F2 (wiring app.py). **Ordem:** F1 → F2.
**Deps:** —.
**Modo:** plan.
**AC:**
```
.venv/Scripts/python.exe -m pytest tests/test_risk_engine.py tests/test_metrics_overview.py -q
```

---

## Ordem geral sugerida

Independentes (qualquer ordem / paralelizáveis): **T-S1-001, T-S1-002, T-S1-003**.
Sequência engine→UI: **T-S1-004**, **T-S1-005** (cada uma F1→F2 interna).
Dependem de dados externos: **T-S1-006** (extensão), **T-S1-007** (métricas do dashboard).

Nenhuma é bloqueante para o deploy do MVP (ver `RELEASE_3.0_CHECKLIST.md`).
