# Edge Function — `live-ingest`

Recebe snapshots de posição/PnL do trader (enviados pela extensão Chrome `BI TopStep — Live Monitor`) e grava em `public.live_snapshots`, isolado por `user_id` derivado do JWT.

## Endpoint

```
POST https://<projeto>.supabase.co/functions/v1/live-ingest
```

Headers obrigatórios:
- `Authorization: Bearer <JWT>` — JWT do usuário logado no app BI TopStep.
- `apikey: <SUPABASE_ANON_KEY>`
- `content-type: application/json`

## Modos de uso

### Ping (teste de autenticação)
```json
POST /functions/v1/live-ingest
{ "ping": true }
```
Resposta 200: `{ "ok": true, "user_id": "...", "mode": "ping" }`

### Snapshot real
```json
POST /functions/v1/live-ingest
{
  "account_id": "PRACTICEAPR152025112233",
  "snapshot_at": "2026-05-20T20:31:42.123Z",
  "position_size": 2,
  "position_avg_price": 18450.25,
  "position_contract": "MNQ",
  "position_side": "Long",
  "unrealized_pnl": 12.50,
  "realized_pnl": 145.00,
  "day_pnl": 157.50,
  "drawdown": 0
}
```
Resposta 200: `{ "ok": true, "user_id": "..." }`

Campos obrigatórios: `account_id`, `snapshot_at`. Demais são opcionais (default 0/null).

## Erros possíveis

| Código | Causa |
|---|---|
| 401 | Token Bearer ausente ou inválido |
| 400 | JSON malformado, campo obrigatório faltando, `snapshot_at` fora da janela de ±5min ou `account_id` com tamanho inválido |
| 405 | Método ≠ POST |
| 413 | Payload > 16KB |
| 429 | Rate limit excedido (>12 req/min por user_id) |
| 500 | Erro no INSERT do Postgres (logado em `console.error`) |

## Logs estruturados

Toda chamada emite uma linha JSON no console (capturada em Supabase → Logs → Edge Functions). Formato:

```json
{"ts":"2026-05-20T20:31:42.123Z","lvl":"info","fn":"live-ingest","evt":"ingest_ok","user_id":"<uuid>","account_id":"...","contract":"MNQ","size":2,"day_pnl":157.5,"ms":42}
```

Eventos:
- `info` — `ping_ok`, `ingest_ok` (caminho feliz).
- `warn` — `auth_missing_bearer`, `auth_invalid_token`, `rate_limited`, `payload_too_large`, `invalid_json`, `missing_field`, `invalid_snapshot_at`, `snapshot_at_out_of_window`, `invalid_account_id_length`, `method_not_allowed`.
- `error` — `db_error` (problema do Postgres).

O `raw` do snapshot **nunca** é logado (privacidade). Filtragem no Logflare/SQL: `metadata.evt = 'rate_limited'` ou `metadata.user_id = '<uuid>'`.

## Limites defensivos

- **Payload:** máximo 16KB. Snapshots legítimos ficam em ~1-2KB.
- **Rate limit:** 12 req/min por `user_id` (bucket em memória do worker; soft limit). Cobre heartbeat de 30s + eventos esporádicos com folga.
- **Skew de relógio:** `snapshot_at` deve estar a ≤5min do agora do servidor. Rejeita backfill e relógio dessincronizado.
- **Ranges:** `position_size` clampado em `[0, 10_000]` contratos; PnL/drawdown clampados em `[-10M, +10M]` USD; `account_id` em `[1, 64]` chars; `position_contract` truncado em 32 chars. Valores fora dos limites são corrigidos silenciosamente (clamp), não rejeitam o request — uma extensão com bug não trava de imediato, mas dados absurdos não chegam ao banco.

## Segurança

- O `user_id` gravado em `live_snapshots` vem **sempre** de `supabase.auth.getUser(jwt)` server-side, nunca de um campo do payload. Cliente malicioso não consegue gravar em nome de outro usuário.
- INSERT usa `SERVICE_ROLE` (bypass RLS) apenas para evitar overhead de troca de contexto; RLS continua protegendo SELECT pelos próprios usuários.

## Deploy

```bash
# Pré-requisito: supabase CLI instalada e linkada ao projeto
supabase functions deploy live-ingest

# Secrets já injetadas automaticamente pela plataforma:
#   SUPABASE_URL
#   SUPABASE_SERVICE_ROLE_KEY
# Nenhuma config manual necessária.
```

## Teste local

```bash
# Servir localmente:
supabase functions serve live-ingest --no-verify-jwt=false --env-file ./supabase/functions/live-ingest/.env

# Ping (obtenha um JWT real fazendo login no app):
curl -X POST http://localhost:54321/functions/v1/live-ingest \
  -H "authorization: Bearer $JWT" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "content-type: application/json" \
  -d '{"ping": true}'
```

## Idempotência

A função usa `upsert(..., onConflict: "user_id,account_id,snapshot_at", ignoreDuplicates: true)`, que casa com a UNIQUE constraint adicionada por [PRD/m11_live_snapshots_unique.sql](../../../PRD/m11_live_snapshots_unique.sql). Retries de rede ou reinstall da extensão não inflam duplicatas — o segundo POST com mesmo `(user_id, account_id, snapshot_at)` é silenciosamente ignorado.

## Backlog explícito

- Rate limit persistente (hoje é em memória do worker — reseta em cold start; suficiente para abuso casual mas não para distributed flood). Mover para Redis/Postgres se virar gargalo.
- Substituir dependência runtime de `esm.sh` por bundle vendored no Supabase Storage.
