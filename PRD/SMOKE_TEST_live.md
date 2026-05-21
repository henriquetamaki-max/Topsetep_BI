# Smoke test — Live Monitor (pós-deploy)

Roteiro de validação end-to-end após executar os 3 passos manuais de deploy:

1. Criar bucket público `extension` no Supabase Storage.
2. Subir `dist/extension/extension-latest.zip` no bucket.
3. `supabase login` + `supabase functions deploy live-ingest --no-verify-jwt`.

Cobre 4 checkpoints: **Edge Function viva → extensão carrega → snapshot fluindo → Risk Guard dispara alerta**.

## Pré-requisitos no Supabase

- [ ] Migrations aplicadas: `schema.sql`, `saas_schema.sql`, `m5_daily_plans.sql`, `m6_*.sql`, `m9_features.sql`, `m10_risk_guard_trigger.sql`, `m11_live_snapshots_unique.sql`.
- [ ] Bucket público `extension` existe e contém `extension-latest.zip`.
- [ ] Edge Function `live-ingest` deployada (Functions → live-ingest → status Ready).
- [ ] Usuário de teste com `subscriptions.plan_slug in ('pro','admin','trial')`. Sem isso a aba Live nem aparece.

## Checkpoint 1 — Edge Function viva (sem extensão)

Pegue um JWT real fazendo login no app (`streamlit run src/app.py` → login → no console do browser: `JSON.parse(localStorage.getItem('sb-<ref>-auth-token')).access_token`).

```bash
curl -X POST https://qjzouhdrhoxmtinidsqv.supabase.co/functions/v1/live-ingest \
  -H "authorization: Bearer $JWT" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "content-type: application/json" \
  -d '{"ping": true}'
```

- [ ] Resposta 200 com `{"ok":true,"user_id":"<uuid>","mode":"ping"}`.
- [ ] Em Logs → Edge Functions, aparece `{"evt":"ping_ok","user_id":"<uuid>",...}`.

**Falhou?** Confira:
- 401 → JWT colado errado/expirado, ou function deployada com `verify_jwt=true` (deve ser `--no-verify-jwt`).
- 405 → veio sem `POST` (alguém testou com GET).
- 429 → você está testando rápido demais, espere 1 min.

## Checkpoint 2 — Extensão carrega e conecta

1. Baixe o zip do Supabase Storage (URL pública: `https://qjzouhdrhoxmtinidsqv.supabase.co/storage/v1/object/public/extension/extension-latest.zip`) ou pegue local em `dist/extension/extension-latest.zip`.
2. Extraia em uma pasta.
3. Chrome → `chrome://extensions` → ativar Developer mode → "Load unpacked" → selecionar a pasta.
4. Pin do ícone na toolbar → abrir popup.

- [ ] Popup mostra "Config: https://qjzouhdrhoxmtinidsqv.supabase.co".
- [ ] Cole o JWT obtido no Checkpoint 1 → "Save token" → linha vira "Signed in as `<email>` · expires `<iso>`".
- [ ] "Test connection" → "Last send: HH:MM:SS · 2XX (ping)".

**Falhou?** Confira:
- "Config: NOT SET" → o `config.js` não foi injetado no zip; rerun `scripts/package_extension.py --supabase-url X --anon-key Y`.
- "Invalid token" no Save → JWT do projeto errado (cole de novo) ou expirado.
- "Last send: 401" → o JWT foi aceito pelo popup mas o servidor rejeitou. Pode ser desvio de relógio (`snapshot_at_out_of_window`) ou JWT de outro env.

## Checkpoint 3 — Snapshot real fluindo

Abra `https://topstepx.com/trade`, abra uma posição test (1 contrato MNQ na conta practice).

- [ ] Em ≤ 30s, popup mostra "Scrape: OK" e "Last send: HH:MM:SS · 2XX (event)".
- [ ] No app Streamlit, aba **Live** mostra: position size = 1, contract = MNQ, side = Long, day_pnl atualizando.
- [ ] Em Supabase → Table Editor → `live_snapshots`: nova row com seu `user_id`.
- [ ] Em Logs → Edge Functions: linha `{"evt":"ingest_ok","contract":"MNQ","size":1,...}`.

**Falhou?** Confira:
- Aba Live diz "no snapshots in last 60s" → extensão não está enviando. Inspecionar popup → "Last send" (se 4xx, ler `code`/`detail`); ou Service Worker em `chrome://extensions` → "service worker" link → console.
- `live_snapshots` vazia mas log de `ingest_ok` aparece → trigger m10 está descartando? Improvável; verifique RLS na sessão.
- `Scrape: selectors out of date` → DOM do TopstepX mudou; ajustar `extension/selectors.json` e re-empacotar.

## Checkpoint 4 — Risk Guard dispara

Na aba **Configurações** do app, configure:
- account_type: Express 50K
- daily_loss_limit_usd: 50 (limite ridículo de propósito)
- max_position_size: 5
- save.

Volte ao chart TopstepX e force uma das violações:
- **DLL**: dê stop num trade pequeno até `day_pnl < -50`.
- **Max Size**: abra 6+ contratos.
- **Unplanned Addition**: tenha um plano em Daily Plan para MNQ Long com max_size=2 e abra 3+.

- [ ] Em ≤ 5s após o snapshot, aba Live mostra alerta novo (cartão colorido por severity).
- [ ] **Web Notification** aparece no canto do browser (precisa de permissão concedida no primeiro load da aba Live).
- [ ] Em `alerts` table: row nova com `severity in ('warn','critical')` e `dismissed_at is null`.
- [ ] Dispare uma 2ª violação do mesmo tipo em <5min: **não** deve criar alerta novo (cooldown). Aguarde 5min e re-teste para confirmar.

**Falhou?** Confira:
- Alerta não aparece → conferir `risk_settings` populada (ela é por user_id, RLS isolada) e se o `live_snapshot` chegou (Checkpoint 3 OK).
- Notification não dispara → permissão negada no browser. `chrome://settings/content/notifications` → permitir para `localhost`/domínio do Streamlit.
- 2ª violação cria alerta duplicado → bug no cooldown; verificar `_rg_insert_alert` em `m10_risk_guard_trigger.sql`.

## Limpeza após teste

- Marcar alertas teste como `dismissed` na própria aba Live.
- Resetar `risk_settings` para valores realistas (DLL ~ 80% do limite da conta TopStep).
- Fechar a posição test no TopstepX.

## O que esse smoke NÃO cobre

- Carga sustentada (rate limit em produção; teste de carga é outro exercício).
- DST do `America/New_York` (passar pela borda de horário só na primeira semana de novembro / segundo domingo de março).
- Cenário multi-conta (1 user com 2 accounts ativas — backlog, hoje só 1 ativa).
- Modo offline → resync (extensão sem internet por X min → snapshots acumulam? Spoiler: hoje são descartados, não há queue).
