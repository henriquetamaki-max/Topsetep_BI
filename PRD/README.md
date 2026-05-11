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

Streamlit local, conectado ao Supabase via `service_role` key (lendo o mesmo `Env/Topstep_bi.env`).

```bash
streamlit run app.py
```

Ou duplo-clique em `BI_TopStep.bat`, que cuida de subir o servidor (porta 8501) e abrir o navegador. Decisão de usar Streamlit em vez de Metabase: o app já tem lógica em Python (overlap grouping, KPIs em pontos, coach behavioral) que seria difícil de portar para a camada de SQL do Metabase.

## Agendamento (opcional)

Para rodar a ingestão automaticamente, use o Task Scheduler do Windows apontando para `import_csv_silent.bat` (não tem `pause`, então roda sem janela esperando input). Sugestão: gatilho horário em horário comercial, ou "ao logon" + "repetir a cada 30 min". O script não falha se `CSV input/` estiver vazia — apenas registra "Nada a fazer" e sai com código 0.
