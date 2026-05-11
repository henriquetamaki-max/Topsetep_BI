# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Pipeline

CSVs de trades exportados do TopStepX → ingestão Python → Supabase Postgres (tabela `public.trades`) → Metabase.

Tudo é orquestrado por um único script (`ingest.py`); não há servidor web, fila ou agendador. A "execução" é o operador rodar o script localmente quando há novos CSVs.

## Comandos

```bash
# Setup (1x, na raiz do projeto)
python -m venv .venv
.venv\Scripts\activate          # Windows (bash: source .venv/Scripts/activate)
pip install -r requirements.txt

# Rodar a ingestão (lê CSV input/, escreve em Supabase, move para CSV output/)
python ingest.py
```

Não há suite de testes, linter configurado ou pipeline de build. O schema do banco vive em `schema.sql` e é aplicado manualmente uma vez no Supabase SQL Editor.

## Arquitetura

### Fluxo de `ingest.py`

1. **`load_env()`** — lê `Env/Topstep_bi.env`. Exige `NEXT_PUBLIC_SUPABASE_URL` (ou `SUPABASE_URL`) e `SUPABASE_SERVICE_ROLE_KEY` (a chave anon **não** serve — RLS bloqueia inserts). Em caso de variável faltando o script aborta com mensagem específica.
2. **`normalize_df()`** — renomeia colunas PascalCase do CSV (`Id`, `ContractName`, `EnteredAt`, ...) para snake_case que casa com `schema.sql`. Datas vêm no formato `MM/DD/YYYY HH:MM:SS ±HH:MM` (formato dos EUA com timezone) e são convertidas para UTC.
3. **`_duration_to_pg_interval()`** — `TradeDuration` chega em formato .NET `HH:MM:SS.fffffff` (7 casas decimais). Postgres `interval` aceita no máximo microssegundos (6 casas), então a fração é truncada. Se algum CSV futuro vier sem esse formato, a função retorna a string original — isso vai falhar no upsert, o que é o comportamento desejado (não silenciar formato inesperado).
4. **`upsert_batches()`** — upsert em lotes de `BATCH_SIZE=1000` com `on_conflict="id"`. Reprocessar o mesmo CSV é seguro: duplicatas por `id` (PK) são ignoradas.
5. **`next_output_name()`** — gera nomes sequenciais `YYYYMMDD_N.csv` para a pasta `CSV output/`. Usa timezone `America/Sao_Paulo` para a data (não UTC), porque o nome reflete o dia da operação do trader, não o dia UTC.
6. CSVs com erro **permanecem em `CSV input/`** para retry; só os processados com sucesso (ou vazios) são movidos. Exit code 1 se houve qualquer erro.

### Schema (`schema.sql`)

- PK é `id` (bigint vindo do TopStepX) — toda a idempotência do pipeline depende disso.
- `pnl_net` é coluna **gerada** (`pnl - fees - commissions`) — não tente popular pelo Python.
- `type` tem CHECK constraint `in ('Long','Short')` — qualquer outro valor no CSV faz o upsert falhar.
- Índices em `trade_day` e `contract_name` (queries do Metabase tipicamente filtram por esses).

### Convenções de pastas

- `CSV input/` — inbox de CSVs a processar.
- `CSV output/` — arquivos já ingeridos, renomeados `YYYYMMDD_N.csv`.
- `Env/Topstep_bi.env` — segredos (ver regras globais sobre pastas `Env`); nunca commitar.
- `PRD/` — notas de produto/processo (texto, não código).
- `Templates/` — referência visual do dashboard alvo.

## Notas operacionais

- A coluna `Id` do CSV pode chegar como float se o CSV tiver linha vazia no meio; `astype("int64")` vai falhar antes do upsert — isso é o comportamento desejado.
- A `service_role` key bypassa RLS — não use ela em nenhum contexto que não seja este script local.
- Não rode `ingest.py` em paralelo: `next_output_name()` faz um scan-then-write sem lock e duas instâncias podem colidir no nome.
