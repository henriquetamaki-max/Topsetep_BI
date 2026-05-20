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
| 400 | JSON malformado ou campo obrigatório faltando |
| 405 | Método ≠ POST |
| 500 | Erro no INSERT do Postgres (logado em `console.error`) |

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

## Backlog explícito

- Idempotência por `(user_id, account_id, snapshot_at)` para tolerar retries do cliente sem inflar duplicatas.
- Rate limit por user_id (ex.: 5 req/s) para evitar abuso.
- Validação de range (`position_size` razoável, timestamps recentes).
- Métricas de latência (Edge Function logs estruturados).
