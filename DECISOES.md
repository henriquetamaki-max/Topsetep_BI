# Decisões — BI TopStep

Registro cronológico de decisões arquiteturais/técnicas/de produto. Entradas
mais recentes no topo. Datas em `AAAA-MM-DD`.

---

## 2026-05-29 — Release 3.0: Risk Planner pré-trade (arquitetura)

**Contexto:** a 2.0 só avaliava risco retrospectivamente (Risk Guard reativo em `live_snapshots`). A 3.0 muda para planejamento pré-trade: dado saldo + limite de blowout, simular sizing/stops/nº de trades + probabilidade de quebra por ativo, e alimentar o plano matinal.

**Decisões:**

1. **Linha 3.0 isolada em worktree git** (`release/3.0` em `E:\BD\X-Metrics 3.0`), 2.0 mantida em `main` + tag `v2.0.0`. Razão: evoluir 3.0 sem misturar com manutenção da 2.0; `.git` compartilhado, working trees separados.

2. **Saldo + MLL por input manual diário, não em `accounts`.** `accounts` é o mapeamento conta-TopStep→user; saldo é estado financeiro diário e mora no header `risk_plans` (1 linha por `plan_date`). Evita acoplar a um campo `balance` que precisaria de integração de API.

3. **Schema em duas tabelas** (`risk_plans` header + `risk_plan_assets` linha-por-ativo) em vez de um único jsonb. Razão: comparativo consultável + mapeamento 1:1 para `daily_plans`. `result_snapshot jsonb` guarda só o agregado Monte Carlo.

4. **Engine puro (`risk_engine.py`) separado do CRUD/UI.** Sem Streamlit/Supabase: toda a matemática testável isolada (37 testes). Monte Carlo **determinístico** via `numpy.default_rng(seed)` — reprodutível e snapshotável.

5. **Trailing MLL modelado fiel ao TopStep**: Combine traila pelo pico intraday; Express Funded (XFA) traila pelo EOD e trava no saldo inicial ("$0 net"). Buffers 2/3/4.5K (50/100/150K) coincidem com `risk_settings.trailing_drawdown_usd` já existente.

6. **Gravação no `daily_plans` reusa `daily_plan.upsert_plans`** (diff + injeção user_id já testados), respeitando a UNIQUE `(user_id,plan_date,contract_name,direction)`: skip sem `overwrite`, update com `overwrite`. Não duplica o motor de persistência.

**Consequências:** `numpy` agora é dependência explícita. Limitação aceita no MVP: teto de contratos aplicado por-ativo (soma de equivalentes micro/mini = backlog). Per-asset `p_blowout` no comparativo desligado por default (1 Monte Carlo por linha é caro).

## 2026-05-21 — Hardening pré-deploy + reversões de escopo do loop autônomo

**Contexto:** após codar as 4 fases da fusão, o usuário pediu loop autônomo para evoluir o que desse sem depender dos passos manuais de deploy (bucket Storage, `supabase functions deploy`, archive Trade_Agent). Várias decisões pequenas mas com impacto futuro foram tomadas sem nova rodada de Q&A.

**Decisões:**

1. **i18n da extensão Chrome expandido para 3 idiomas (en + pt_BR + es).** Reverte explicitamente a decisão #22 da entrada de 2026-05-20 ("apenas EN no MVP"). Razão: ferramentas do Chrome (`chrome.i18n.getMessage` + `_locales/<lang>/messages.json` + atributos `data-i18n` no popup) tornam a tradução barata (19 chaves) e a paridade com o app principal vale o custo. Procedimento documentado e testado por `tests/test_i18n_consistency.py`.

2. **M11 — UNIQUE `(user_id, account_id, snapshot_at)` em `live_snapshots`** (não estava no plano original). Edge Function `live-ingest` migrou de `.insert` para `.upsert({ onConflict, ignoreDuplicates: true })`. Razão: retries da extensão (rede instável, worker re-acordando) estavam inflando duplicatas. Sem isso, o dashboard ficaria sujo após primeira semana de uso.

3. **Hardening defensivo da `live-ingest`** com 4 camadas:
   - Rate limit em memória 12 req/min por `user_id` (HTTP 429). Cobre heartbeat 30s + eventos com folga.
   - Cap de payload 16KB (HTTP 413). Snapshots legítimos ficam em 1-2KB.
   - Validação de skew ±5min do `snapshot_at` (HTTP 400). Rejeita backfill e clock-skew obviamente errados.
   - Clamp de ranges em `position_size`, PnL/drawdown, `account_id.length`. Bug na extensão envia dados absurdos? São truncados silenciosamente em vez de derrubar a função.

   Rate limit é **em memória do worker** (reseta em cold start). Decisão consciente: para abuso casual basta; flood distribuído verdadeiro precisa de Redis/Postgres (backlog se virar gargalo).

4. **Logs estruturados JSON na `live-ingest`** com 11 eventos (`ping_ok`, `ingest_ok`, `auth_*`, `rate_limited`, `payload_too_large`, `invalid_*`, `missing_field`, `db_error`). `raw` do snapshot **nunca** é logado por privacidade. Filtragem via Logflare/SQL. Decisão de não usar uma biblioteca de log (pino, etc.) — função simples, custo de adoção > ganho.

5. **Validação estrutural de JWT no popup** (`validateJwt` em `extension/popup.js`). Sem a chave HMAC do Supabase **não dá** para verificar assinatura no cliente — isso é função do servidor. Mas checar shape (3 partes base64url + header HS\*) + claims (`sub` uuid, `exp/iat`, `aud=authenticated`) + `exp` > now bloqueia 3 erros de UX comuns: cola de texto errado virando "Signed in as undefined", JWT expirado exibido como válido, payload do projeto errado. Botão Save bloqueia ANTES de chamar o servidor.

6. **Refactor `_supabase()` / `_current_user_id()` → `auth.get_client()` / `auth.current_user_id()`**. Os 4-liners viviam em `coach_ai.py` por inércia histórica (foi o primeiro módulo a precisar). Migrei para `auth.py` (onde semanticamente cabem) e atualizei 5 módulos (daily_plan, action_plan, risk_settings, live, app via coach_ai). Wrappers em `coach_ai.py` ficaram como aliases delegando — sem quebra de compat.

7. **Day Plan ganhou "Copiar do dia anterior" + "Limpar dia"** (com confirmação 2-cliques). Não eram parte do plano da fusão; apareceram como atrito real ao usar a UI durante o smoke test mental. Mantém o CRUD enxuto (sem widget de "duplicar linha", etc.).

8. **Stacked bar visual da aderência** dentro do expander do Dashboard, mostrando proporção das 5 categorias. Decidi não criar gráfico em aba dedicada — visual leve dentro do contexto onde os números já estão.

**Alternativas consideradas:**

- **i18n só EN** (manter decisão original): rejeitado depois de constatar que `_locales/` é o mecanismo nativo do Chrome — esforço de 30min pra 3 idiomas.
- **Rate limit em Postgres/Redis** desde o MVP: rejeitado por overkill. Em memória cobre o cenário realista (1 trader, 1 extensão, ocasional flood acidental).
- **Verificar assinatura HMAC do JWT no popup** (entregando a chave para o cliente): **proibido por design** — quebraria o modelo de segurança. Server-side resolve.
- **Refactor maior do `coach_ai.py`** (separar wrappers, helpers, prompt builder em módulos): rejeitado por escopo. Refactor cirúrgico (mover 2 funções) basta.

**Consequências:**

- 57 testes unitários verdes (eram 33 no início do loop). Cobertura cresceu em `daily_plan`, `risk_settings`, `alerts`, `metrics`, `timezones`, `i18n_consistency`.
- 1 nova migration aplicada (M11). Banco está agora idempotente para snapshots.
- Edge Function `live-ingest` mais robusta — pronta para deploy real.
- Extensão pronta para 3 idiomas; zip atualizado em `dist/extension/extension-latest.zip`.
- Aba Day Plan menos frustrante (CRUD reutilizável dia-a-dia).
- Refactor de auth abre porta para mover outras utilities para `auth.py` no futuro sem quebra.

**Referência:** [MEMORIA.md](MEMORIA.md) seção "Estado atual" (2026-05-21); [guia_execucao.md](guia_execucao.md) §0.5 (Status de execução); `PRD/SMOKE_TEST_live.md` para validar end-to-end pós-deploy.

---

## 2026-05-20 — Cleanup Trade_Agent: ENCERRAMENTO.md fica como cópia no BI TopStep

**Contexto:** Fase 4 do guia (cleanup) previa criar `ENCERRAMENTO.md` direto na raiz do Trade_Agent + warning no README + tag `v-archived`. A criação foi tentada via Write/Bash mas bloqueada pelo classificador de segurança: alterar repo externo (com history e tags) sem autorização explícita do humano é ação irreversível com blast radius alto.

**Decisão:** o post-mortem foi gerado como `PRD/ENCERRAMENTO_trade_agent.md` aqui no BI TopStep (cópia de referência preservada na memória institucional da fusão), com a seção 12 detalhando os 5 passos manuais que o humano deve executar (`cp` para o Trade_Agent + edit README + git add/commit + git tag).

**Alternativas consideradas:**
- **Forçar pela bypass:** rejeitado por princípio (CLAUDE.md global proíbe ações destrutivas em git sem autorização).
- **Pedir autorização explícita interativa:** descartado porque o usuário disse "siga adiante" e "trabalhar sem perguntar" — preservar a autonomia mas em zona segura é melhor que parar.
- **Postergar Fase 4 para outra sessão:** rejeitado porque o post-mortem em si era valioso (captura estado mental atual), mesmo sem mover bytes no outro repo.

**Consequências:**
- Trade_Agent fica em estado "soft-archived": tag `v-final-pre-merge` existe; tag `v-archived` ainda não. Read-only é convenção, não enforced.
- BI TopStep mantém o post-mortem dentro dele — quando alguém revisitar o histórico daqui a 6 meses, não precisa navegar até `E:\BD\Trade_Agent` para entender o que migrou.
- Se um dia o BI TopStep for clonado/portado, o post-mortem viaja junto naturalmente.

**Referência:** `PRD/ENCERRAMENTO_trade_agent.md`, seção 12 "Passos manuais para arquivar".

---

## 2026-05-20 — Decisões operacionais da fusão (consolidadas para o `guia_execucao.md`)

**Contexto:** com a fusão BI TopStep ← Trade_Agent já aprovada (entrada abaixo), faltava aterrissar as decisões intermediárias que destravam a execução. Sete rodadas estruturadas com o usuário fecharam o quadro. Esta entrada documenta apenas as decisões com impacto arquitetural ou de produto (escolhas óbvias ou puramente táticas vão direto no `guia_execucao.md`).

**Decisões:**

1. **Ordem de fases no guia:** 1.3 → 1.2 → 1.1 → 2 → 3 → 4. Schemas Postgres primeiro porque destravam tudo (live_snapshots/alerts/tilt_patterns/payouts/risk_settings/accounts/contracts). Refatorar a detecção de adições só depois que dual-timezone estabilizar.
2. **Auth da extensão Chrome:** JWT colado manualmente pelo trader (botão "gerar token de extensão" na aba Account). Sem OAuth in-extension no MVP. Token renovado a cada login do app — trader recola.
3. **Pricing da feature live:** **plano Pro** (US$ 49/mês). Trial inclui. Plano Basic (US$ 19) fica só com batch. Implementa-se via `features.live_monitor` em `public.plans`.
4. **Risk Guard MVP:** 4 regras — Daily Loss Limit, Trailing Drawdown, Max Position Size, Adição não-planejada. Outras regras (consecutive losses lockout, time-out forçado, etc.) ficam em backlog explícito.
5. **Modelagem de `live_snapshots`:** híbrido colunar + jsonb. Colunas tipadas para os 9 campos críticos (position_size/avg_price/contract/side, unrealized/realized/day pnl, drawdown, snapshot_at) + `raw jsonb` para evolução do payload sem migration. Index `(user_id, snapshot_at desc)`. Retenção **7 dias** via job `pg_cron` diário.
6. **Modelagem de `alerts`:** enum `alert_type` (6 valores) + enum `alert_severity` (info/warn/critical) + enum `alert_source` (risk_guard/metric/manual). Ciclo de vida explícito (`read_at`, `dismissed_at`). Index parcial em unread.
7. **Modelagem de `tilt_patterns`:** histórico de detecções (1 linha = 1 detecção), com `context jsonb` carregando os trades/grupos envolvidos. Permite trend histórico.
8. **`payouts`:** rastreio manual do trader (CRUD simples). Sem integração com API TopStep — só campos suficientes para registrar request/paid/amount/status/notes.
9. **POINT_VALUE_USD multi-contrato:** nova tabela `public.contracts` com seed de 12 símbolos (micro + full size dos índices, energia, ouro). Substitui o dict hardcoded em `daily_plan.py`. Permite adicionar contrato sem deploy.
10. **Detecção de adições não-planejadas:** refinar a `compute_plan_adherence` atual portando 3-5 regras adicionais do `processor.py` Legacy do Trade_Agent (addition_against_plan, size_creep, addition_after_stop). Não substituir a função inteira.
11. **Dual-timezone:** primário fixo `America/New_York` (sessão CME). Secundário = fuso do usuário, auto-detectado via `Intl.DateTimeFormat().resolvedOptions().timeZone` no primeiro login; sobrescrevível na aba Configurações; persistido em `user_metadata.preferred_tz`. Aplicar em: `trade_day_et` derivado no Dashboard, cards Best/Worst Trade, tabela de trades, alertas e timestamps de live_snapshots. **Não** mudar sidebar/Day Plan no MVP (eles continuam em BRT/Chicago atual — revisitar se gerar inconsistência).
12. **Tela de configurações:** **nova aba "Configurações"** dedicada (idioma, fuso, Risk Guard, contas TopStep). Separa de Account (billing/plano). Visível para todos; seção Risk Guard só para Pro/Trial.
13. **Tabela `risk_settings`:** user_id PK + `account_type` (Express 50K/100K/150K/Custom) + DLL + trailing DD + max_position_size + warning_threshold_pct. CRUD na aba Configurações.
14. **Conta TopStep por trader:** 1 ativa por usuário no MVP. Tabela `accounts` com flag `active`. Múltiplas contas ativas é backlog.
15. **Snapshot final do Trade_Agent (T0):** primeira tarefa do guia. Tag `v-final-pre-merge`. Repo só vira read-only na Fase 4 — durante a fusão, segue editavel para consultas.
16. **Frequência de snapshot da extensão:** 30 segundos heartbeat (via `chrome.alarms.create`) + push imediato quando há mudança relevante (posição altera, PnL passa threshold, trade fecha). Balanceia latência vs custo.
17. **Distribuição da extensão:** **load unpacked dev only** no MVP. Trader baixa `.zip` da aba Live, carrega em `chrome://extensions` modo dev. Chrome Web Store fica como backlog (depois de validar 5-10 usuários).
18. **Realtime no Streamlit:** **polling controlado** via `streamlit-autorefresh` (3s) na aba Live. Não componente custom complexo. Web Notifications complementa: componente HTML/JS leve embutido via `st.components.v1.html` escuta `postgres_changes` da tabela `alerts` e dispara `Notification` API quando severity=critical.
19. **Trigger Risk Guard:** **trigger Postgres `AFTER INSERT` em `live_snapshots`** que consulta `risk_settings`, avalia regras e insere em `alerts`. Sem Edge Function `risk-guard` no MVP — fica tudo no banco (latência mínima, sem custo de invocação). Edge Function vira backlog se a lógica crescer.
20. **Dados históricos do Trade_Agent local:** descartar. Sem script de migração. Operador (Henrique) ingere CSVs reais pela UI normal.
21. **Aba Live (Pro):** widgets próprios (status, posição, alertas, instalação). Aderência ao plano matinal **permanece no Dashboard** (batch, como está hoje).
22. **i18n da extensão:** apenas EN no MVP (popup tem poucas strings). PT-BR/ES vira backlog.
23. **Selectors do TopstepX:** migrar `selectors.json` do Trade_Agent como está; popup sinaliza "selectors desatualizados" quando scrape falha repetidamente (3 ciclos consecutivos).
24. **Estrutura da pasta `extension/`:** background + content + popup + offscreen + selectors + icons + novo `config.js` com `SUPABASE_URL`/`LIVE_INGEST_URL` parametrizáveis.

**Consequências:**

- O `guia_execucao.md` na raiz tem ~30 tarefas auto-contidas (T0 a T4.4) com branches sugeridas, SQL, snippets de Python/JS, critérios de pronto e commits. Pode ser executado sequencialmente sem novas decisões críticas — apenas táticas finas.
- Sete novas tabelas (`contracts`, `live_snapshots`, `alerts`, `tilt_patterns`, `payouts`, `risk_settings`, `accounts`) entram no Postgres do Supabase, todas com `user_id` + RLS.
- Uma nova Edge Function (`live-ingest`) e um trigger plpgsql (`risk_guard_eval`) entram em produção.
- Nova aba "Configurações" e nova aba "Live" no Streamlit; aba Account ganha botão "gerar JWT extensão" visível só para Pro/Trial.
- Snapshot/tag final do Trade_Agent vira T0 (pré-requisito); arquivamento real ocorre só em Fase 4.

**Alternativas consideradas (resumidas):**

- Real-time via componente JS custom (em vez de polling): rejeitado pelo custo de manutenção; polling + Web Notifications cobrem o caso de uso.
- OAuth in-extension: rejeitado para o MVP; entra como backlog quando houver volume suficiente de traders para justificar.
- Edge Function dedicada para Risk Guard: rejeitada porque o trigger Postgres tem latência menor e zero custo de invocação; pode ser introduzida se a lógica crescer.
- Importar dados históricos do Trade_Agent: rejeitado porque eram de teste; operador re-ingere CSVs reais pela UI.

**Referência:** `guia_execucao.md` na raiz; plano da rodada em `~/.claude/plans/favor-ler-o-todos-purring-cupcake.md`.

---

## 2026-05-20 — Fusão: BI TopStep absorve Trade_Agent (com real-time MVP)

**Contexto:** Dois projetos paralelos no domínio TopStepX vinham evoluindo em silos.
**BI TopStep** (este repo) é SaaS multi-tenant pós-trade em produção próxima
(M1–M3 entregues, M4 webhook Stripe em curso). **Trade_Agent** (`E:\BD\Trade_Agent`)
é sistema local single-user focado em comportamento (anti-tilt, real-time via
WebSocket + extensão Chrome MV3 + toast Windows), em **pré-Fase-1**: fundação
documentada + Legacy funcional, restante das pastas vazias. Sobreposição clara
(parsing CSV TopStepX, métricas em pontos, "coach" comportamental) e
complementaridade forte (extensão Chrome, processador de adições, multi-tenancy/RLS/Stripe).

**Decisão:** BI TopStep **absorve** Trade_Agent. Migrar componentes específicos
(extensão Chrome, detecção de adições não-planejadas, schema rico, janelas
dual-timezone, Risk Guard) para dentro deste repo. **Real-time entra como MVP**
via Supabase Realtime (`postgres_changes`) + Edge Functions Deno + Web
Notifications API no navegador. Trade_Agent será **arquivado** após migração.
**Coach IA continua prompt copy-paste** (sem custo de LLM no SaaS).

**Alternativas consideradas:**

- **Trade_Agent absorve BI TopStep**: levar SaaS/multi-tenant/Stripe para o
  FastAPI do Trade_Agent. **Descartada** porque exigiria refazer auth, RLS,
  billing e dashboard que já estão em produção próxima aqui — alto retrabalho.
- **Projeto novo unificado**: terceiro repo combinando o melhor dos dois.
  **Descartada** por risco de "rewrite trap" e perda de velocidade do M1–M3
  já entregue; ambos os repos virariam legado simultaneamente.
- **Real-time via Streamlit + sidecar FastAPI/WebSocket**: caminho técnico
  viável. **Descartada como primeira opção** em favor de Supabase Realtime
  (zero processo adicional, já no stack). Fica como backup.
- **Real-time via migração para Next.js**: solução ideal arquiteturalmente.
  **Descartada agora** por custo alto (refactor do dashboard inteiro).

**Consequências:**

- M4 Stripe webhook vira **pré-requisito** da fusão — bloqueia Fase 1.
- Extensão Chrome migrada precisa autenticar via Supabase JWT e respeitar RLS;
  isolamento de dados entre traders é responsabilidade do banco, não da extensão.
- Schema Postgres ganha novas tabelas (`live_snapshots`, `alerts`, `tilt_patterns`,
  `daily_plans`, `payouts`), todas com `user_id` + RLS.
- Toast Windows nativo (Trade_Agent local) é substituído por **Web Notifications API**
  — em SaaS hosted não há acesso a toast nativo do SO.
- Streamlit assina Supabase Realtime para widgets live; alertas críticos disparam
  notificação no navegador via Permissions API. Latência alvo: < 2s.
- Trade_Agent vira repo read-only com `ENCERRAMENTO.md` apontando para os
  componentes migrados. Histórico técnico (`MEMORIA.md`, `DECISOES.md` do
  Trade_Agent) preservado lá como referência.
- Coach IA permanecendo copy-paste mantém custo de LLM em zero, mas limita
  o produto a uma camada "geração de prompt" — feature de coach automático
  com Claude API fica em backlog explícito.
- Decisões em aberto a resolver antes da Fase 2: (a) auth da extensão Chrome
  (token colado vs OAuth in-extension), (b) pricing da feature live por plano,
  (c) quais regras TopStep entram no Risk Guard MVP, (d) destino dos dados
  históricos do Trade_Agent local.

**Roadmap em 4 fases** (resumo): Fase 0 — pré-requisitos (M4 + snapshot
Trade_Agent); Fase 1 — batch (detecção de adições, dual-timezone, schema novo);
Fase 2 — extensão Chrome multi-tenant; Fase 3 — real-time + Risk Guard;
Fase 4 — cleanup (arquivamento + `ENCERRAMENTO.md`).

**Referência:** plano detalhado em
`C:\Users\henrique.tamaki\.claude\plans\me-fa-a-um-comparativo-soft-bear.md`.

---

## 2026-05-20 — Userscript Pullbacks MNQ: manter single-file + CHANGELOG lateral

**Contexto:** O port JS do indicador Pine `Pullbacks MNQ - Bone Zone v7.1`
(`PRD/topstepx_pullback_mnq.user.js`) cresceu de ~550 linhas (v0.2.x) para
~700 linhas (v0.3.0) com a reorganização em blocos coesos (`CONFIG`, `LOGGING`,
`AUDIO`, `BOOTSTRAP`, `INDICATOR`, `CONSTRUCTOR`, `UTILS`, `MAIN IMPL`). A
pergunta: para a próxima evolução, manter o modelo single-file colável no
Tampermonkey, ou adotar tooling (bundler como esbuild/rollup) para quebrar
em módulos?

**Decisão:** Manter **single-file colável** no Tampermonkey, com um
`PRD/CHANGELOG_pullback_mnq.md` **lateral** para trilha de versões.
Sem build, sem `package.json`, sem TypeScript, sem dependências externas.

**Alternativas consideradas:**

- **Tooling leve (esbuild/rollup → `.user.js`)**: permitiria split em módulos
  (`bootstrap.js`, `metainfo.js`, `indicator.js`, `utils.js`) e abriria porta
  para TypeScript/testes. **Descartada** porque: (a) muda o contrato "abrir o
  arquivo e ler tudo" — qualquer iteração rápida exige rebuild; (b) introduz
  `node_modules` no projeto (superfície de ataque + manutenção); (c) colide
  com a heurística "userscript = portátil" (`MEMORIA.md` documenta o fluxo
  "colar este arquivo inteiro no Tampermonkey"); (d) é overengineering para
  **um único** indicador isolado — só faria sentido se houvesse intenção de
  portar **vários** indicadores Pine ou compartilhar utilitários entre scripts.
- **Single-file SEM CHANGELOG, só com bloco "Novidades" no header**: simpler
  ainda. **Descartada** porque o header já é longo e cresce mal a cada versão;
  o CHANGELOG lateral mantém o cabeçalho enxuto e dá disciplina de versionamento
  alinhada com a convenção do projeto (`MEMORIA.md` / este `DECISOES.md`).

**Consequências:**

- A cada bump de versão (`@version` no header), atualizar **dois** lugares:
  o header do `.user.js` E `PRD/CHANGELOG_pullback_mnq.md`. Mitigação: regra
  fixa no fluxo de release.
- O arquivo deve permanecer legível como um todo — se passar de ~1000 linhas
  ou se a IIFE começar a virar "kitchen sink", revisitar esta decisão.
- Refactors estruturais futuros (split de responsabilidades) acontecem dentro
  do mesmo arquivo via blocos `// ======== NOME ========`, não via imports.
- Quando houver intenção real de portar um **segundo** indicador Pine, esta
  decisão deve ser revisitada — possivelmente extraindo utilitários
  compartilhados (VWAP CME, log levels, bootstrap) para um arquivo "lib"
  e introduzindo build apenas se necessário.
