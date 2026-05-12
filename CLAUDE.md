# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Pipeline

CSVs de trades exportados do TopStepX → upload pela UI do app (autenticado) → Supabase Postgres (tabela `public.trades`, isolada por `user_id` via RLS) → dashboard Streamlit.

App é multi-tenant: cada trader faz login (email/senha ou Google via Supabase Auth) e só enxerga os próprios dados. RLS no banco garante o isolamento, não a aplicação.

Existe também o CLI legado `ingest.py` para uso local pelo operador (bypass de RLS via service_role); novos usuários devem ingerir pela aba **Importar CSVs** do app.

## Comandos

```bash
# Setup (1x, na raiz do projeto)
python -m venv .venv
.venv\Scripts\activate          # Windows (bash: source .venv/Scripts/activate)
pip install -r requirements.txt

# Rodar o app (dashboard + login + upload)
streamlit run app.py

# (Opcional) Ingestão local via CLI legado — lê CSV input/, atribui ao
# INGEST_USER_ID configurado no Env, move para CSV output/.
python ingest.py
```

Schema do banco vive em `PRD/schema.sql` e é aplicado manualmente uma vez no Supabase SQL Editor.

## Arquitetura

### Autenticação (`auth.py`)

- Supabase Auth: email/senha + Google OAuth (PKCE). `login_screen()` bloqueia o app até logar.
- Sessão em `st.session_state["session"]` (dict). `get_client()` devolve um cliente Supabase com o JWT do usuário injetado — todas as queries respeitam RLS.
- Credenciais lidas de `st.secrets` (deploy Streamlit Cloud) ou `Env/Topstep_bi.env` (local). Anon key, **nunca** service_role.

### Ingestão

- `ingest_core.py` — funções puras: `detect_format`, `normalize_topstepx`, `normalize_dashboard`, `records_for_supabase(df, user_id)`, `upsert_batches`, `ingest_uploaded_csv(file, client, user_id)`.
- Upload pelo app: aba "Importar CSVs" chama `ingest_uploaded_csv` com o cliente autenticado. `user_id` vem da sessão.
- `ingest.py` CLI: wrapper fino que lê `CSV input/`, usa service_role + `INGEST_USER_ID` do env, e move arquivos para `CSV output/` como `YYYYMMDD_N.csv`.
- `_duration_to_pg_interval()` trunca a fração `.fffffff` (.NET, 7 casas) para microssegundos (Postgres `interval` aceita no máximo 6).

### Schema (`PRD/schema.sql`)

- PK de `trades` é **composta** `(user_id, id)` — TopStepX gera `id` sem conhecer multi-tenancy, então a PK composta evita colisão entre usuários distintos.
- `coach_analyses` e `action_items` também têm `user_id`. As três tabelas têm RLS habilitada com policy `auth.uid() = user_id` (SELECT/INSERT/UPDATE/DELETE).
- `pnl_net` é coluna **gerada** (`pnl - fees - commissions`) — não tente popular pelo Python.
- `type` tem CHECK constraint `in ('Long','Short')`.
- Índices: `(user_id, trade_day)`, `(user_id, contract_name)`.

### Convenções de pastas

- `CSV input/` / `CSV output/` — usados pelo CLI legado. No app hosted, irrelevantes.
- `Env/Topstep_bi.env` — segredos para uso local (anon key + service_role + INGEST_USER_ID). Nunca commitar.
- `.streamlit/secrets.toml` — segredos para deploy Streamlit Cloud (gitignored). Exemplo em `secrets.toml.example`.
- `PRD/` — schema SQL + notas de produto.

## Configuração Supabase (uma vez, no painel)

1. Authentication → Providers → habilitar **Email**.
2. Authentication → Providers → habilitar **Google**: criar OAuth client no Google Cloud Console (Web Application), redirect URI = `https://<projeto>.supabase.co/auth/v1/callback`, colar `client_id`/`client_secret`.
3. Authentication → URL Configuration → adicionar em Redirect URLs: `http://localhost:8501` e a URL final do Streamlit Cloud.

## Notas operacionais

- A coluna `Id` do CSV pode chegar como float se o CSV tiver linha vazia; `astype("int64")` falha antes do upsert — comportamento desejado.
- `service_role` bypassa RLS — só use no `ingest.py` local; **nunca** em `secrets.toml`/Streamlit Cloud.
- `load_trades(user_id)` em `app.py` usa `user_id` como chave de cache: previne vazamento entre usuários no mesmo processo Streamlit.
- Não rode `ingest.py` em paralelo: `next_output_name()` não tem lock.
