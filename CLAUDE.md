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

Toda feature que toca schema + UI + métrica deve virar 3 commits:

- **PR-A — Schema**: migration SQL idempotente em `PRD/m<N>_<feature>.sql` + aplicar no Supabase + validar.
- **PR-B — UI**: módulo CRUD em `src/<feature>.py` + `render_<feature>()` em `app.py` + nova aba + i18n nos 3 locales + validação manual via Streamlit local.
- **PR-C — Métrica/integração**: função em `metrics.py` + integração no Dashboard ou outra aba + i18n + validação end-to-end.

Cada PR é vali­dável isolado. Use branch dedicada (`fusao/m<N>-<feature>`, `saas/m<N>-<feature>`, `feat/<area>`).

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
src/                       # código Python (10 módulos)
locales/{en,es,pt_BR}.json # i18n — 3 idiomas SEMPRE sincronizados
PRD/                       # schema SQL + PRDs + migrations + userscript
supabase/functions/        # Edge Functions Deno (Stripe webhook etc.)
Env/                       # segredos LOCAL (gitignored, nunca commitar)
.streamlit/                # secrets.toml para deploy (gitignored)
assets/                    # imagens estáticas (login screen etc.)
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
3. `PRD/m<N>_<feature>.sql` — migrations da fusão (M5+: `daily_plans` etc.).

**Regras absolutas para toda tabela nova:**

- Coluna `user_id uuid not null references auth.users(id) on delete cascade`.
- `enable row level security` + policy `<tabela>_owner_all` com `using (auth.uid() = user_id) with check (...)`.
- PK simples `bigserial` OU composta `(user_id, id)` — composta quando o `id` vem de fonte externa (ex.: TopStepX).
- UNIQUE constraint quando há semântica de chave natural (ex.: `daily_plans`: `unique (user_id, plan_date, contract_name, direction)`).
- Índice `(user_id, <campo de busca frequente>)`.
- Migrations idempotentes: `drop table if exists ... cascade;` no início se a tabela for nova sem dados em produção. Comente explicitamente o risco no cabeçalho.

### Edge Functions (`supabase/functions/`)

Deno + TypeScript, `verify_jwt=false` quando autenticação vem de assinatura externa (Stripe HMAC). Cliente Supabase usa `SERVICE_ROLE_KEY` (bypassa RLS — webhook é única escrita legítima em tabelas privilegiadas).

Cada função tem:

- `index.ts` — handler.
- `README.md` — eventos tratados, secrets, deploy, teste local com Stripe CLI / supabase functions serve.
- `.env.example` — todos os secrets esperados (gitignored o `.env` real).

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

### Timezone

- Fonte da verdade do CSV TopStepX está em **Chicago (CT)** — o `trade_day` é calculado nessa timezone no ingest.
- Internamente todo timestamp é armazenado em **UTC** (Postgres `timestamptz`).
- Para exibição de "dia da sessão", sempre converter para `America/Chicago` antes de extrair `.date()`.
- `entry_hour` para análise horária é convertido para `America/Sao_Paulo` (BRT — perspectiva do trader).

### Git

- **NUNCA** commitar `Env/`, `.streamlit/secrets.toml`, `CSV input/*.csv`, `__pycache__/`, `.venv/`, ou qualquer pasta com nome `Env` em qualquer caixa (regra global).
- Commits em pt-BR seguindo Conventional Commits: `feat(escopo):`, `fix(escopo):`, `refactor:`, `chore:`, `docs:`. Co-author Claude no rodapé.
- Branches: `saas/m<N>-<nome>` para milestones SaaS, `fusao/m<N>-<nome>` para fases da fusão com Trade_Agent, `feat/<area>` para features avulsas.
- Mergear via `--no-ff` para preservar ponto de integração (padrão herdado dos merges M1-M4).
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
