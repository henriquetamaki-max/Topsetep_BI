# CLAUDE.md

Instruções operacionais para Claude Code (claude.ai/code) ao trabalhar neste repositório. Este arquivo é o contrato: siga-o em cada sessão, mesmo que o usuário não relembre.

Para histórico vivo do projeto (estado atual, decisões cronológicas, gotchas) consulte sempre antes de começar uma tarefa não-trivial:

- [`MEMORIA.md`](MEMORIA.md) — diário operacional e tarefas concluídas com data.
- [`DECISOES.md`](DECISOES.md) — registro de decisões arquiteturais (ADR-lite).
- [`~/.claude/plans/`](C:\Users\henrique.tamaki\.claude\plans) — planos em andamento (ex.: `me-fa-a-um-comparativo-soft-bear.md` é o plano da fusão com Trade_Agent).

---

## Pipeline

CSVs de trades exportados do TopStepX → upload pela UI do app (autenticado) → Supabase Postgres (tabela `public.trades`, isolada por `user_id` via RLS) → dashboard Streamlit.

App é multi-tenant: cada trader faz login (email/senha ou Google via Supabase Auth) e só enxerga os próprios dados. RLS no banco garante o isolamento, não a aplicação.

Existe também o CLI legado `src/ingest.py` para uso local pelo operador (bypass de RLS via service_role); novos usuários devem ingerir pela aba **Importar CSVs** do app.

## Comandos

```bash
# Setup (1x, na raiz do projeto)
python -m venv .venv
.venv\Scripts\activate          # Windows (bash: source .venv/Scripts/activate)
pip install -r requirements.txt

# Rodar o app (dashboard + login + upload)
streamlit run src/app.py

# (Opcional) Ingestão local via CLI legado — lê CSV input/, atribui ao
# INGEST_USER_ID configurado no Env, move para CSV output/.
python src/ingest.py
```

Schema base em `PRD/schema.sql`, SaaS em `PRD/saas_schema.sql`, migrations M5+ em `PRD/m<N>_<feature>.sql`. Todos aplicados manualmente uma vez no Supabase SQL Editor.

---

## Fluxo de trabalho (obrigatório)

### 1. Antes de codar — explorar com subagentes em paralelo

Para qualquer tarefa que toque mais de 1 arquivo ou cuja área você não conhece bem:

- Dispare **2-3 agentes `Explore` em paralelo** (um único turn com múltiplas tool calls) cobrindo aspectos diferentes (ex.: módulo origem, módulo destino, schema, UI). Brief curto por agente, foco no que importa para decidir.
- Para tarefas com arquitetura ambígua ou multi-arquivo, considere também 1 agente `Plan` após os `Explore` para validar o desenho.
- Não use `Bash`/`Glob`/`Grep` direto se a exploração tem >3 queries ou área incerta — delegue ao subagente.
- Subagente é gratuito em token (não conta contra contexto principal) e roda em paralelo. **Use sempre que viável.**

### 2. Quebrar features em sub-PRs sequenciais

Toda feature que toca schema + UI + métrica deve virar 3 commits sequenciais **direto em `main`** (trunk-based — ver seção Git):

- **C-A — Schema**: migration SQL idempotente em `PRD/m<N>_<feature>.sql` + aplicar no Supabase + validar.
- **C-B — UI**: módulo CRUD em `src/<feature>.py` + `render_<feature>()` em `app.py` + nova aba + i18n nos 3 locales + validação manual via Streamlit local.
- **C-C — Métrica/integração**: função em `metrics.py` + integração no Dashboard ou outra aba + i18n + validação end-to-end.

Cada commit é validável isolado (a suíte fica verde a cada passo). Sem branch dedicada.

### 3. Validar antes de commitar

- **Sintaxe**: rodar `.venv/Scripts/python.exe -c "import ast; ast.parse(open('src/<arq>.py').read())"` e o mesmo para JSON de locales.
- **End-to-end na UI**: subir `streamlit run src/app.py` em background, dar F5 no browser e fazer o caminho golden + 1 caso de erro. Se houver mudança em texto traduzido, conferir nos 3 idiomas.
- Não comitar se a UI quebra. Se o erro não é trivial, criar plano de fix antes.

### 4. Atualizar artefatos vivos após cada task não-trivial

- **`MEMORIA.md`** — adicionar entrada datada na seção `Histórico de tarefas concluídas` + atualizar `Estado atual` se relevante. Gotchas encontrados vão na seção própria.
- **`DECISOES.md`** — entrada nova **só se** houve decisão arquitetural/técnica não óbvia (escolha de stack, mudança de padrão, descarte de abordagem). Não para fix trivial.
- **Plano em `~/.claude/plans/`** — atualizar checklist do plano se a tarefa fechou uma fase.

### 5. Skills a invocar quando aplicável

Estas skills estão disponíveis no harness e devem ser usadas proativamente nos momentos certos:

| Skill | Quando invocar |
|---|---|
| `/review` | Antes de mergear PRs grandes (>200 LOC tocando >3 arquivos), especialmente fusões de branch. |
| `/security-review` | **Obrigatório** em qualquer mudança que toque autenticação, billing, RLS, secrets, Edge Functions ou Stripe. |
| `/simplify` | Após implementar feature nova — varre o diff procurando reuso, duplicação, complexidade desnecessária. |
| `/ultrareview` | Para review multi-agente de PRs críticos (rodado pelo usuário, não pelo agente). |
| `/graphify` | Antes de entrar em código novo do projeto (entender estrutura), ou para mapear impacto de refactor grande. |
| `karpathy-guidelines` | Consultar mentalmente antes de implementar — surgical changes, surface assumptions, success criteria. |

---

## Arquitetura

### Estrutura de pastas

```
src/                       # código Python (15 módulos pós-fusão Trade_Agent)
locales/{en,es,pt_BR}.json # i18n — 3 idiomas SEMPRE sincronizados
PRD/                       # schema SQL + PRDs + migrations + userscript + ENCERRAMENTO
supabase/functions/        # Edge Functions Deno (stripe-webhook, live-ingest)
extension/                 # extensão Chrome MV3 (BI TopStep Live Monitor)
scripts/                   # ferramentas (package_extension.py etc.)
Env/                       # segredos LOCAL (gitignored, nunca commitar)
.streamlit/                # secrets.toml para deploy (gitignored)
assets/                    # imagens estáticas (login screen etc.)
dist/                      # builds (.zip da extensão) — gitignored
CSV input/ CSV output/     # legados do CLI ingest.py — gitignored
```

### Autenticação (`src/auth.py`)

- Supabase Auth: email/senha + Google OAuth (PKCE). `login_screen()` bloqueia o app até logar.
- Sessão em `st.session_state["session"]` (dict). `get_client()` devolve um cliente Supabase com o JWT do usuário injetado — todas as queries respeitam RLS.
- Credenciais lidas de `st.secrets` (deploy) ou `Env/Topstep_bi.env` (local). Anon key, **nunca** service_role.

### Ingestão (`src/ingest_core.py` + `src/ingest.py`)

- `ingest_core.py` — funções puras: `detect_format`, `normalize_topstepx`, `normalize_dashboard`, `records_for_supabase(df, user_id)`, `upsert_batches`, `ingest_uploaded_csv(file, client, user_id)`.
- Upload pelo app: aba "Importar CSVs" chama `ingest_uploaded_csv` com o cliente autenticado. `user_id` vem da sessão.
- `ingest.py` CLI: wrapper fino que lê `CSV input/`, usa service_role + `INGEST_USER_ID` do env, move arquivos para `CSV output/` como `YYYYMMDD_N.csv`.
- `_duration_to_pg_interval()` trunca fração `.fffffff` (.NET, 7 casas) para microssegundos (Postgres aceita máx 6).

### Métricas (`src/metrics.py`)

Funções puras (sem Streamlit). Convenção `compute_<area>(df, ...) -> dict | DataFrame`:

- `compute_groups(df) -> (df_anotado, groups_df)` — overlap grouping; gera `group_id`, `additions_count`, `has_addition`.
- `compute_kpis(df, groups) -> dict` — 12 KPIs em pontos.
- `compute_segments(groups) -> dict` — 4 buckets (sem/com adições, vencedoras/perdedoras).
- `compute_daily(df) -> DataFrame` — breakdown por trade_day.
- `compute_overview(df) -> dict` — 12 KPIs em USD.
- `compute_duration_buckets(df) -> DataFrame` — win rate por 11 buckets.
- `compute_coach(df, groups) -> dict` — análise comportamental (revenge, cut-hold, overtrading, losing streak, leaks, strengths).
- `compute_plan_adherence(groups, plans) -> dict` — score % de aderência ao plano matinal (M5).

### CRUD multi-tenant (padrão para tabelas com user_id)

Cada feature CRUD vive em `src/<feature>.py` (puro) + `render_<feature>()` em `app.py`:

- `list_<entity>(...) -> DataFrame` — `client.table(...).select("*").execute()`; RLS filtra.
- `_normalize_row(row) -> dict | None` — validação + tipo coerção; retorna None para linha vazia/inválida.
- `upsert_<entity>(original, edited) -> dict` — diff por `id`; insere/atualiza/deleta em batch; injeta `user_id` em inserts via `coach_ai._current_user_id()`.
- Reusa cliente via `from coach_ai import _supabase, _current_user_id`.

Exemplos: `src/action_plan.py`, `src/daily_plan.py`.

### Schema (Postgres + RLS)

Aplicado manualmente no Supabase SQL Editor, em ordem:

1. `PRD/schema.sql` — `trades`, `coach_analyses`, `action_items`.
2. `PRD/saas_schema.sql` — `plans`, `subscriptions`, `admin_users` (M1-M4).
3. `PRD/m5_daily_plans.sql` — plano matinal (M5).
4. `PRD/m6_*.sql` — schemas da fusão (M6): `contracts`, `live_snapshots` + `purge_old_live_snapshots()`, `alerts` + enums, `tilt_patterns`, `payouts`, `risk_settings`, `accounts`. Ordem em `PRD/m6_README.md`.
5. `PRD/m9_features.sql` — habilita `features.live_monitor=true` em `plans.slug in ('pro','admin','trial')`.
6. `PRD/m10_risk_guard_trigger.sql` — trigger `AFTER INSERT` em `live_snapshots` que avalia 4 regras (DLL, Trailing DD, Max Size, Unplanned Addition) e insere em `alerts` com cooldown anti-spam de 5min.

**Regras absolutas para toda tabela nova:**

- Coluna `user_id uuid not null references auth.users(id) on delete cascade`.
- `enable row level security` + policy `<tabela>_owner_all` com `using (auth.uid() = user_id) with check (...)`.
- PK simples `bigserial` OU composta `(user_id, id)` — composta quando o `id` vem de fonte externa (ex.: TopStepX).
- UNIQUE constraint quando há semântica de chave natural (ex.: `daily_plans`: `unique (user_id, plan_date, contract_name, direction)`).
- Índice `(user_id, <campo de busca frequente>)`.
- Migrations idempotentes: `drop table if exists ... cascade;` no início se a tabela for nova sem dados em produção. Comente explicitamente o risco no cabeçalho.

### Edge Functions (`supabase/functions/`)

Deno + TypeScript. Funções existentes:

- **`stripe-webhook`** — `verify_jwt=false`; autenticação via assinatura HMAC do Stripe. Cliente Supabase usa `SERVICE_ROLE_KEY`. Trata 6 eventos (checkout/subscription/invoice).
- **`live-ingest`** — `verify_jwt=false` no manifest, mas valida o JWT do usuário server-side via `supabase.auth.getUser()` antes de INSERT. Cliente Supabase usa `SERVICE_ROLE_KEY` apenas no INSERT (RLS bypass) — o `user_id` gravado é sempre o do token validado, nunca do payload do cliente. Suporta modo `{ping:true}` para health-check da extensão.

Cada função tem:
- `index.ts` — handler.
- `README.md` — eventos tratados, secrets, deploy, teste local com Stripe CLI / supabase functions serve.
- `.env.example` — todos os secrets esperados (gitignored o `.env` real).

### Extensão Chrome (`extension/`)

MV3 vanilla JS. Estrutura:
- `manifest.json` — name "BI TopStep — Live Monitor", v0.1.0. Permissões enxutas (`storage, alarms, tabs, notifications`).
- `config.js` — URLs do Supabase (preenchidas em build time pelo `scripts/package_extension.py --supabase-url X --anon-key Y`, ou editadas à mão para dev).
- `background.js` — service worker. `chrome.alarms` 30s heartbeat + debounce de 5s entre envios. `sendSnapshot()` via fetch para `live-ingest` Edge Function.
- `content.js` — scrape do DOM `topstepx.com/trade` a cada 5s. Manda `SNAPSHOT_CHANGED` para o background apenas quando há mudança relevante. Marca `scrape_status=broken` após 3 ciclos vazios consecutivos.
- `popup.html/js/css` — UI mínima: textarea de JWT, botões Save/Test, status do scrape, status do último envio.
- `selectors.json` — seletores DOM versionados; trader atualiza quando markup TopstepX muda.
- `README.md` — instruções de load unpacked.

Distribuição (MVP): zip via `scripts/package_extension.py` → `dist/extension/extension-latest.zip` → upload manual no Supabase Storage (bucket público `extension/`). Aba "Live" do app linka para esse zip.

### Aba "Live" (`src/live.py`, plano Pro)

Visível apenas para usuários com `billing.has_feature(plan, "live_monitor")`. Polling via `streamlit-autorefresh` (3s). Sub-seções: status conexão, posição atual, PnL realized/day/drawdown, alertas (cartões coloridos por severity com botões mark_read/dismiss inline + contador de unread), instalação da extensão. Componente JS leve (`_inject_web_notifications`) assina `postgres_changes` da tabela `alerts` via supabase-js (esm.sh) e dispara `Notification` API em INSERTs de severity warn/critical.

### Aba "Configurações" (`src/settings.py`)

Idioma (reusa `i18n.language_selector`), fuso (primário ET fixo, secundário auto-detectado por JS + override manual, persiste em `user_metadata.preferred_tz`), Risk Guard CRUD (5 inputs com defaults sugeridos por account_type; persiste em `risk_settings`), Contas TopStep (stub — backlog Fase 3.b).

### Risk Guard

Trigger Postgres `risk_guard_eval` em `live_snapshots` (criado por `PRD/m10_risk_guard_trigger.sql`) avalia 4 regras consultando `risk_settings` + `daily_plans` (em fuso NY/ET) e insere em `alerts` via helper `_rg_insert_alert` com **cooldown de 5min** por (user_id, alert_type) para anti-spam. Sem Edge Function dedicada — toda a lógica vive no banco para latência mínima.

---

## Convenções de código

### Naming

- Funções públicas em `metrics.py` começam com `compute_<area>`.
- Funções de listagem em módulos CRUD: `list_<entity>`.
- Funções privadas com `_` prefix.
- Render functions em `app.py`: `render_<area>()`.
- Tabelas SQL: `snake_case` plural (`daily_plans`, `coach_analyses`).
- Locale keys: `<contexto>.<sub>.<chave>` (ex.: `dash.adherence.score`, `dayplan.col.contract`).

### i18n — sempre 3 idiomas sincronizados

- Toda string visível na UI deve passar por `t("chave")` (módulo `i18n`).
- Adicionar a chave nos 3 arquivos: `locales/en.json`, `locales/pt_BR.json`, `locales/es.json`. Nunca um só.
- Antes de commitar, validar JSON: `.venv/Scripts/python.exe -c "import json; [json.load(open(f'locales/{l}.json',encoding='utf-8')) for l in ('en','es','pt_BR')]"`.
- Para valores canônicos do banco que aparecem em UI traduzida (ex.: Pendente/Em andamento/Concluído de `action_items`), usar helpers `i18n.<entity>_label/_options/_from_label`.

### Streamlit

- Botões em funções `render_*` distintas devem ter `key=` explícito quando o label pode colidir (Streamlit gera ID automático a partir de label + tipo + width — labels iguais entre duas abas explodem em runtime).
- `@st.cache_data(ttl=N)` para queries Supabase que se repetem; chave do cache deve incluir `user_id` ou parâmetro discriminante.
- Data editors devem usar `key` parametrizado quando a estrutura varia por seleção (ex.: `f"day_plan_editor_{selected_date.isoformat()}"`).
- Tema: preferência do usuário persiste em `user_metadata.preferred_language` via Supabase Auth.

### Timezone (dual-tz pós-M7)

- Internamente todo timestamp é armazenado em **UTC** (Postgres `timestamptz`).
- **Fuso primário fixo** = `America/New_York` (sessão CME). Usado em: `trade_day_et` derivado no `load_trades` do Dashboard, KPIs/calendário/coach do Dashboard, comparação de `daily_plans` no Risk Guard trigger.
- **Fuso secundário do usuário** = `user_metadata.preferred_tz`, auto-detectado via JS no primeiro login (`Intl.DateTimeFormat().resolvedOptions().timeZone`); sobrescrevível na aba Configurações; fallback `America/Sao_Paulo`. Usado em: `entry_hour` para análise horária, segunda metade do `fmt_dual()` nos cards/tabela de trades/alertas.
- **`trade_day` (coluna do banco)** continua em CT — vem do CSV TopStepX. **NÃO usar** em métricas novas; usar `trade_day_et` derivado em memória.
- **Helpers:** `src/timezones.py` — `PRIMARY_TZ`, `user_tz()`, `to_primary(ts)`, `to_user(ts)`, `fmt_dual(ts) -> "HH:MM ET / HH:MM BRT"`.
- Em `metrics.py`, funções que precisam agregar por dia usam `_day_col(df)` que prefere `trade_day_et` quando presente, mantendo back-compat.

### Git

- **NUNCA** commitar `Env/`, `.streamlit/secrets.toml`, `CSV input/*.csv`, `__pycache__/`, `.venv/`, ou qualquer pasta com nome `Env` em qualquer caixa (regra global).
- Commits em pt-BR seguindo Conventional Commits: `feat(escopo):`, `fix(escopo):`, `refactor:`, `chore:`, `docs:`. Co-author Claude no rodapé.
- **Trunk-based: commitar direto em `main`** (decisão 2026-05-30, dev único). Não criar feature branches (`saas/m<N>`, `fusao/m<N>`, `feat/`) nem merge `--no-ff`. Cada mudança é 1+ commit atômico direto em `main`, push em `origin/main`. O pre-push hook (`git config core.hooksPath .githooks`, 1x por worktree) roda `pytest tests/` como gate.
- Skipping hooks (`--no-verify`) ou forçar push em `main` é proibido sem instrução explícita do usuário.

---

## Configuração Supabase (uma vez, no painel)

1. Authentication → Providers → habilitar **Email**.
2. Authentication → Providers → habilitar **Google**: criar OAuth client no Google Cloud Console (Web Application), redirect URI = `https://<projeto>.supabase.co/auth/v1/callback`, colar `client_id`/`client_secret`.
3. Authentication → URL Configuration → adicionar em Redirect URLs: `http://localhost:8501` e a URL final do Streamlit Cloud.
4. SQL Editor → rodar em ordem: `schema.sql` → `saas_schema.sql` → cada `m<N>_<feature>.sql`.
5. Edge Functions: `supabase secrets set ...` para cada secret listado nos `.env.example` das funções.

---

## Notas operacionais

- A coluna `Id` do CSV pode chegar como float se o CSV tiver linha vazia; `astype("int64")` falha antes do upsert — comportamento desejado.
- `service_role` bypassa RLS — só use no `ingest.py` local e em Edge Functions; **nunca** em `secrets.toml`/Streamlit Cloud.
- **SQL Editor do Supabase roda como `postgres` (service role), não como o usuário autenticado** — portanto `auth.uid()` retorna `NULL` ali e qualquer `where user_id = auth.uid()` devolve zero linhas mesmo com dados presentes. Para inspecionar dados de um user específico no Editor, filtre pelo UUID literal (`where user_id = 'c4765210-...'`). Esse é o caminho legítimo para debug — não tente "logar" no Editor.
- `load_trades(user_id)` em `app.py` usa `user_id` como chave de cache: previne vazamento entre usuários no mesmo processo Streamlit.
- Não rode `ingest.py` em paralelo: `next_output_name()` não tem lock.
- LSP do VS Code pode reportar "Cannot find module streamlit/plotly" — é falso positivo (LSP olhando Python 3.14 global em vez do `.venv`). Ignorar.

---

## Verificações antes de declarar tarefa concluída

Checklist obrigatório (especialmente para features novas):

- [ ] Sintaxe Python OK: `ast.parse` nos arquivos tocados.
- [ ] JSON dos locales válidos nos 3 idiomas.
- [ ] Streamlit sobe sem erro (`streamlit run src/app.py` em background, ler stdout).
- [ ] Golden path manualmente testado no browser (criar / editar / deletar / recarregar).
- [ ] Pelo menos 1 caso de erro testado (entrada inválida, RLS bloqueando, etc.).
- [ ] Mudanças sensíveis (auth, billing, RLS, secrets) revisadas com `/security-review`.
- [ ] `MEMORIA.md` atualizado (estado + histórico).
- [ ] `DECISOES.md` atualizado se houve decisão arquitetural.
- [ ] Commit message clara, escopo único, co-author Claude.

Se algum item falha, **não declare concluído** — sinalize ao usuário e trate antes.
