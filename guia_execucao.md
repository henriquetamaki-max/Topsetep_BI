# Guia de Execução — Fusão Trade_Agent → BI TopStep

> Documento operacional. Cada tarefa é auto-contida: define objetivo, pré-requisitos, arquivos tocados, passo a passo, critério de pronto e branch sugerida. Execute em ordem (ou em paralelo quando indicado).

## 0. Contexto

A fusão foi decidida em 2026-05-20 ([DECISOES.md](DECISOES.md)): o repo BI TopStep absorve o Trade_Agent (`E:\BD\Trade_Agent`). Real-time entra como MVP via Supabase Realtime + Web Notifications API. Trade_Agent será arquivado após migração. Coach IA segue copy-paste.

Já entregue antes deste guia:
- M4 Stripe webhook mergeado em `saas-main` (merge `6df8737`).
- Refatoração de layout (módulos em `src/`).
- M5 parcial: tabela `daily_plans` + UI CRUD + `compute_plan_adherence`.

Este guia cobre o restante: **schemas novos** → **dual-timezone** → **adições não-planejadas refinadas** → **extensão Chrome multi-tenant + Edge Function live-ingest** → **realtime + Risk Guard + Web Notifications** → **cleanup**.

---

## 1. Decisões consolidadas (referência rápida)

| Tema | Decisão |
|---|---|
| Ordem das fases | 1.3 → 1.2 → 1.1 → 2 → 3 → 4 (schemas primeiro destravam tudo) |
| Auth extensão Chrome | JWT colado manualmente (botão na aba Account); OAuth in-extension é backlog |
| Pricing live | Apenas plano **Pro (US$ 49)**; trial inclui |
| Risk Guard MVP | 4 regras — Daily Loss Limit, Trailing Drawdown, Max Position Size, Adição não-planejada |
| `live_snapshots` | Híbrido colunar + jsonb; retenção 7 dias (job de purge) |
| `alerts` | Enum + severity + ciclo de vida (read_at, dismissed_at, source) |
| `tilt_patterns` | Histórico de detecções (1 linha = 1 detecção em 1 momento) |
| `payouts` | CRUD manual pelo trader |
| POINT_VALUE_USD | Tabela `contracts` no banco, seeded com 12 contratos |
| Adições não-planejadas | Refinar `compute_plan_adherence` atual + portar conceitos do Legacy |
| Fuso primário | NY/ET fixo (`America/New_York`) + fuso do usuário auto-detectado |
| Onde aplicar dual-tz | `trade_day`, cards/tabelas, alertas e timestamps de live_snapshots |
| Tela de configs | Nova aba "Configurações" (idioma, fuso, Risk Guard) |
| Tabela `risk_settings` | user_id PK + account_type + DLL + trailing DD + warning% |
| Snapshot Trade_Agent | Tag `v-final-pre-merge` é T0 do guia |
| Frequência snapshot extensão | 30s heartbeat + push imediato por evento |
| Distribuição extensão | Load unpacked dev only no MVP; CWS backlog |
| Realtime no Streamlit | Polling `st_autorefresh` (3s) + componente JS leve para Web Notifications |
| Dados Trade_Agent local | Descartar (sem script de import) |
| Conta TopStep por trader | 1 ativa no MVP (tabela `accounts`); múltiplas é backlog |
| Aderência ao plano | Permanece no Dashboard (batch); Live tem widgets próprios |
| Risk Guard runtime | Trigger Postgres `AFTER INSERT` em `live_snapshots` |
| i18n da extensão | Só EN no MVP |

---

## 2. Convenções deste guia

- **Códigos de tarefa**: `T<fase>.<subfase>.<n>` (ex.: `T1.3.2` = Fase 1.3, tarefa 2).
- **Branches**: `fusao/m<N>-<feature>`, mergear via `--no-ff` em `main` após validação.
- **Migrations SQL**: arquivo único por tarefa em `PRD/m<N>_<feature>.sql`, idempotente, aplicado manualmente no Supabase SQL Editor.
- **i18n**: toda string visível usa `t("chave")`; chaves novas vão nos 3 locales (`en.json`, `pt_BR.json`, `es.json`) antes de commitar.
- **Validação obrigatória** antes de commit:
  - `.venv/Scripts/python.exe -c "import ast; ast.parse(open('src/<arq>.py').read())"`
  - `.venv/Scripts/python.exe -c "import json; [json.load(open(f'locales/{l}.json',encoding='utf-8')) for l in ('en','es','pt_BR')]"`
  - `streamlit run src/app.py` em background, golden path no browser, 1 caso de erro.
- **Subagentes**: para qualquer task que toque >1 área, disparar 2-3 `Explore` em paralelo antes de implementar (ver [CLAUDE.md](CLAUDE.md)).

---

## 3. Pré-requisito — T0

### T0.1 — Snapshot final do Trade_Agent
**Objetivo:** congelar `E:\BD\Trade_Agent` antes de migrar componentes.
**Pré-requisitos:** nenhum.
**Arquivos tocados:** repo `E:\BD\Trade_Agent`.
**Passo a passo:**
1. `cd E:\BD\Trade_Agent && git status` — verificar pendências.
2. Se houver mudanças, commitar: `git add -A && git commit -m "chore: snapshot final pre-merge para BI TopStep"`.
3. Tag: `git tag -a v-final-pre-merge -m "Snapshot final antes da fusão com BI TopStep"`.
4. Não fazer `git push` da tag (repo local; subir só quando arquivar na Fase 4).
**Critério de pronto:** `git tag` lista `v-final-pre-merge` no repo Trade_Agent; `git status` limpo.
**Observação:** o repo do Trade_Agent só vira read-only/arquivado na Fase 4. Durante o guia, pode-se ler arquivos dele para referência (ex.: `processor.py`, `chrome_extension/`).

---

## 4. Fase 1.3 — Schemas Postgres novos

**Branch:** `fusao/m6-schemas-base`
**Objetivo:** criar todas as tabelas que sustentam as fases seguintes.
**Estratégia:** uma migration por tabela em `PRD/m6_<tabela>.sql`, aplicada na ordem T1.3.1 → T1.3.7.

### T1.3.1 — Tabela `contracts` (catálogo multi-contrato)
**Pré-requisitos:** T0.1.
**Arquivos tocados:** `PRD/m6_contracts.sql` (novo).
**SQL:**
```sql
-- PRD/m6_contracts.sql
create table if not exists public.contracts (
    symbol           text primary key,
    description      text not null,
    point_value_usd  numeric(12,4) not null,
    tick_size        numeric(12,6) not null,
    currency         text not null default 'USD',
    is_micro         boolean not null default false,
    created_at       timestamptz not null default now()
);

insert into public.contracts (symbol, description, point_value_usd, tick_size, is_micro) values
    ('MNQ', 'Micro E-mini Nasdaq-100',     2.00, 0.25, true),
    ('NQ',  'E-mini Nasdaq-100',          20.00, 0.25, false),
    ('MES', 'Micro E-mini S&P 500',        5.00, 0.25, true),
    ('ES',  'E-mini S&P 500',             50.00, 0.25, false),
    ('M2K', 'Micro E-mini Russell 2000',   5.00, 0.10, true),
    ('RTY', 'E-mini Russell 2000',        50.00, 0.10, false),
    ('MYM', 'Micro E-mini Dow',            0.50, 1.00, true),
    ('YM',  'E-mini Dow',                  5.00, 1.00, false),
    ('MCL', 'Micro WTI Crude Oil',         1.00, 0.01, true),
    ('CL',  'WTI Crude Oil',              10.00, 0.01, false),
    ('MGC', 'Micro Gold',                  1.00, 0.10, true),
    ('GC',  'Gold',                       10.00, 0.10, false)
on conflict (symbol) do nothing;

alter table public.contracts enable row level security;
drop policy if exists contracts_read_all on public.contracts;
create policy contracts_read_all on public.contracts
    for select using (true);
```
**Aplicar:** rodar no Supabase SQL Editor.
**Pós-implementação:**
- Em [src/daily_plan.py](src/daily_plan.py), substituir o dict `POINT_VALUE_USD` por uma função `_load_contracts()` que faz `client.table("contracts").select("symbol, point_value_usd").execute()` com cache `@st.cache_data(ttl=3600)`.
- `point_value_usd(symbol)` lê do cache.
**Critério de pronto:**
- Aba Day Plan: criar plano para "ES" com max_size=1, stop_points=4. Stop USD deve mostrar `$200.00` (4 × 50 × 1).
- Plano com contrato fora do catálogo (ex.: "FOO") mostra `—` no Stop USD sem quebrar.
**Commit:** `feat(m6): tabela contracts + seed multi-contrato (12 simbolos)`

### T1.3.2 — Tabela `live_snapshots` + retenção 7 dias
**Pré-requisitos:** T1.3.1.
**Arquivos tocados:** `PRD/m6_live_snapshots.sql` (novo).
**SQL:**
```sql
-- PRD/m6_live_snapshots.sql
create table if not exists public.live_snapshots (
    id                 bigserial primary key,
    user_id            uuid not null references auth.users(id) on delete cascade,
    account_id         text not null,
    snapshot_at        timestamptz not null,
    position_size      integer not null default 0,
    position_avg_price numeric(18,6),
    position_contract  text,
    position_side      text check (position_side in ('Long','Short') or position_side is null),
    unrealized_pnl     numeric(18,4) not null default 0,
    realized_pnl       numeric(18,4) not null default 0,
    day_pnl            numeric(18,4) not null default 0,
    drawdown           numeric(18,4) not null default 0,
    raw                jsonb,
    created_at         timestamptz not null default now()
);

create index if not exists live_snapshots_user_at_idx
    on public.live_snapshots (user_id, snapshot_at desc);

alter table public.live_snapshots enable row level security;
drop policy if exists live_snapshots_owner_all on public.live_snapshots;
create policy live_snapshots_owner_all on public.live_snapshots
    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- Job de purge: remover snapshots > 7 dias. Agendar via pg_cron (extensão
-- já habilitada no Supabase). Roda 1x por dia às 03:00 UTC.
create or replace function public.purge_old_live_snapshots()
returns void language sql as $$
    delete from public.live_snapshots where snapshot_at < now() - interval '7 days';
$$;

-- IMPORTANTE: rodar em SQL Editor 1x para criar o schedule (pg_cron):
--   select cron.schedule('purge_live_snapshots', '0 3 * * *',
--       $$select public.purge_old_live_snapshots()$$);
```
**Aplicar:** rodar a migration. Depois rodar o `cron.schedule` manualmente uma vez.
**Critério de pronto:** `select * from cron.job where jobname='purge_live_snapshots';` retorna 1 linha.
**Commit:** `feat(m6): live_snapshots + RLS + purge job (7d)`

### T1.3.3 — Tabela `alerts` + enum + ciclo de vida
**Pré-requisitos:** T1.3.2.
**Arquivos tocados:** `PRD/m6_alerts.sql` (novo).
**SQL:**
```sql
-- PRD/m6_alerts.sql
do $$ begin
    if not exists (select 1 from pg_type where typname='alert_type') then
        create type public.alert_type as enum (
            'daily_loss_limit',
            'trailing_drawdown',
            'max_position_size',
            'unplanned_addition',
            'plan_unplanned_contract',
            'manual'
        );
    end if;
    if not exists (select 1 from pg_type where typname='alert_severity') then
        create type public.alert_severity as enum ('info','warn','critical');
    end if;
    if not exists (select 1 from pg_type where typname='alert_source') then
        create type public.alert_source as enum ('risk_guard','metric','manual');
    end if;
end $$;

create table if not exists public.alerts (
    id            bigserial primary key,
    user_id       uuid not null references auth.users(id) on delete cascade,
    created_at    timestamptz not null default now(),
    alert_type    public.alert_type not null,
    severity      public.alert_severity not null default 'warn',
    source        public.alert_source not null default 'risk_guard',
    title         text not null,
    body          text,
    payload       jsonb,
    read_at       timestamptz,
    dismissed_at  timestamptz
);

create index if not exists alerts_user_created_idx
    on public.alerts (user_id, created_at desc);
create index if not exists alerts_unread_idx
    on public.alerts (user_id) where read_at is null;

alter table public.alerts enable row level security;
drop policy if exists alerts_owner_all on public.alerts;
create policy alerts_owner_all on public.alerts
    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
```
**Aplicar:** rodar no SQL Editor.
**Critério de pronto:** `insert into public.alerts (user_id, alert_type, title) values (auth.uid(), 'manual', 'teste');` cria a linha. RLS bloqueia leitura por outro usuário.
**Commit:** `feat(m6): alerts + enums + RLS + ciclo de vida`

### T1.3.4 — Tabela `tilt_patterns`
**Pré-requisitos:** T1.3.3.
**Arquivos tocados:** `PRD/m6_tilt_patterns.sql` (novo).
**SQL:**
```sql
-- PRD/m6_tilt_patterns.sql
do $$ begin
    if not exists (select 1 from pg_type where typname='tilt_pattern_type') then
        create type public.tilt_pattern_type as enum (
            'revenge',
            'overtrading',
            'cut_winners_hold_losers',
            'losing_streak',
            'size_creep',
            'plan_deviation'
        );
    end if;
end $$;

create table if not exists public.tilt_patterns (
    id            bigserial primary key,
    user_id       uuid not null references auth.users(id) on delete cascade,
    detected_at   timestamptz not null default now(),
    pattern_type  public.tilt_pattern_type not null,
    severity      public.alert_severity not null default 'warn',
    context       jsonb,
    related_trade_ids bigint[],
    related_group_id  bigint
);

create index if not exists tilt_patterns_user_detected_idx
    on public.tilt_patterns (user_id, detected_at desc);

alter table public.tilt_patterns enable row level security;
drop policy if exists tilt_patterns_owner_all on public.tilt_patterns;
create policy tilt_patterns_owner_all on public.tilt_patterns
    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
```
**Critério de pronto:** insert manual funciona; RLS bloqueia leitura cross-user.
**Commit:** `feat(m6): tilt_patterns + enum + RLS`

### T1.3.5 — Tabela `payouts`
**Pré-requisitos:** T1.3.4.
**Arquivos tocados:** `PRD/m6_payouts.sql` (novo).
**SQL:**
```sql
-- PRD/m6_payouts.sql
create table if not exists public.payouts (
    id            bigserial primary key,
    user_id       uuid not null references auth.users(id) on delete cascade,
    account_id    text,
    requested_at  date not null,
    paid_at       date,
    amount_usd    numeric(12,2) not null check (amount_usd >= 0),
    status        text not null default 'pending'
                  check (status in ('pending','approved','paid','denied')),
    notes         text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index if not exists payouts_user_requested_idx
    on public.payouts (user_id, requested_at desc);

alter table public.payouts enable row level security;
drop policy if exists payouts_owner_all on public.payouts;
create policy payouts_owner_all on public.payouts
    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop trigger if exists payouts_touch_updated_at on public.payouts;
create trigger payouts_touch_updated_at
    before update on public.payouts
    for each row execute function public.touch_updated_at();
```
**Commit:** `feat(m6): payouts + RLS + trigger updated_at`

### T1.3.6 — Tabela `risk_settings`
**Pré-requisitos:** T1.3.5.
**Arquivos tocados:** `PRD/m6_risk_settings.sql` (novo).
**SQL:**
```sql
-- PRD/m6_risk_settings.sql
create table if not exists public.risk_settings (
    user_id                uuid primary key references auth.users(id) on delete cascade,
    account_type           text,
    daily_loss_limit_usd   numeric(12,2),
    trailing_drawdown_usd  numeric(12,2),
    max_position_size      integer,
    warning_threshold_pct  numeric(5,2) not null default 80.0,
    updated_at             timestamptz not null default now()
);

alter table public.risk_settings enable row level security;
drop policy if exists risk_settings_owner_all on public.risk_settings;
create policy risk_settings_owner_all on public.risk_settings
    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop trigger if exists risk_settings_touch_updated_at on public.risk_settings;
create trigger risk_settings_touch_updated_at
    before update on public.risk_settings
    for each row execute function public.touch_updated_at();
```
**Commit:** `feat(m6): risk_settings + RLS`

### T1.3.7 — Tabela `accounts` (mapeamento account_id TopStep → user_id)
**Pré-requisitos:** T1.3.6.
**Arquivos tocados:** `PRD/m6_accounts.sql` (novo).
**SQL:**
```sql
-- PRD/m6_accounts.sql
create table if not exists public.accounts (
    id                 bigserial primary key,
    user_id            uuid not null references auth.users(id) on delete cascade,
    account_id         text not null,
    label              text,
    topstep_plan_size  text,
    active             boolean not null default true,
    created_at         timestamptz not null default now(),
    unique (user_id, account_id)
);

create index if not exists accounts_user_active_idx
    on public.accounts (user_id, active);

alter table public.accounts enable row level security;
drop policy if exists accounts_owner_all on public.accounts;
create policy accounts_owner_all on public.accounts
    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
```
**Commit:** `feat(m6): accounts + RLS (1 conta TopStep ativa por trader no MVP)`

### T1.3.8 — Wrap-up Fase 1.3
**Objetivo:** validar tudo em conjunto.
**Passo a passo:**
1. Rodar todas as migrations T1.3.1 a T1.3.7 em ambiente staging.
2. Conferir: `select tablename from pg_tables where schemaname='public' order by tablename;` deve listar: `accounts, action_items, admin_users, alerts, coach_analyses, contracts, daily_plans, feature_flags, live_snapshots, payouts, plans, risk_settings, subscriptions, tilt_patterns, trades`.
3. Atualizar `PRD/CLAUDE.md` (se existir) ou `CLAUDE.md` da raiz: listar as 7 tabelas novas com 1 linha cada.
4. Atualizar `MEMORIA.md` com entrada datada da conclusão.
**Commit:** `docs(m6): registra Fase 1.3 (schemas base) no MEMORIA`

---

## 5. Fase 1.2 — Dual-timezone + Aba Configurações

**Branch:** `fusao/m7-dual-timezone-configs`
**Objetivo:** introduzir fuso primário fixo NY/ET + fuso do usuário auto-detectado, com tela de configurações para persistir preferências.

### T1.2.1 — Aba "Configurações" (esqueleto)
**Pré-requisitos:** Fase 1.3 completa.
**Arquivos tocados:** `src/settings.py` (novo), `src/app.py`, `locales/{en,es,pt_BR}.json`.
**Passo a passo:**
1. Criar `src/settings.py` com `render_settings_tab(user, plan)`. Estrutura interna em 4 seções (renderizadas como expanders ou containers):
   - **Idioma** — reusa `i18n.language_selector()` que já existe.
   - **Fuso horário** — campo read-only "Fuso primário: New York (ET)" + select "Fuso secundário" (default = auto-detectado, opções = lista IANA curta + "outro").
   - **Risk Guard** — só visível se `billing.has_feature(plan, 'live_monitor')`. CRUD da `risk_settings` (formulário, não data_editor).
   - **Conta TopStep ativa** — CRUD de `accounts` (1 ativa, listadas inativas separadamente). Tabela editável com checkbox `active`.
2. Em `src/app.py`, adicionar a aba na lista de tabs (penúltima, antes de Account):
   ```python
   tab_dash, tab_coach, tab_dayplan, tab_plan, tab_import, tab_settings, tab_account = st.tabs([...])
   with tab_settings:
       settings.render_settings_tab(_user, _plan)
   ```
3. Adicionar chaves i18n: `tab.settings`, `settings.title`, `settings.caption`, `settings.section.language`, `settings.section.timezone`, `settings.section.risk_guard`, `settings.section.accounts`, etc.
**Critério de pronto:** aba "Configurações" aparece no menu de tabs; abrir mostra as 4 seções sem erro.
**Commit:** `feat(m7): aba Configurações (esqueleto + 4 secoes)`

### T1.2.2 — Utilitário `src/timezones.py`
**Pré-requisitos:** T1.2.1.
**Arquivos tocados:** `src/timezones.py` (novo), `src/i18n.py` (para integrar com `current_lang()`).
**Conteúdo do módulo:**
```python
"""Utilitário de fuso horário — NY/ET fixo como primário + fuso do usuário.

Primário: America/New_York (sessão CME). Secundário: do usuário, lido de
user_metadata.preferred_tz ou auto-detectado via JS no primeiro load.
"""
from __future__ import annotations
import pandas as pd
import streamlit as st

PRIMARY_TZ = "America/New_York"
FALLBACK_TZ = "America/Sao_Paulo"

def user_tz() -> str:
    """Fuso secundário do usuário. Lê de session_state (setado por
    settings.py após auto-detect no front)."""
    return st.session_state.get("user_tz") or FALLBACK_TZ

def to_primary(ts: pd.Timestamp | pd.Series) -> pd.Timestamp | pd.Series:
    """Converte timestamp (assumindo UTC se naive) para fuso primário."""
    if isinstance(ts, pd.Series):
        if ts.dt.tz is None:
            ts = ts.dt.tz_localize("UTC")
        return ts.dt.tz_convert(PRIMARY_TZ)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(PRIMARY_TZ)

def to_user(ts: pd.Timestamp | pd.Series) -> pd.Timestamp | pd.Series:
    """Converte timestamp para fuso do usuário."""
    tz = user_tz()
    if isinstance(ts, pd.Series):
        if ts.dt.tz is None:
            ts = ts.dt.tz_localize("UTC")
        return ts.dt.tz_convert(tz)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(tz)

def fmt_dual(ts: pd.Timestamp, fmt: str = "%d/%m %H:%M") -> str:
    """Formata '14:32 ET / 11:32 BRT'."""
    p = to_primary(ts).strftime(fmt)
    u = to_user(ts).strftime(fmt)
    return f"{p} ET / {u}" if u != p else p
```
**Em `src/settings.py`:** ao abrir a aba, injetar JS via `st.components.v1.html` que detecta `Intl.DateTimeFormat().resolvedOptions().timeZone` e envia para o backend via uma query param ou usando `st.experimental_set_query_params`. Salvar em `user_metadata.preferred_tz` via `auth.get_client().auth.update_user({"data": {"preferred_tz": tz}})`. Cachear em `st.session_state["user_tz"]`.
**Critério de pronto:** chamar `timezones.fmt_dual(pd.Timestamp.now(tz='UTC'))` no console retorna "HH:MM ET / HH:MM <user_tz>".
**Commit:** `feat(m7): timezones.py (PRIMARY ET + user_tz auto-detectado)`

### T1.2.3 — Aplicar fuso primário no cálculo de `trade_day` (Dashboard)
**Pré-requisitos:** T1.2.2.
**Arquivos tocados:** `src/app.py` (função `load_trades`), `src/metrics.py` (`compute_daily`, `compute_overview`).
**Passo a passo:**
1. Em `load_trades`, mudar:
   ```python
   df["entry_hour"] = df["entered_at"].dt.tz_convert("America/Sao_Paulo").dt.hour
   ```
   para usar fuso secundário do usuário:
   ```python
   from timezones import user_tz, PRIMARY_TZ
   df["entry_hour"] = df["entered_at"].dt.tz_convert(user_tz()).dt.hour
   ```
2. **Importante:** `trade_day` hoje vem do CSV (ingestão calcula em `America/Chicago`). Trocar a coluna gerada pelo ingest para `America/New_York` quebra histórico. Solução: deixar `trade_day` original como está (Chicago — TopStepX usa CT na verdade na coluna `Trade Day` do CSV) e criar coluna derivada **em memória** `trade_day_et` para uso no Dashboard:
   ```python
   df["trade_day_et"] = df["entered_at"].dt.tz_convert(PRIMARY_TZ).dt.date
   ```
   Backward-compat: a coluna `trade_day` permanece no banco. Filtros e KPIs do Dashboard passam a usar `trade_day_et`.
3. Atualizar [src/metrics.py](src/metrics.py) `compute_daily`, `compute_overview`, `compute_coach` para usar `trade_day_et` em vez de `trade_day`. Trocar todas as referências internas.
**Critério de pronto:** rodar app, no Dashboard verificar que KPI "Day Win %" e calendário não mudaram radicalmente (a maioria dos trades fecha no mesmo dia ET/CT). Trades em horários extremos (após 17h CT no domingo, antes das 18h CT na segunda) podem mover entre dias — isso é esperado.
**Commit:** `feat(m7): trade_day_et derivado para uso no Dashboard (sessao NY/ET)`

### T1.2.4 — Cards e tabelas com dual-tz
**Pré-requisitos:** T1.2.3.
**Arquivos tocados:** `src/app.py` (`_trade_card`, tabela de trades), `src/coach_ai.py` (timestamps de history).
**Passo a passo:**
1. Em `_trade_card`, substituir:
   ```python
   entered = pd.to_datetime(trade["entered_at"]).tz_convert("America/Sao_Paulo")
   ```
   por:
   ```python
   from timezones import fmt_dual
   # ... no segment-row:
   <span>{fmt_dual(pd.to_datetime(trade['entered_at']))}</span>
   ```
2. Na tabela de trades (`st.dataframe(show, ...)`), adicionar coluna `entered_at_dual` antes de renderizar:
   ```python
   show["entered_at_dual"] = show["entered_at"].apply(lambda ts: timezones.fmt_dual(ts))
   ```
3. Para a aba Coach (`render_coach`), os timestamps de `losing_streak.start/end` devem usar `fmt_dual`.
**Critério de pronto:** ao ver Best Trade no Dashboard, mostra "14:32 ET / 11:32 BRT" (ou local do user).
**Commit:** `feat(m7): cards e tabela trades com dual-timezone ET + user_tz`

### T1.2.5 — Persistência da preferência em `user_metadata`
**Pré-requisitos:** T1.2.2.
**Arquivos tocados:** `src/settings.py`, `src/auth.py` (sem mudança, só leitura).
**Passo a passo:**
1. Na aba Configurações, seção "Fuso horário", se `st.session_state["user_tz"]` não estiver setado:
   - Tentar ler de `_user.get("user_metadata", {}).get("preferred_tz")`.
   - Se ainda vazio, injetar JS que detecta e faz `Streamlit.setComponentValue(tz)`. Salvar via `auth.get_client().auth.update_user({"data": {"preferred_tz": tz}})`.
2. Select dropdown com opções: lista IANA curta (`America/Sao_Paulo`, `America/New_York`, `America/Los_Angeles`, `Europe/London`, `Europe/Madrid`, `Europe/Lisbon`, etc.) + auto-detect destacado no topo.
3. Botão "Salvar" persiste em `user_metadata.preferred_tz` e atualiza `session_state["user_tz"]`.
**Critério de pronto:** setar fuso "Europe/London", recarregar o app, ainda mostra Europe/London. Logout/login mantém.
**Commit:** `feat(m7): persiste preferred_tz em user_metadata + UI Configuracoes`

### T1.2.6 — i18n nas 3 línguas
**Pré-requisitos:** T1.2.5.
**Arquivos tocados:** `locales/en.json`, `locales/pt_BR.json`, `locales/es.json`.
**Chaves novas (mínimo):**
- `tab.settings`
- `settings.title`, `settings.caption`
- `settings.section.language`, `settings.section.timezone`, `settings.section.risk_guard`, `settings.section.accounts`
- `settings.tz.primary_label`, `settings.tz.secondary_label`, `settings.tz.auto_detected`, `settings.tz.save_ok`
- `settings.risk.account_type`, `settings.risk.dll`, `settings.risk.trailing_dd`, `settings.risk.max_size`, `settings.risk.warning_pct`, `settings.risk.save_ok`, `settings.risk.locked_by_plan`
- `settings.accounts.label`, `settings.accounts.account_id`, `settings.accounts.plan_size`, `settings.accounts.active`, `settings.accounts.save_ok`
**Validar JSON:** o snippet de validação em [CLAUDE.md](CLAUDE.md) é obrigatório.
**Commit:** `i18n(m7): chaves de Configuracoes nos 3 idiomas`

---

## 6. Fase 1.1 — Refinar detecção de adições não-planejadas

**Branch:** `fusao/m8-adicoes-refined`
**Objetivo:** Estender `compute_plan_adherence` com regras adicionais inspiradas no `processor.py` Legacy do Trade_Agent.

### T1.1.1 — Inspecionar processor.py Legacy e mapear regras novas
**Pré-requisitos:** Fase 1.2 completa.
**Arquivos tocados:** apenas leitura.
**Passo a passo:**
1. Ler `E:\BD\Trade_Agent\Legacy\Dashboard Trade Pontos\backend\core\processor.py` (delegar a um `Explore` agent para extrair só a parte de detecção de adições/desvios de plano).
2. Comparar com [src/metrics.py](src/metrics.py)`compute_plan_adherence` (já trata `unplanned` e `size_exceeded`).
3. Listar regras novas que valem a pena portar. Candidatos esperados:
   - **`addition_after_stop`** — trader adiciona contratos depois de o preço já ter encostado no stop_points do plano.
   - **`addition_against_plan`** — trader opera direção oposta ao plano do dia (ex.: plano Long, abriu Short).
   - **`size_creep`** — soma de adições ao longo do dia (mesmo em grupos diferentes) ultrapassa max_size.
   - **`time_outside_window`** — trade entrou fora da janela operacional do plano (se houver `entry_window` no daily_plan — campo a adicionar via migration).
4. Documentar as regras escolhidas em comentário no topo de `metrics.py`.
**Critério de pronto:** lista das 3-5 regras novas com justificativa (1 linha cada) em comentário no código.
**Commit:** N/A (só leitura)

### T1.1.2 — Estender `compute_plan_adherence`
**Pré-requisitos:** T1.1.1.
**Arquivos tocados:** [src/metrics.py](src/metrics.py).
**Passo a passo:**
1. Refatorar `compute_plan_adherence(groups, plans)`:
   - Renomear coluna `violation_type` para aceitar enum aberto (string com até 5 valores).
   - Adicionar lógica para cada regra nova (addition_against_plan, etc.).
   - Devolver, além dos 3 contadores atuais, novos: `against_plan`, `size_creep_day`.
2. Manter back-compat: `unplanned` e `size_exceeded` continuam existindo no dict.
3. Adicionar testes inline no docstring (doctests simples).
**Critério de pronto:** rodar com CSV de teste contendo trade Short em dia que plano era Long → aparece `against_plan` no dict.
**Commit:** `feat(m8): compute_plan_adherence com 5 tipos de violacao`

### T1.1.3 — UI no Dashboard reflete novas categorias
**Pré-requisitos:** T1.1.2.
**Arquivos tocados:** [src/app.py](src/app.py) (expander "Aderência ao plano matinal"), locales.
**Passo a passo:**
1. No expander, mostrar 5 contadores em vez de 3.
2. Tabela `violations`: nova coluna "Tipo" usa labels traduzidos.
3. Chaves i18n: `dash.adherence.against_plan`, `dash.adherence.size_creep_day`, etc.
**Critério de pronto:** subir um CSV de teste e verificar que cada categoria nova é contabilizada e mostrada na UI.
**Commit:** `feat(m8): UI Dashboard mostra 5 categorias de violacao do plano`

---

## 7. Fase 2 — Extensão Chrome multi-tenant + Edge Function `live-ingest`

**Branch:** `fusao/m9-extensao-live-ingest`

### T2.1 — Migrar pasta `extension/` do Trade_Agent
**Pré-requisitos:** Fase 1.1 completa.
**Arquivos tocados:** `extension/` (nova pasta no BI TopStep).
**Passo a passo:**
1. Copiar `E:\BD\Trade_Agent\chrome_extension\*` → `e:\BD\260502 BI TopStep\extension\`.
2. Estrutura final esperada:
   ```
   extension/
     manifest.json
     background.js
     content.js
     popup.html
     popup.js
     popup.css
     offscreen.html
     offscreen.js
     selectors.json
     config.js          (NOVO)
     icons/
     sounds/
     styles/
     README.md          (NOVO)
   ```
3. Atualizar `manifest.json`:
   - `name`: "BI TopStep — Live Monitor"
   - `version`: "0.1.0"
   - `description`: "Monitor real-time TopstepX → envia snapshots para BI TopStep"
   - Manter `permissions`/`host_permissions` (incluem `notifications` que vamos usar).
4. Criar `extension/config.js`:
   ```js
   // Config injetada no build/dev. Em load unpacked, edite à mão.
   const BI_TOPSTEP_CONFIG = {
     SUPABASE_URL: "https://<seu-projeto>.supabase.co",
     LIVE_INGEST_URL: "https://<seu-projeto>.supabase.co/functions/v1/live-ingest",
     VERSION: "0.1.0",
   };
   if (typeof window !== "undefined") window.BI_TOPSTEP_CONFIG = BI_TOPSTEP_CONFIG;
   ```
5. Adicionar `config.js` em `manifest.json` em `content_scripts.js` (antes de `content.js`) e como `background.scripts` (se MV3 service_worker, importar via `importScripts`).
6. Adicionar `README.md` na pasta com instruções de load unpacked.
7. Atualizar `.gitignore` da raiz: garantir que `extension/config.local.js` (se um dia houver override) não entre — mas `config.js` com URLs públicas vai pro git.
**Critério de pronto:** `chrome://extensions` em modo dev, "load unpacked" da pasta `extension/` funciona; popup abre sem erro de console.
**Commit:** `feat(m9): migra extensao Chrome MV3 do Trade_Agent para extension/`

### T2.2 — Adaptar `background.js` para enviar snapshot para Edge Function
**Pré-requisitos:** T2.1.
**Arquivos tocados:** `extension/background.js`, `extension/popup.js`.
**Passo a passo:**
1. Em `background.js`:
   - Substituir lógica de toast Windows / alarms do Trade_Agent por: timer de 30s + listener de eventos do `content.js`.
   - Função `sendSnapshot(payload)`:
     ```js
     async function sendSnapshot(payload) {
       const cfg = self.BI_TOPSTEP_CONFIG;
       const { jwt } = await chrome.storage.local.get(["jwt"]);
       if (!jwt) return { ok: false, error: "no_jwt" };
       const res = await fetch(cfg.LIVE_INGEST_URL, {
         method: "POST",
         headers: {
           "content-type": "application/json",
           "authorization": `Bearer ${jwt}`,
           "apikey": SUPABASE_ANON_KEY,  // necessário para Edge Function aceitar
         },
         body: JSON.stringify(payload),
       });
       return { ok: res.ok, status: res.status };
     }
     ```
   - Schedule via `chrome.alarms.create("heartbeat", { periodInMinutes: 0.5 })` (30s).
2. Em `popup.js`:
   - Campo de input para colar JWT.
   - Botão "Salvar" persiste em `chrome.storage.local.set({ jwt })`.
   - Indicador de status: "Connected as <email>" (decodificando o payload do JWT — JWT é 3 partes base64 separadas por ponto; payload.email pode ser lido).
   - Botão "Testar conexão" envia `POST /live-ingest` com `{ ping: true }` e mostra resultado.
3. Atualizar `popup.html` com novo layout enxuto (sem features antigas do Trade_Agent que não se aplicam).
**Critério de pronto:** colar JWT válido + clicar "Testar conexão" → popup mostra "OK" (depois que Edge Function existir, T2.4).
**Commit:** `feat(m9): background.js envia snapshot via Edge Function + popup JWT input`

### T2.3 — Adaptar `content.js` (scrape do DOM TopstepX)
**Pré-requisitos:** T2.2.
**Arquivos tocados:** `extension/content.js`, `extension/selectors.json`.
**Passo a passo:**
1. `content.js` lê `selectors.json` via `fetch(chrome.runtime.getURL('selectors.json'))`.
2. A cada mudança detectada na página (MutationObserver) ou via timer interno de 5s:
   - Lê position size, contract, side, unrealized_pnl, realized_pnl, day_pnl, drawdown.
   - Envia para `background.js` via `chrome.runtime.sendMessage({ type: "snapshot", payload: {...} })`.
3. `background.js` faz debounce (último-write wins) e envia conforme schedule.
4. Detecção de falha de scrape (selectors quebrados): se nenhum campo crítico for lido por 3 ciclos consecutivos, marcar `chrome.storage.local.set({ scrape_status: "broken" })`. Popup lê esse status e mostra aviso "selectors desatualizados — atualize a extensão".
**Critério de pronto:** abrir `topstepx.com/trade` com extensão carregada; abrir DevTools do popup; ver no console que mensagens "snapshot" chegam ao background com posição correta.
**Commit:** `feat(m9): content.js scrape de posicao + PnL via selectors.json`

### T2.4 — Edge Function `live-ingest`
**Pré-requisitos:** T2.3.
**Arquivos tocados:** `supabase/functions/live-ingest/index.ts` (novo), `supabase/functions/live-ingest/README.md`, `.env.example`.
**Passo a passo:**
1. Criar pasta `supabase/functions/live-ingest/`.
2. `index.ts`:
   ```ts
   import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

   const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
   const SERVICE_KEY  = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

   Deno.serve(async (req) => {
     if (req.method !== "POST") return new Response("method", { status: 405 });
     const auth = req.headers.get("authorization") ?? "";
     if (!auth.startsWith("Bearer ")) return new Response("auth", { status: 401 });
     const jwt = auth.slice(7);

     // Cliente Supabase com o JWT do usuario (respeita RLS para SELECT;
     // INSERT em live_snapshots usa service_role logo abaixo).
     const userClient = createClient(SUPABASE_URL, jwt, {
       auth: { persistSession: false },
     });
     const { data: ud, error: userErr } = await userClient.auth.getUser(jwt);
     if (userErr || !ud?.user) return new Response("bad token", { status: 401 });
     const user_id = ud.user.id;

     let body: any;
     try { body = await req.json(); } catch { return new Response("json", { status: 400 }); }

     if (body?.ping) return new Response(JSON.stringify({ ok: true, user_id }), {
       status: 200, headers: { "content-type": "application/json" },
     });

     // Validação mínima de payload
     const required = ["account_id", "snapshot_at"];
     for (const k of required) {
       if (body[k] === undefined) return new Response(`missing ${k}`, { status: 400 });
     }

     const admin = createClient(SUPABASE_URL, SERVICE_KEY, { auth: { persistSession: false }});
     const row = {
       user_id,
       account_id: String(body.account_id),
       snapshot_at: body.snapshot_at,
       position_size: body.position_size ?? 0,
       position_avg_price: body.position_avg_price ?? null,
       position_contract: body.position_contract ?? null,
       position_side: body.position_side ?? null,
       unrealized_pnl: body.unrealized_pnl ?? 0,
       realized_pnl: body.realized_pnl ?? 0,
       day_pnl: body.day_pnl ?? 0,
       drawdown: body.drawdown ?? 0,
       raw: body,
     };
     const { error } = await admin.from("live_snapshots").insert(row);
     if (error) return new Response(`db error: ${error.message}`, { status: 500 });

     return new Response(JSON.stringify({ ok: true }), {
       status: 200, headers: { "content-type": "application/json" },
     });
   });
   ```
3. `README.md` da função: documentar payload esperado, deploy via `supabase functions deploy live-ingest`, secrets necessárias (apenas `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY` — ambas injetadas pela plataforma).
4. `.env.example`: vazio (usa só secrets injetadas).
**Critério de pronto:** após deploy, `curl -X POST <url>/live-ingest -H "authorization: Bearer <jwt valido>" -H "content-type: application/json" -d '{"ping": true}'` retorna `{ok: true, user_id: "..."}`.
**Commit:** `feat(m9): Edge Function live-ingest (JWT auth + INSERT em live_snapshots)`

### T2.5 — Botão "Gerar token de extensão" na aba Account
**Pré-requisitos:** T2.4.
**Arquivos tocados:** `src/account.py`, locales.
**Passo a passo:**
1. Em `render_account_tab`, adicionar nova seção (visível apenas para `billing.has_feature(plan, 'live_monitor')`):
   ```python
   if billing.has_feature(plan, "live_monitor"):
       st.divider()
       st.markdown(f"#### {t('account.extension_section')}")
       sess = st.session_state.get("session", {})
       jwt = sess.get("access_token", "")
       if jwt:
           st.text_input(t("account.extension_jwt_label"), value=jwt, type="password")
           st.caption(t("account.extension_jwt_hint"))
   ```
2. UX: o `text_input` em modo password tem botão "ver" nativo do Streamlit que permite copiar.
3. Atualizar `saas_schema.sql` (ou criar `PRD/m9_features.sql`) para garantir que `plans.features` do plano `pro` tem `"live_monitor": true`:
   ```sql
   update public.plans set features = features || '{"live_monitor": true}'::jsonb
     where slug in ('pro', 'admin', 'trial');
   ```
**Critério de pronto:** logar como usuário Pro/Trial → aba Account mostra campo do JWT. Logar como Basic/Free → seção não aparece.
**Commit:** `feat(m9): botao gerar JWT extensao em Account + feature flag live_monitor`

### T2.6 — Aba "Live" / "Extensão" no app (Pro)
**Pré-requisitos:** T2.5.
**Arquivos tocados:** `src/live.py` (novo), `src/app.py`, locales.
**Passo a passo:**
1. `src/live.py` exporta `render_live_tab(user, plan)`.
2. Conteúdo:
   - Se não Pro/Trial: paywall.
   - **Sub-seção 1 — Instalação da extensão**: passo a passo com texto + link de download do `.zip` (servido do Supabase Storage; bucket `public/extension/`). Botão "Baixar última versão" → URL pública do Storage.
   - **Sub-seção 2 — Status da conexão**: lê última linha de `live_snapshots` filtrado por user_id. Mostra "Conectado há Xs" ou "Sem snapshot há Xs". Se >2 min sem snapshot, mostra alerta.
   - **Sub-seção 3 — Widget de posição atual**: posição, contrato, preço médio, PnL não realizado, PnL do dia. Vazio se não houver position.
   - **Sub-seção 4 — Alertas recentes**: tabela das últimas 20 linhas de `alerts` (descendente por created_at). Coluna "Lido" toggleable (atualiza `read_at`).
3. Em `src/app.py`, adicionar aba "Live" (entre Day Plan e Plan, ou onde fizer sentido). Visível somente se Pro/Trial.
4. i18n: `tab.live`, `live.title`, `live.section.install`, `live.section.status`, `live.section.position`, `live.section.alerts`, etc.
**Critério de pronto:** aba Live aparece para Pro/Trial; mostra "Sem snapshot" se a extensão ainda não enviou nada.
**Commit:** `feat(m9): aba Live com instalacao, status, posicao e alertas`

### T2.7 — Empacotar e publicar `.zip` da extensão no Supabase Storage
**Pré-requisitos:** T2.6.
**Arquivos tocados:** `scripts/package_extension.py` (novo, ou `.bat`).
**Passo a passo:**
1. Criar `scripts/package_extension.py` que:
   - Gera `extension-<version>.zip` da pasta `extension/`.
   - (Manual ou via script) sobe para bucket público `extension/` no Supabase Storage.
   - Atualiza tabela `app_releases` (criar se quiser histórico) ou apenas atualiza o link no `live.py` apontando para a URL fixa do último zip.
2. **Decisão MVP**: pular tabela `app_releases`. Hardcode na `live.py` a URL pública do `.zip` (`https://<projeto>.supabase.co/storage/v1/object/public/extension/extension-latest.zip`).
3. Script gera nome `extension-latest.zip` (sobrescreve a cada release).
**Critério de pronto:** rodar o script gera `extension-latest.zip`. Subir manualmente no Supabase Studio Storage. URL pública retorna o `.zip` corretamente.
**Commit:** `feat(m9): script package_extension.py + zip publicado no Supabase Storage`

### T2.8 — Wrap-up Fase 2
**Passo a passo:**
1. Validação end-to-end:
   - Usuário Pro loga → aba Account copia JWT.
   - Cola JWT no popup da extensão → testa conexão → 200 OK.
   - Abre TopstepX, abre uma posição teste (sandbox/practice).
   - Após até 30s, aba Live do app mostra a posição.
2. Atualizar `MEMORIA.md` com entrada datada.
3. Atualizar `CLAUDE.md` da raiz (seção Arquitetura) com a nova pasta `extension/` e a Edge Function `live-ingest`.
**Commit:** `docs(m9): registra fim da Fase 2 (extensao + live-ingest) no MEMORIA`

---

## 8. Fase 3 — Realtime, Risk Guard e Web Notifications

**Branch:** `fusao/m10-realtime-risk-guard`

### T3.1 — CRUD `risk_settings` na aba Configurações
**Pré-requisitos:** Fase 2 completa.
**Arquivos tocados:** `src/settings.py`, `src/risk_settings.py` (novo módulo CRUD), locales.
**Passo a passo:**
1. Criar `src/risk_settings.py` com:
   - `get_settings(user_id) -> dict | None` — `client.table("risk_settings").select("*").maybeSingle().execute()`.
   - `upsert_settings(payload: dict) -> dict` — upsert; campos opcionais aceitos.
2. Em `src/settings.py`, na seção Risk Guard (apenas se `live_monitor`):
   - Formulário com 5 campos: `account_type` (select com "Express 50K", "Express 100K", "Express 150K", "Custom"), `daily_loss_limit_usd` (number), `trailing_drawdown_usd` (number), `max_position_size` (number), `warning_threshold_pct` (number, default 80).
   - Botão "Salvar" → `upsert_settings`.
   - Se `account_type` mudar, sugerir defaults (Express 50K → DLL $1000, Trailing $2000) em info banner.
**Critério de pronto:** salvar → recarregar → valores persistem. RLS isola entre usuários.
**Commit:** `feat(m10): CRUD risk_settings em Configuracoes`

### T3.2 — Trigger Postgres `risk_guard_eval` em `live_snapshots`
**Pré-requisitos:** T3.1.
**Arquivos tocados:** `PRD/m10_risk_guard_trigger.sql` (novo).
**SQL:**
```sql
-- PRD/m10_risk_guard_trigger.sql
create or replace function public.risk_guard_eval()
returns trigger language plpgsql as $$
declare
    rs record;
    warn_threshold numeric;
    dll_remaining numeric;
    dd_remaining numeric;
begin
    select * into rs from public.risk_settings where user_id = new.user_id;
    if not found then return new; end if;

    -- Daily Loss Limit
    if rs.daily_loss_limit_usd is not null and rs.daily_loss_limit_usd > 0 then
        dll_remaining := rs.daily_loss_limit_usd + new.day_pnl;
        warn_threshold := rs.daily_loss_limit_usd * (1 - rs.warning_threshold_pct / 100.0);
        if new.day_pnl <= -rs.daily_loss_limit_usd then
            insert into public.alerts (user_id, alert_type, severity, source, title, body, payload)
            values (new.user_id, 'daily_loss_limit', 'critical', 'risk_guard',
                'Daily Loss Limit atingido',
                'PnL do dia: ' || new.day_pnl || ' USD',
                jsonb_build_object('snapshot_id', new.id, 'day_pnl', new.day_pnl));
        elsif dll_remaining <= warn_threshold then
            insert into public.alerts (user_id, alert_type, severity, source, title, body, payload)
            values (new.user_id, 'daily_loss_limit', 'warn', 'risk_guard',
                'Próximo do Daily Loss Limit',
                'Restam ' || dll_remaining || ' USD para o limite',
                jsonb_build_object('snapshot_id', new.id));
        end if;
    end if;

    -- Trailing Drawdown
    if rs.trailing_drawdown_usd is not null and new.drawdown >= rs.trailing_drawdown_usd then
        insert into public.alerts (user_id, alert_type, severity, source, title, body, payload)
        values (new.user_id, 'trailing_drawdown', 'critical', 'risk_guard',
            'Trailing Drawdown atingido',
            'Drawdown: ' || new.drawdown || ' USD',
            jsonb_build_object('snapshot_id', new.id));
    end if;

    -- Max Position Size
    if rs.max_position_size is not null and new.position_size > rs.max_position_size then
        insert into public.alerts (user_id, alert_type, severity, source, title, body, payload)
        values (new.user_id, 'max_position_size', 'warn', 'risk_guard',
            'Tamanho de posição acima do limite',
            'Posição atual: ' || new.position_size,
            jsonb_build_object('snapshot_id', new.id, 'position_size', new.position_size));
    end if;

    -- Unplanned addition: posição abaixo > max_size do daily_plans do dia atual
    -- (em America/New_York). Verificado contra plan do contrato.
    if new.position_contract is not null and new.position_size > 0 then
        declare
            plan_max integer;
            today_et date := (new.snapshot_at at time zone 'America/New_York')::date;
        begin
            select max_size into plan_max from public.daily_plans
             where user_id = new.user_id and plan_date = today_et
               and contract_name = new.position_contract
               and direction = new.position_side;
            if plan_max is not null and new.position_size > plan_max then
                insert into public.alerts (user_id, alert_type, severity, source, title, body, payload)
                values (new.user_id, 'unplanned_addition', 'warn', 'risk_guard',
                    'Adição não-planejada detectada',
                    'Posição ' || new.position_size || ' acima do plano (' || plan_max || ')',
                    jsonb_build_object('snapshot_id', new.id, 'plan_max_size', plan_max));
            end if;
        end;
    end if;

    return new;
end $$;

drop trigger if exists trg_risk_guard on public.live_snapshots;
create trigger trg_risk_guard
    after insert on public.live_snapshots
    for each row execute function public.risk_guard_eval();
```
**Critério de pronto:** inserir manualmente em `live_snapshots` com `day_pnl = -1500` para usuário com `risk_settings.daily_loss_limit_usd = 1000` → aparece linha em `alerts` com `alert_type='daily_loss_limit'`, `severity='critical'`.
**Commit:** `feat(m10): trigger risk_guard_eval cria alerts a partir de live_snapshots`

### T3.3 — Aba Live com polling via `st_autorefresh`
**Pré-requisitos:** T3.2.
**Arquivos tocados:** `src/live.py`, `requirements.txt` (adicionar `streamlit-autorefresh`).
**Passo a passo:**
1. `pip install streamlit-autorefresh` na `.venv` e atualizar `requirements.txt`.
2. Em `src/live.py`:
   ```python
   from streamlit_autorefresh import st_autorefresh
   st_autorefresh(interval=3000, key="live_refresh")
   ```
3. Cada refresh re-lê `live_snapshots` (`order by snapshot_at desc limit 1`) e `alerts` (`order by created_at desc limit 20`).
4. Cache: usar `@st.cache_data(ttl=2)` com `user_id` na chave.
**Critério de pronto:** abrir aba Live com extensão enviando snapshots → posição atualiza em até 3s. Inserir alerta manualmente no banco → aparece na lista em até 3s.
**Commit:** `feat(m10): aba Live com st_autorefresh (3s)`

### T3.4 — Web Notifications API via componente HTML/JS
**Pré-requisitos:** T3.3.
**Arquivos tocados:** `src/components/notifications.py` (novo) ou `src/live.py` direto.
**Passo a passo:**
1. Em `src/live.py`, no topo da aba (só se Pro/Trial), injetar:
   ```python
   import streamlit.components.v1 as components
   SUPABASE_URL = auth._read_secret("SUPABASE_URL")
   SUPABASE_ANON = auth._read_secret("SUPABASE_ANON_KEY")
   jwt = st.session_state["session"]["access_token"]
   user_id = _user["id"]
   components.html(f"""
   <script type="module">
     import {{ createClient }} from "https://esm.sh/@supabase/supabase-js@2.45.4";
     const supa = createClient("{SUPABASE_URL}", "{SUPABASE_ANON}");
     await supa.auth.setSession({{ access_token: "{jwt}", refresh_token: "" }});
     if (Notification.permission === "default") await Notification.requestPermission();
     supa.channel("alerts:{user_id}")
         .on("postgres_changes",
             {{ event: "INSERT", schema: "public", table: "alerts",
                filter: "user_id=eq.{user_id}" }},
             (p) => {{
                if (Notification.permission === "granted") {{
                   new Notification(p.new.title, {{ body: p.new.body || "", tag: String(p.new.id) }});
                }}
             }})
         .subscribe();
   </script>
   """, height=0)
   ```
2. **Importante:** o JWT é incluído no HTML — só é seguro porque é o JWT do próprio usuário logado, exposto apenas ao próprio browser. Não compartilhar logs.
3. Em produção (Streamlit Cloud), pode-se servir via Supabase Realtime sem headers extras.
**Critério de pronto:** primeira visita à aba Live → browser pede permissão de notificação. Aceitar → inserir alerta manual no banco → notificação nativa do OS aparece (mesmo com app em outra aba).
**Commit:** `feat(m10): Web Notifications API via componente HTML escutando alerts`

### T3.5 — Marcar alerta como lido / dismissed
**Pré-requisitos:** T3.4.
**Arquivos tocados:** `src/live.py`, `src/alerts.py` (novo módulo CRUD).
**Passo a passo:**
1. `src/alerts.py`:
   - `list_recent(limit=20)` → lê alertas.
   - `mark_read(alert_id)` → `update alerts set read_at = now() where id = ?`.
   - `mark_dismissed(alert_id)` → `update alerts set dismissed_at = now() where id = ?`.
2. UI na aba Live: tabela com botões inline "Marcar como lido" e "Descartar".
**Critério de pronto:** clicar "Marcar como lido" remove o destaque visual; recarregar mantém.
**Commit:** `feat(m10): alerts CRUD (mark_read/dismissed) + UI`

### T3.6 — Wrap-up Fase 3
**Passo a passo:**
1. Validação end-to-end:
   - Configurar `risk_settings` com DLL $1000.
   - Extensão envia snapshot com `day_pnl = -800` (80% do limite) → alerta `warn` aparece.
   - Snapshot com `day_pnl = -1100` → alerta `critical` aparece + notificação push.
2. Atualizar `MEMORIA.md` + `DECISOES.md` (se houve decisão arquitetural — provavelmente sim sobre trigger Postgres ser preferido a Edge Function).
**Commit:** `docs(m10): registra fim da Fase 3 (realtime + risk guard) no MEMORIA`

---

## 9. Fase 4 — Cleanup

**Branch:** `fusao/m11-cleanup-trade-agent`

### T4.1 — Gerar `ENCERRAMENTO.md` no Trade_Agent
**Pré-requisitos:** Fase 3 completa.
**Arquivos tocados:** `E:\BD\Trade_Agent\ENCERRAMENTO.md` (novo).
**Passo a passo:**
1. Seguir o template de `ENCERRAMENTO.md` definido no [CLAUDE.md global](C:\Users\henrique.tamaki\.claude\CLAUDE.md).
2. Conteúdo principal: post-mortem do Trade_Agent + lista do que migrou para BI TopStep com ponteiros (commit hash, arquivo no BI TopStep).
3. Histórico Git seção: top 10 commits estratégicos do Trade_Agent.
**Commit (no Trade_Agent):** `docs: ENCERRAMENTO.md (projeto absorvido por BI TopStep)`

### T4.2 — Marcar Trade_Agent como read-only
**Passo a passo:**
1. No GitHub/GitLab/etc. do Trade_Agent (se houver remote), arquivar o repo.
2. Tag final: `git tag -a v-archived -m "Projeto arquivado — funcionalidades migradas para BI TopStep"`.
3. Atualizar `README.md` do Trade_Agent na primeira linha: "⚠️ ARQUIVADO — ver BI TopStep (`E:\BD\260502 BI TopStep`)".

### T4.3 — Atualizar MEMORIA.md e DECISOES.md do BI TopStep
**Passo a passo:**
1. `MEMORIA.md` — entrada datada de conclusão da fusão.
2. `DECISOES.md` — eventual nova entrada se algo grande mudou de rumo durante a execução.
3. `CLAUDE.md` da raiz — atualizar para refletir o estado final.
**Commit:** `docs: registra conclusao da fusao Trade_Agent → BI TopStep (MEMORIA + DECISOES)`

### T4.4 — `/security-review` + `/review` finais
**Passo a passo:**
1. Invocar `/security-review` no estado final do branch antes do merge (especialmente Edge Function `live-ingest`, trigger `risk_guard_eval`, RLS de todas as tabelas novas).
2. Invocar `/review` no diff acumulado.
3. Tratar findings antes do merge final em `main`.

---

## 10. Validação end-to-end (smoke test após cada fase)

| Fase | Smoke test |
|---|---|
| 1.3 | `select tablename from pg_tables where schemaname='public';` lista as 7 tabelas novas. RLS bloqueia cross-user em todas. |
| 1.2 | Trocar fuso secundário em Configurações → cards no Dashboard atualizam dual-tz na hora. trade_day_et bate com a sessão NY/ET. |
| 1.1 | CSV de teste com adição contra plano → expander mostra `against_plan`. |
| 2 | Pro user cola JWT → popup conecta → posição na sandbox aparece na aba Live em <30s. RLS isola entre 2 contas de teste. |
| 3 | Alerta crítico dispara → notificação push aparece com app em outra aba. Polling de 3s atualiza widgets sem refresh manual. |
| 4 | Trade_Agent tem `ENCERRAMENTO.md` + tag `v-archived`. MEMORIA do BI TopStep cita a fusão concluída. |

---

## 11. Backlog explícito (fora deste guia)

- OAuth Supabase dentro da extensão (substituir token colado).
- Chrome Web Store (publicação oficial).
- Múltiplas contas TopStep ativas por trader.
- Edge Function dedicada `risk-guard` (caso a lógica do trigger fique grande demais).
- i18n da extensão Chrome (EN/PT-BR/ES).
- Tabela `app_releases` para histórico de versões da extensão.
- Coach IA automático com Claude API (hoje copy-paste).
- Partitioning de `live_snapshots` por dia (caso o volume cresça).
- Importar dados históricos do Trade_Agent local (descartado no MVP, mas pode ressurgir).
- Downsampling automático: snapshots crus 24h + agregado diário.
- Service Worker dedicado para Web Notifications (em vez de componente HTML inline).
- Selectors do TopstepX: pipeline de testes automatizado (Playwright contra sandbox).

---

## 12. Glossário rápido

- **ET / NY**: `America/New_York`, fuso da sessão CME (18:00 ET = open futuros).
- **Daily Loss Limit (DLL)**: regra TopStep de perda máxima diária permitida na conta avaliada.
- **Trailing Drawdown**: regra TopStep de drawdown máximo desde o peak da conta.
- **Snapshot**: estado capturado da conta do trader (posição + PnL + drawdown) num momento.
- **Adição não-planejada**: trade que viola o plano matinal (size acima do max, contrato fora, direção contrária).
- **JWT colado**: padrão MVP de auth da extensão. Trader copia da aba Account e cola no popup.
- **st_autorefresh**: hook do `streamlit-autorefresh` que força rerender do widget em intervalo fixo.
- **Realtime**: assinatura via Supabase `postgres_changes` para reagir a INSERTs em `alerts` no front.
