# BI TopStep

Dashboard multi-tenant para análise de trades exportados do TopStepX + Live Monitor real-time com Risk Guard para traders Pro.

**Pipeline batch:** CSV → upload pela UI (Streamlit + Supabase Auth) → Postgres com RLS por `user_id` → dashboard.

**Pipeline live (Pro):** extensão Chrome MV3 lê posição/PnL em `topstepx.com/trade` → Edge Function `live-ingest` → `live_snapshots` → trigger `risk_guard_eval` → `alerts` → Web Notifications no navegador do trader.

## Como rodar (app Streamlit)

Setup inicial (1x):

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Subir o app:

```bash
streamlit run src/app.py
```

Ou duplo-clique em `BI_TopStep.bat`.

Ingestão local via CLI legado (opcional, para o operador):

```bash
python src/ingest.py
```

## Schemas Supabase

Aplicar manualmente no SQL Editor na ordem (todos idempotentes):

1. `PRD/schema.sql`
2. `PRD/saas_schema.sql`
3. `PRD/m5_daily_plans.sql`
4. `PRD/m6_*.sql` — ver `PRD/m6_README.md` para ordem interna.
5. `PRD/m9_features.sql`
6. `PRD/m10_risk_guard_trigger.sql`
7. `PRD/m11_live_snapshots_unique.sql`

## Edge Functions

- `supabase/functions/stripe-webhook/` — webhook do Stripe (M4).
- `supabase/functions/live-ingest/` — ingestão de snapshots da extensão (M9).

Deploy: `supabase functions deploy <nome>`.

## Extensão Chrome (Live Monitor — plano Pro)

Pasta `extension/` contém um MV3 (`manifest.json`, `background.js`, `content.js`, `popup.*`, `_locales/`, `selectors.json`).

Empacotar para distribuição:

```bash
.venv/Scripts/python.exe scripts/package_extension.py
# Opcional: injetar config no zip
.venv/Scripts/python.exe scripts/package_extension.py \
    --supabase-url https://<proj>.supabase.co --anon-key <ANON>
```

Saída: `dist/extension/extension-latest.zip` + versionado.

Distribuição MVP: subir o `.zip` no bucket público `extension/` do Supabase Storage. Aba **Live** do app linka para esse arquivo.

## Tests

```bash
.venv/Scripts/python.exe -m unittest discover -s tests
```

Cobre: `metrics.compute_plan_adherence` (5 categorias), `daily_plan.compute_usd` (multi-contrato), `timezones.fmt_dual` (dual-tz NY/ET + user_tz) e consistência de chaves i18n entre `locales/` e `extension/_locales/` (chaves + placeholders + valores não vazios).

## Estrutura do repo

```
src/                    código Python (app, auth, billing, metrics, live, settings, etc.)
extension/              extensão Chrome MV3
supabase/functions/     Edge Functions Deno
PRD/                    schemas SQL + PRDs + ENCERRAMENTO Trade_Agent + guia
locales/                i18n do app (EN / PT-BR / ES)
tests/                  unit tests (unittest stdlib)
scripts/                ferramentas (package_extension.py, etc.)
assets/                 estáticos (login screen, vendor/)
dist/                   builds (.zip da extensão) — gitignored
Env/                    segredos locais — gitignored, NUNCA commitar
.streamlit/secrets.toml segredos do deploy — gitignored
CSV input/ output/      legados do CLI ingest.py — gitignored
```

## Documentos

- [`CLAUDE.md`](CLAUDE.md) — instruções arquiteturais para o agente Claude Code (contrato de cada sessão).
- [`MEMORIA.md`](MEMORIA.md) — diário operacional (estado atual, gotchas, histórico).
- [`DECISOES.md`](DECISOES.md) — decisões técnicas (ADR-lite).
- [`guia_execucao.md`](guia_execucao.md) — roteiro detalhado da fusão Trade_Agent → BI TopStep (30 tarefas, T0 a T4.4).
- [`PRD/ENCERRAMENTO_trade_agent.md`](PRD/ENCERRAMENTO_trade_agent.md) — post-mortem do projeto Trade_Agent absorvido.

## Estado atual (2026-05-20)

Fusão Trade_Agent → BI TopStep **toda codada nas 4 fases** (schemas + dual-timezone + adições refinadas + extensão + Edge Function + Risk Guard + Web Notifications). Falta apenas execução de passos manuais de deploy:

- ~~Aplicar migrations M11~~ (✅ aplicada 2026-05-20; M6/M9/M10/M11 todas vivas no banco).
- Criar bucket público `extension` no Supabase Storage + subir `dist/extension/extension-latest.zip`.
- `supabase login` + `supabase functions deploy live-ingest --no-verify-jwt`.
- Smoke test end-to-end seguindo `PRD/SMOKE_TEST_live.md` (4 checkpoints).
- Arquivar Trade_Agent (5 passos manuais em `PRD/ENCERRAMENTO_trade_agent.md` seção 12).
