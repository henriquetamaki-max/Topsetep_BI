# Memória — BI TopStep

## Objetivo

BI multi-tenant para análise de trades exportados do TopStepX. Pipeline: CSV → upload no app Streamlit (autenticado) → Supabase Postgres (com RLS por `user_id`) → dashboard. Detalhes técnicos no `PRD/CLAUDE.md`.

Há também um anexo paralelo: **port JavaScript do indicador Pine "Pullbacks MNQ - Bone Zone v7.1 PRO"** para uso direto na plataforma TopstepX (via charting_library customizada), distribuído como userscript Tampermonkey.

## Estado atual

Última atualização: 2026-05-20.

- **Fusão com Trade_Agent aprovada (2026-05-20)**: este repo passa a absorver o projeto `E:\BD\Trade_Agent` (sistema local real-time anti-tilt, em pré-Fase-1). Migração em 4 fases: (0) pré-reqs — concluir M4 Stripe webhook + snapshot final do Trade_Agent; (1) batch — portar detecção de adições não-planejadas + janelas dual-timezone (Brasília/Chicago) + schema novo (`live_snapshots`, `alerts`, `tilt_patterns`, `daily_plans`, `payouts`); (2) extensão Chrome MV3 multi-tenant (auth via Supabase JWT); (3) real-time via Supabase Realtime + Web Notifications API + Risk Guard em Edge Function; (4) cleanup (arquivar Trade_Agent, gerar `ENCERRAMENTO.md` lá). **Real-time é MVP** da fusão. Coach IA segue copy-paste (sem LLM no SaaS). Plano completo: `C:\Users\henrique.tamaki\.claude\plans\me-fa-a-um-comparativo-soft-bear.md`. Decisão detalhada em `DECISOES.md`.
- **App SaaS**: branch `saas/m4-stripe-webhook-edge-fn` em andamento (Edge Function de webhook Stripe na M4) — **bloqueia o início da Fase 1 da fusão**. Schema multi-tenant e RLS já aplicados.
- **Refatoração de layout (2026-05-20)**: todos os 10 módulos `.py` da raiz foram movidos para `src/` via `git mv` (preserva histórico). Imports planos (`import auth`, `from i18n import t`) continuam funcionando porque Streamlit adiciona o diretório do script ao `sys.path`. Ajustes em `Path(__file__).resolve().parent` → `.parent.parent` em `auth.py`, `i18n.py`, `app.py` e `ingest.py` (esses resolviam `Env/` e `locales/` relativos ao próprio arquivo). `.bat` e docs apontam agora para `src/app.py` / `src/ingest.py`. `login-screen.png` foi para `assets/`. Raiz contém apenas: 3 `.bat`, `requirements.txt`, `.gitignore`, docs (`README.md`, `CLAUDE.md`, `MEMORIA.md`, `DECISOES.md`) e pastas de infraestrutura. **Atenção em deploy**: Streamlit Cloud precisa ser reconfigurado para entrypoint `src/app.py`.
- **Indicador customizado TopstepX (anexo)**: `PRD/topstepx_pullback_mnq.user.js` **v0.3.0** (refactor amplo). Mantém todas as features de 0.2.x (EMAs 9/21/50/200, VWAP CME, Bone Zone, AlgoAlpha local, volume SMA, plotshapes A+/B/C, linhas Entry/Stop/Alvos, log + beep). Mudanças: hot path mais rápido (cache de boundary VWAP elimina `Intl.DateTimeFormat` por barra; drop de `new_var` ociosos), bootstrap robusto (detecção por shape + soft-watch contínuo + reativação em SPA navigation), sanidade de runtime (clamp de inputs, feature-detect de `PineJS.Std.*`, reset em troca de símbolo/TF, TTL configurável das linhas, AudioContext singleton). Novo input `linhasTTLBars` (default 50, 0=infinito). Trilha de versões em `PRD/CHANGELOG_pullback_mnq.md`.

## Como rodar / testar / debugar

### App (Streamlit + Supabase)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run src/app.py
```

Schema em `PRD/schema.sql`, aplicado manualmente no SQL Editor do Supabase.

### Indicador customizado no TopstepX

1. Instalar Tampermonkey no Chrome.
2. Criar novo userscript no Tampermonkey, colar o conteúdo de `PRD/topstepx_pullback_mnq.user.js`, salvar (Ctrl+S).
3. Abrir página de trade do TopstepX → menu Indicators → buscar "Pullbacks MNQ".
4. Diagnóstico: F12 → Console → buscar prefixo `[Pullback MNQ]`. A última linha deve ser `custom indicator injetado`.

## Gotchas / Aprendizados

- 2026-05-20 — `Intl.DateTimeFormat(...).formatToParts()` é caro o suficiente para dominar o tempo por barra em backfills longos (~5-20µs/chamada + alocação). Para indicadores que precisam de timezone (ex.: sessão CME 18:00 ET), **cachear o boundary** (próximo limite em ms epoch) e só recomputar quando o timestamp ultrapassa. DST é tratado naturalmente porque a primeira barra após transição recomputa e corrige.
- 2026-05-20 — `ctx.new_var()` aloca + mantém histórico do valor — só vale a pena para séries lidas com `.get(n)`. No port v0.2.x, `high_s` e `low_s` foram criados mas nunca consumidos: drop dessas duas chamadas é ganho gratuito por barra. Sempre auditar `.get(` no arquivo antes de mover OHLCV para new_var.
- 2026-05-20 — A charting_library v31 recria o contexto do custom indicator quando o usuário muda inputs no dialog (cache de inputs em `init` é redundante), mas **não** sempre quando troca o símbolo/timeframe — precisa detectar manualmente via `ctx.symbol.ticker + period` e resetar contadores/VWAP/sinal confirmado para evitar contaminação cross-symbol.
- 2026-05-20 — Userscripts com `@match` largo (`/*`) + `setupSPAObserver` (monkey-patch leve em `history.pushState/replaceState` + listener `popstate`) suportam SPA navigation de `/account` → `/trade` sem F5. Restringir `@match` para `/trade*` **quebra** esse fluxo porque Tampermonkey não re-injeta em pushState. Solução: match largo + guard `CHART_PATH_REGEX` no startScan para não desperdiçar CPU fora de `/trade`.
- 2026-05-18 — TopstepX usa **TradingView Advanced Charts (charting_library) versão `TT v31.1.0`**, não TradingView.com. Não tem Pine Script Editor; indicadores customizados precisam ser em **JavaScript** seguindo o formato `metainfo + constructor` da API. A brecha para injetar é interceptar `window['tradingview_XXXX'].getCustomIndicators` (sufixo dinâmico — precisa scan).
- 2026-05-18 — A API custom indicators **não expõe** `request.security()` (MTF) nem desenho de `table` (dashboard). Também não dispara alertas configuráveis via `alertcondition`. No port do indicador MNQ, esses três blocos do Pine foram cortados do MVP.
- 2026-05-18 — `_metainfoVersion: 51` aceita `plot.type: 'shapes'` com `plottype` em `shape_diamond`, `shape_triangle_up/down`, `shape_xcross`, etc. — vale para reproduzir os `plotshape` do Pine.
- 2026-05-18 — `PineJS.Std.vwap(ctx)` pode não estar exposto em todas as versões da charting_library. O userscript faz fallback para VWAP manual com reset por dia UTC (não é a sessão CME ideal, mas serve para MVP).
- 2026-05-18 — O modal vermelho **"Indicator error — Oops. Something has gone wrong with one or more of your indicators"** no TopstepX pode ser estado transitório do widget (lifecycle do service-worker / unload listener deprecated). **F5 resolve** quando não há erro real no código do userscript. Antes de mexer no script, sempre conferir o console: se aparecer `[Pullback MNQ] custom indicator injetado` + `N custom indicators loaded` + `getBars()` retornando dados, o nosso indicador está OK.
- 2026-05-18 — Em chart de **tick (100T, 500T)**, o console fica poluído com `ChartApi.PointsetsManager:Cannot get index of time: time=…`. **Não é do nosso indicador** — é o `Topstep Daily Levels` (Custom indicator nativo da Topstep) tentando plotar timestamps fixos (abertura/fechamento RTH, em UTC) que não correspondem a nenhuma barra da timescale de tick (barras de tick não têm hora fixa). Para silenciar: desativar o Daily Levels no chart de tick, ou usar chart por tempo (1m/5m). Não afeta funcionamento.
- 2026-05-18 — Diagnóstico de erro real do indicador: tudo do `Pullback MNQ` é logado com prefixo `[Pullback MNQ]` (LOG/WARN). Erros internos do charting_library aparecem em `library.<hash>.js` — só são problema nosso se a stack trace mencionar `pullbacks_mnq_bone_zone` ou `tv-basicstudies-1`.

## Histórico de tarefas concluídas

- 2026-05-20 — **Comparativo tático Trade_Agent ↔ BI TopStep + decisão de fusão**: gerado panorama lado-a-lado dos dois projetos (modelo, stack, estado, sobreposições, complementaridades). Direção escolhida: BI TopStep absorve Trade_Agent. Real-time entra como MVP via Supabase Realtime. Coach segue copy-paste. Trade_Agent arquivado pós-migração. Roadmap em 4 fases definido. Decisão registrada em `DECISOES.md`. Plano completo em `~/.claude/plans/me-fa-a-um-comparativo-soft-bear.md`.
- 2026-05-20 — **Refatoração de layout**: movidos `app.py`, `auth.py`, `account.py`, `action_plan.py`, `billing.py`, `coach_ai.py`, `i18n.py`, `ingest_core.py`, `ingest.py` e `metrics.py` da raiz para `src/`. Ajustados 4 paths baseados em `__file__`, 3 `.bat`, `CLAUDE.md`, `PRD/README.md`. Criado `README.md` na raiz. `login-screen.png` movido para `assets/`. `__pycache__/` órfão removido.
- 2026-05-20 — **v0.3.0** do userscript Pullbacks MNQ: refactor amplo em 4 blocos (estrutura, bootstrap, performance, sanidade). Hot path otimizado (cache de boundary VWAP CME, drop `new_var` ociosos, consolidação de cálculos). Bootstrap robusto (detecção por shape + soft-watch + SPA navigation). Sanidade de runtime (clamp de inputs com WARN single-shot, feature-detect `PineJS.Std.*`, reset em troca de símbolo/TF, TTL configurável de linhas, AudioContext singleton). Novo input `linhasTTLBars`. Criado `PRD/CHANGELOG_pullback_mnq.md` e `DECISOES.md` (decisão sobre single-file + CHANGELOG vs build tooling).
- 2026-05-18 — Port v0.1.0 do indicador "Pullbacks MNQ - Bone Zone v7.1" para userscript JS no formato da charting_library da TopstepX. Sem MTF, sem dashboard, sem alertas (limitações da API).
- 2026-05-18 — Diagnóstico do erro "Indicator error" em produção: confirmado como transitório (F5 resolve). Identificado ruído de `PointsetsManager` em tick charts como vindo do `Topstep Daily Levels`, não do nosso script.
- 2026-05-18 — v0.2.0 do userscript Pullbacks MNQ: VWAP com reset por sessão CME (18:00 ET via `Intl.DateTimeFormat` com timezone NY), 5 plots de linha persistentes (Entry/Stop/Tgt2/Tgt4/Tgt6) que perduram até próximo sinal, `console.info('[Pullback MNQ] SIGNAL', {...})` estruturado + beep Web Audio API (880 Hz long, 440 Hz short) — debounce por timestamp da barra + janela de 5min para não spammar no replay histórico. Inputs novos: `mostrarLinhas` (bool, default true), `tocarSom` (bool, default true).
