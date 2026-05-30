# Definition of Done — BI TopStep

Última atualização: 2026-05-30. Atualizado por: @henrique.

Critérios de aceite **por categoria de task**. Antes de marcar uma task como
concluída, confirmar que TODOS os critérios da categoria aplicável estão verdes.
Categorias derivadas da arquitetura real do projeto (ver `CLAUDE.md`).

Gate rápido antes de qualquer commit/push (`<2min`):

```
.venv/Scripts/python.exe -m pytest tests/ -q
```

---

## Categoria: Lib pura (`metrics.py`, `ingest_core.py`, `timezones.py`, `app_helpers.py`)

- [ ] Função pública com `compute_<area>` / `list_<entity>` / nome convencional.
- [ ] Testes unitários cobrindo caminho feliz + ≥1 adversário (df vazio, NaN, Int64 `<NA>`, all-NaN, divisão por zero, unicode).
- [ ] Docstring nas funções públicas.
- [ ] Sem dependência de Streamlit (importável standalone — `python -c "import <mod>"`).
- [ ] Timezone: agregação por dia usa `trade_day_et`/`_day_col`, nunca `trade_day` (CT) cru; exibição de hora rotula o fuso via `tz_label`/`fmt_dual`, nunca string fixa.

## Categoria: Migration / Schema (`PRD/*.sql`)

- [ ] Tabela por-usuário tem `user_id uuid not null references auth.users(id) on delete cascade`.
- [ ] `enable row level security` + policy `<tabela>_owner_all` com `using (auth.uid() = user_id) with check (...)`.
- [ ] Índice `(user_id, <campo de busca frequente>)`.
- [ ] UNIQUE para chave natural quando aplicável.
- [ ] Migration idempotente (rerun seguro: `drop ... if exists` comentado ou `on conflict do nothing/update`).
- [ ] Catálogo público (sem user_id) com leitura `using(true)` e **escrita restrita a admin** explícita.
- [ ] Aplicada no Supabase SQL Editor e registrada na ordem em `CLAUDE.md`.

## Categoria: CRUD multi-tenant (`src/<feature>.py` + `render_<feature>()`)

- [ ] `list_<entity>` confia no RLS (não duplica filtro divergente).
- [ ] `upsert_<entity>` injeta `user_id` da sessão (`auth.current_user_id()`) em inserts.
- [ ] Função cacheada (`@st.cache_data`/`cache_resource`) que toca dado por-usuário tem `user_id` (SEM prefixo `_`) na chave. **Bug-class:** `_`-prefix desabilita o hash → vazamento cross-tenant (gotchas billing/live/app em MEMORIA).
- [ ] Testes mockando `auth.get_client()` (caminho feliz + RLS bloqueando + linha inválida).

## Categoria: Componente UI (`render_*` em `app.py` / `src/*.py`)

- [ ] Toda string visível passa por `t("chave")`.
- [ ] Chave existe nos 3 locales (`en`/`es`/`pt_BR`) — validar com o smoke de paridade i18n.
- [ ] `key=` explícito em widgets cujo label pode colidir entre abas.
- [ ] Eixo X temporal converte via `timezones.to_primary(...).dt.tz_localize(None)` (nunca `entered_at` UTC cru).
- [ ] Caminho golden testado no browser (criar/editar/deletar/recarregar) + 1 caso de erro.

## Categoria: Edge Function (`supabase/functions/*`)

- [ ] `user_id` derivado de `getUser(jwt)` validado server-side — **nunca** do payload do cliente.
- [ ] `SERVICE_ROLE_KEY` usada só onde RLS precisa bypass (INSERT), nunca exposta/logada.
- [ ] Stripe: assinatura HMAC verificada (`constructEventAsync` sobre raw body) ANTES de processar.
- [ ] Secrets em `.env.example`; `.env` real gitignored.
- [ ] Sem teste unitário (Deno/TS, projeto só tem pytest) → smoke E2E pós-deploy documentado.

## Categoria: Extensão Chrome (`extension/*`)

- [ ] Arquivos referenciados no `manifest.json` existem (smoke `test_smoke_config_paths`).
- [ ] `config.js` só com anon key (pública); service_role **ausente**.
- [ ] `onMessage` valida `sender.id === chrome.runtime.id`.
- [ ] Token/JWT nunca logado cru em console (logar `status`, não a exceção/resposta).
- [ ] Permissões/host_permissions mínimas (sem `<all_urls>`).

## Categoria: Decisão técnica/produto

- [ ] Entry em `DECISOES.md` (contexto, decisão, ≥2 alternativas, consequências).
- [ ] Gotcha operacional não-óbvio → `MEMORIA.md`.

## Categoria: E2E

- [ ] ≥1 smoke exercita o caminho golden do produto (ingest: CSV → normalize → records → upsert) — `test_e2e_golden_path.py`.
- [ ] Roda dentro de `pytest tests/` (não isolado).

---

## Atualizações desta DoD

- 2026-05-30 — criação no bootstrap de quality gates (origem: audit #4 / `projeto-qualidade`). Adicionado item de cache-isolation na categoria CRUD (origem: gotcha billing/app 2026-05-30) e regra de eixo-X em ET na categoria UI (origem: bug equity curve 2026-05-30).
