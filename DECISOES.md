# Decisões — BI TopStep

Registro cronológico de decisões arquiteturais/técnicas/de produto. Entradas
mais recentes no topo. Datas em `AAAA-MM-DD`.

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
