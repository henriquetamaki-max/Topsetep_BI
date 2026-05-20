# BI TopStep

Dashboard multi-tenant para análise de trades exportados do TopStepX.

Pipeline: CSV → upload pela UI (Streamlit + Supabase Auth) → Postgres com RLS por `user_id` → dashboard.

## Como rodar

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

Ou duplo-clique em `BI_TopStep.bat` (sobe Streamlit + abre o navegador).

Ingestão local via CLI legado (opcional):

```bash
python src/ingest.py
```

Ou duplo-clique em `import_csv.bat`.

## Estrutura

- `src/` — código Python da aplicação (entrypoint: `app.py`; CLI: `ingest.py`).
- `assets/` — imagens estáticas (login screen, etc.).
- `locales/` — traduções i18n (`pt_BR`, `en`, `es`).
- `Env/` — segredos locais (não vai pro git). Ver `Env/Topstep_bi.env.example` se houver.
- `.streamlit/secrets.toml` — segredos do deploy Streamlit Cloud (não vai pro git).
- `CSV input/` / `CSV output/` — diretórios do CLI legado.
- `PRD/` — schema SQL, documentos de produto e indicador userscript.
- `Templates/`, `scripts/`, `supabase/` — utilitários e migrations.

## Documentos

- `CLAUDE.md` — instruções de arquitetura para o agente Claude Code.
- `MEMORIA.md` — diário operacional do projeto (estado atual, gotchas, histórico).
- `DECISOES.md` — decisões técnicas relevantes (ADR-lite).
