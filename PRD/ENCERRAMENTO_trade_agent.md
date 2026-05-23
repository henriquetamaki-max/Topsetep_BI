# Encerramento — Trade_Agent (cópia de referência)

> Documento de fechamento do projeto **Trade_Agent** (`E:\BD\Trade_Agent`), descontinuado em 2026-05-20 com funcionalidades migradas para este repo (`BI TopStep`).
>
> Esta cópia vive no BI TopStep porque o Trade_Agent vai ser arquivado e este post-mortem é parte da memória institucional da fusão. Uma cópia idêntica deve ser colocada como `ENCERRAMENTO.md` na raiz do Trade_Agent (instrução em "Passos manuais para arquivar" no final).

---

## 1. Dor / Problema resolvido

Operador trader em conta TopStep Combine 100K (NQ Micro / NASDAQ) precisava:

- **Não sentir tilt em tempo real:** detectar adições não-planejadas (overtrading) e dar feedback antes do close do dia, não só na revisão pós-mercado.
- **Risk Guard ao vivo:** receber alerta quando se aproximar do Daily Loss Limit ou do Trailing Drawdown da conta, evitando perda da Combine por erro evitável.
- **Plano matinal explícito:** persistir o que pretendia fazer no dia (contratos, size, stop, alvo) e confrontar a execução real contra ele.
- **Narrativa comportamental:** revisão diária/semanal das próprias operações em linguagem natural, não só números.

Antes do Trade_Agent, esses controles existiam apenas em planilhas avulsas + memória + uma pasta de CSVs do TopStepX que ninguém revisitava.

## 2. Resultado entregue

**Estado em arquivamento (snapshot `v-final-pre-merge`, commit `45d6f91`):**

- **`Legacy/Dashboard Trade Pontos/processor.py`** funcional: detecta adições não-planejadas via sobreposição temporal entre trades, calcula KPIs em pontos e em USD, classifica grupos por status. **Validado byte-a-byte com CSVs reais** do operador. Esta é a peça mais madura do projeto.
- **`api/`** FastAPI portado do Legacy: 9 tabelas SQLite com WAL + Alembic, watchdog de ingestão TopStepX, WebSocket envelope com event bus, ErrorResponse padronizada, observability dashboard em `/dev/metrics`, smoke test sintético.
- **`Legacy/topstepx-monitor-extension/`** Chrome MV3: monitor da página `topstepx.com/share`, extractors por label, polling v2.3, keepalive 25s.
- **Schema rico** (9 tabelas) para single-user, com semântica clara.
- **Janelas operacionais dual-timezone (Brasília/Chicago)** DST-aware com endpoint de diagnóstico.
- **CI** em Python 3.11 + 3.13, pre-commit hooks, logging JSONL rotativo com PII filter.

**Métricas:**
- 36 commits no `main`.
- Schema com 9 tabelas + Alembic versionado.
- Extensão MV3 com keepalive + 4 extractors por label.
- ~36k linhas de código (Legacy + api/ + frontend Next.js + extensão).

**O que NÃO ficou pronto:**
- Trader UI moderna (frontend Next.js) — só esqueleto.
- Bug P0 da extensão (alertas não disparavam mesmo lendo o DOM) — diagnosticado, não fechado.
- Coach IA (CLI Claude para narrativas) — só conceito; descartado na fusão.
- Persistência de snapshots reais (`live_snapshots` ficou criado e vazio).

## 3. Esforço intelectual aplicado

### Pesquisa e benchmarking

- Avaliados 3 caminhos para ingestão TopStepX em tempo real: (a) extensão Chrome scrape de DOM, (b) parse de CSV exportado em watchdog, (c) API privada TopStep (descartado por falta de credencial estável). Escolha: **(a)+(b) combinados** — extensão para snapshots live, watchdog para CSVs históricos.
- Comparados frameworks de orquestração para coordenar agentes especializados (ingestor, planner, monitor, insighter, supervisor): BMAD (escolhido inicialmente) vs. LangChain Agents vs. self-rolled. **Descartado completamente** na fusão — BMAD provou ser overhead operacional alto, sem ROI claro para single-user.
- Estudado padrão MV3 do Chrome para service workers (keepalive 25s, alarms para wake-up, offscreen documents para audio em background). Conclusão registrada em ADRs.

### Planejamento e modelagem

- **`PRD/PLANO_ARQUITETURAL_v2.md`** — desenho arquitetural com fluxos `(CSV/Browser) → ingestor → DB → (planner|monitor|insighter|supervisor) → (UI|alerts|narrativas)`. Decompôs o problema em 31 stories agrupadas em 4 épicos.
- **Schema das 9 tabelas** desenhado antes de codar: invariantes claras (cada `trade` tem `account_id`; cada `daily_plan` tem `plan_date + contract + direction` único; `live_snapshots` é append-only com TTL).
- **Janelas operacionais DST-aware** modeladas como tabela `trade_windows` em vez de cálculo runtime — recomputar fuso em leitura é caro em queries de alta frequência.

### Feature engineering / modelagem de dados

- **Overlap grouping engine** (`processor.py:158-190`): dois trades pertencem ao mesmo "grupo lógico" (uma operação) se compartilham `(contract_name, type)` e o `entered_at` do novo é ≤ ao `exited_at` máximo do grupo. Variável derivada `additions_count = trade_count - 1`. Esta lógica foi adotada **byte-a-byte** pelo BI TopStep em `metrics.compute_groups`.
- **FIFO reconstruction** do Dashboard parser: reconstrói posição corrente a partir de uma sequência de fills, mantendo fila FIFO.
- **Status do grupo** derivado: `Winner | Loser | Flat` baseado em `total_points`, não em `pnl` — comissões/fees variam por contrato e poluem leitura comportamental.
- **PII filter no logging JSONL** — campos `account_id`, `email`, `token` truncados/hashed antes de gravar.

### Uso estratégico de IA / automação

- **BMAD orchestrator (Claude Agent SDK)** usado para gerar boilerplate de Alembic migrations, ADRs, structure de stories. Reduziu tempo, mas exigiu **curadoria humana intensa** — qualidade caiu quando o agente extrapolava para decisões arquiteturais. Insight: usar IA para "começar" arquivos, nunca para "fechar".
- **CLI Claude para narrativas** chegou a ser prototipado mas descartado: latência alta, custo por execução, e o operador não estava lendo. Substituído por geração de prompt copy-paste no BI TopStep.
- **Smoke test sintético** em `/dev/metrics`: gera trades fake + injeta na pipeline para validar end-to-end sem CSV real.

### Trade-offs analisados

- **SQLite vs. Postgres**: SQLite escolhido por simplicidade local-first. **Trade-off explícito:** sem multi-tenant, sem RLS. Resolvido na fusão.
- **Next.js vs. Streamlit**: Next.js escolhido pelo polimento; descartado na fusão porque BI TopStep já tinha Streamlit em produção próxima.
- **WebSocket próprio vs. SSE vs. Supabase Realtime**: WS próprio escolhido por controle fino do envelope. Descartado depois — Supabase Realtime cobre com zero processo adicional.
- **Single-user permanente vs. evoluir para SaaS**: resolvida em 2026-05-20 com a fusão.

### Hipóteses validadas / refutadas

- ✅ **Validada:** detecção de adições não-planejadas via overlap temporal funciona em dados reais.
- ✅ **Validada:** Chrome MV3 service worker dorme em ~30s — keepalive de 25s é necessário (não opcional).
- ❌ **Refutada:** "BMAD orchestrator vale o overhead" — em projeto single-developer com escopo bem definido, o overhead se mostrou maior que o ganho.
- ❌ **Refutada:** "frontend Next.js polido é diferencial" — em produto pessoal, Streamlit cobre 95% do valor com 10% do esforço.
- ❌ **Refutada:** "narrativas geradas por IA são lidas pelo operador" — durou 1 semana; depois ignoradas. Coach copy-paste no BI TopStep mostrou que o valor está no **prompt estruturado**, não na resposta gerada.

## 4. Áreas / pessoas beneficiadas

**Diretamente:** o próprio operador (single-user). O projeto nunca chegou a usuário externo.

**Indiretamente (via fusão):**
- Traders TopStep que vierem a assinar o BI TopStep — herdam toda a inteligência comportamental (overlap grouping, adições não-planejadas, dual-timezone, Risk Guard) sem ter que reimplementar.
- Time do BI TopStep ganhou ~3 meses de cabeça em domain knowledge.

## 5. Como usar (consulta histórica)

**Não use mais para evoluir funcionalidades.** Trade_Agent está em vias de arquivamento.

```bash
cd E:\BD\Trade_Agent

# Estado final pré-fusão
git checkout v-final-pre-merge
git show v-final-pre-merge --stat

# Componente migrado mais importante:
ls "Legacy/Dashboard Trade Pontos/backend/core/processor.py"

# Schema das 9 tabelas (SQLite)
ls db/migrations/

# Extensao Chrome MV3 (referencia do shape antigo)
ls Legacy/topstepx-monitor-extension/

git checkout main
```

Credenciais ficavam em `Env/` (não commitado).

## 6. Regras de operação

- **NÃO commitar** em `main` a partir de 2026-05-20.
- **NÃO instalar** a extensão Chrome em produção operacional — tem bug P0 de alertas. Use a versão nova em `e:\BD\260502 BI TopStep\extension\`.
- **NÃO** rodar `iniciar_dashboard.bat` em paralelo com o BI TopStep — portas próximas podem colidir.
- Restrição de plataforma: Windows-only (Task Scheduler, batch files). Eliminada na migração.

## 7. Tecnologias utilizadas

| Camada | Tecnologia | Versão | Por quê |
|---|---|---|---|
| Backend | FastAPI | 0.111+ | Tipagem forte, OpenAPI grátis, async nativo |
| Backend | Pydantic v2 | 2.x | Validação de DTOs no boundary HTTP |
| Backend | Pydantic Settings | 2.x | Config via `.env` sem boilerplate |
| Banco | SQLite | 3.x (WAL mode) | Local-first; sem Postgres no escopo single-user |
| Migrations | Alembic | 1.13+ | Migrations versionadas mesmo em SQLite |
| Real-time | WebSocket (FastAPI) | nativo | Push de eventos para extensão + frontend |
| Frontend | Next.js | 14 | Tokens OKLCH + estrutura para futuro (descartado) |
| Frontend | Tailwind | 3.x | Velocidade de prototipação |
| Extensão | Chrome MV3 (vanilla JS) | — | Sem framework — manifest minimal |
| Orquestração | Claude Agent SDK + BMAD pattern | — | Coordenação de agentes (descartado) |
| Testes | pytest + pytest-asyncio | 8.x / 0.23 | Async para FastAPI |
| Lint | ruff + pre-commit | latest | Padrão Python |
| Logging | JSONL rotativo + PII filter | custom | Auditabilidade local sem ELK |
| CI | GitHub Actions | — | Python 3.11 + 3.13 matrix |

## 8. Decisões-chave

- **2026-05-05** — Stack `FastAPI + SQLite + Next.js + Chrome MV3` para iterar local antes de SaaS.
- **2026-05-06** — Política `main-only` para reduzir cognitive load do single-dev.
- **2026-05-06** — Adoção do BMAD orchestrator para coordenar agentes.
- **2026-05-07** — `trading_windows` DST-aware como tabela (não cálculo runtime).
- **2026-05-07** — WebSocket envelope com `event_type + payload + correlation_id`.
- **2026-05-07** — Logging JSONL com PII filter.
- **2026-05-20** — **Fusão com BI TopStep aprovada.** Direção: BI TopStep absorve Trade_Agent. Real-time entra como MVP via Supabase Realtime. Coach IA segue copy-paste. (Detalhe em `e:\BD\260502 BI TopStep\DECISOES.md`.)

## 9. Histórico Git

- **Repositório:** local em `E:\BD\Trade_Agent`. **Sem remote configurado** — projeto nunca foi publicado.
- **Branch principal:** `main` (única branch significativa).
- **Tag final entregue:** `v-final-pre-merge` (commit `45d6f91`, 2026-05-20).
- **Tag de arquivamento sugerida:** `v-archived` (criar manualmente — ver "Passos manuais").
- **Total de commits:** 36.

**Top 10 commits estratégicos:**

| # | Hash | Data | Mensagem | Por que é marco |
|---|---|---|---|---|
| 1 | `e55a152` | 2026-05-05 | first commit | Primeira pedra do projeto |
| 2 | `7f9d565` | 2026-05-05 | feat(orchestrator): MVP do orquestrador BMAD + Claude Agent | Escolha de stack inicial — depois revertida |
| 3 | `e145aee` | 2026-05-06 | feat(api): porta backend Legacy para `api/` + pydantic-settings | Migração funcional do processor.py para API moderna |
| 4 | `3700433` | 2026-05-07 | feat(db): inicializa Alembic + SQLite WAL + 9 tabelas | Schema completo desenhado e versionado |
| 5 | `45413d9` | 2026-05-07 | feat(frontend,extension): port Legacy + tokens OKLCH + layout | Frontend Next.js + extensão conectados |
| 6 | `ed34569` | 2026-05-07 | feat(api,ws): WebSocket envelope + event bus + correlation | Backbone de real-time |
| 7 | `ddbd6f6` | 2026-05-07 | feat(ingestion): watchdog + TopstepX adapter | Ingestão batch funcionando |
| 8 | `30f60ca` | 2026-05-07 | feat(time): trading_windows DST-aware | Peça crítica de fuso (migrada para BI TopStep) |
| 9 | `45d6f91` | 2026-05-20 | wip(extension): snapshot final pre-fusao com BI TopStep | **Tag `v-final-pre-merge` aponta aqui** |
| 10 | `1089f6d` | 2026-05-20 | docs: cria MEMORIA.md registrando snapshot pre-fusao | Encerra o diário operacional |

## 10. Horas trabalhadas

Sem registro preciso. Estimativa:

| Fase | Período | Horas |
|---|---|---|
| Descoberta + arquitetura (PLANO_v1 → v2, ADRs, stories) | 2026-05-05 → 2026-05-06 | ~10h |
| Backend FastAPI + DB + Alembic + WS + observability | 2026-05-06 → 2026-05-07 | ~25h |
| Frontend Next.js + extensão Chrome MV3 | 2026-05-07 | ~10h |
| Bugfix extensão + observability + supervisor + accounts CRUD | 2026-05-07 | ~10h |
| Pausa + análise comparativa BI TopStep ↔ Trade_Agent | 2026-05-08 → 2026-05-19 | ~5h |
| Snapshot final + MEMORIA + ENCERRAMENTO | 2026-05-20 | ~3h |
| **Total estimado** | — | **~63h** |

Faixa razoável: **55–70h**. Boa parte do código gerado com Claude Agent SDK; curadoria humana pesada nas decisões arquiteturais.

## 11. Próximos passos / manutenção

**Manutenção:** zero. Repo arquivado, read-only.

**Possíveis revisitas (no BI TopStep, não aqui):**
- Bug P0 da extensão: a extensão nova do BI TopStep foi reescrita do zero; bug morreu junto. Se algo similar reaparecer, voltar nos commits `d2fab5d` e `d9d6dbe` do Trade_Agent.
- Coach IA automático: pesquisado e descartado. Se retomado no BI TopStep, ler `Legacy/cli/` do Trade_Agent.
- BMAD orchestrator: descartado para single-user; reavaliar se BI TopStep crescer para um time.

## 12. Passos manuais para arquivar (ATENÇÃO — REQUER AUTORIZAÇÃO HUMANA)

Para fechar oficialmente o Trade_Agent, execute manualmente (precisa confirmação humana porque mexe em git de outro repo):

```bash
cd E:\BD\Trade_Agent

# 1. Copiar este documento para a raiz do Trade_Agent
cp "e:\BD\260502 BI TopStep\PRD\ENCERRAMENTO_trade_agent.md" "ENCERRAMENTO.md"

# 2. Editar README.md adicionando aviso de arquivamento na primeira linha:
#    > ⚠️ ARQUIVADO em 2026-05-20 — funcionalidades migradas para BI TopStep
#    > (e:\BD\260502 BI TopStep). Ver ENCERRAMENTO.md.

# 3. Commit final
git add ENCERRAMENTO.md README.md
git commit -m "docs: ENCERRAMENTO.md + aviso de arquivamento (fusao com BI TopStep)"

# 4. Tag de arquivamento
git tag -a v-archived -m "Projeto arquivado — funcionalidades migradas para BI TopStep"

# 5. Verificar tags
git tag --list
# esperado: v-archived  v-final-pre-merge
```

Após isso, o Trade_Agent fica read-only de fato.

---

*Documento gerado em 2026-05-20 como parte do encerramento operacional do projeto Trade_Agent (cópia de referência guardada no BI TopStep).*
