# BI TopStep

Pipeline: CSV de trades (TopStepX) → Supabase Postgres → Metabase.

## Setup (1x)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

No Supabase SQL Editor, rodar o conteúdo de `schema.sql`.

Em `Env/Topstep_bi.env`, garantir a linha:
```
SUPABASE_SERVICE_ROLE_KEY=...   # Settings → API → service_role secret
```

## Rodar a ingestão

Coloque CSVs em `CSV input/` e:

```bash
python ingest.py
```

Cada CSV processado é movido para `CSV output/` renomeado como `YYYYMMDD_N.csv`.
Duplicatas (por `id`) são ignoradas via `UPSERT ON CONFLICT`.

## Dashboard

Metabase → Add Database → PostgreSQL → connection string do `Env/Topstep_bi.env`.
