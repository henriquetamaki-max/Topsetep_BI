// Live Ingest → Supabase
//
// Recebe snapshots de posicao/PnL da extensao Chrome BI TopStep Live Monitor
// (extension/) e grava em public.live_snapshots, isolado por user_id derivado
// do JWT do Supabase Auth.
//
// Autenticacao:
//   - Cliente envia Authorization: Bearer <JWT> + apikey: <ANON_KEY>.
//   - Validacao do JWT via supabase.auth.getUser() (chamada server-side, segura).
//   - INSERT em live_snapshots usa SERVICE_ROLE para bypass de RLS, mas o
//     user_id e' sempre o do token validado (nunca o que o cliente mandou).
//
// Endpoint suporta dois modos:
//   - { ping: true }  → so confirma autenticacao, retorna { ok, user_id }.
//   - payload normal  → INSERT em live_snapshots.
//
// Secrets esperados (injetados automaticamente pela plataforma Supabase):
//   SUPABASE_URL
//   SUPABASE_SERVICE_ROLE_KEY

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;

const ALLOWED_ORIGINS = [
  "chrome-extension://",  // qualquer ext local (load unpacked)
];

// Log estruturado (JSON em uma linha). Supabase preserva stdout/stderr em
// Logs > Edge Functions; JSON facilita filtrar/agregar via SQL no Logflare.
// Convencao: { ts, lvl, fn, evt, ...campos }. user_id e sempre incluido
// quando ja foi resolvido; payload bruto nunca e logado (privacidade).
type LogLevel = "info" | "warn" | "error";
function log(lvl: LogLevel, evt: string, fields: Record<string, unknown> = {}): void {
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    lvl,
    fn: "live-ingest",
    evt,
    ...fields,
  });
  if (lvl === "error") console.error(line);
  else console.log(line);
}

// Limites defensivos. Snapshot legitimo da extensao fica em ~1-2KB; cap de 16KB
// pega payload sob ataque sem afetar uso real.
const MAX_BODY_BYTES = 16 * 1024;
// Janela de aceitacao do snapshot_at (em ms). Tolera relogio dessincronizado
// do cliente em ate +/- 5min, mas rejeita backfill obvio.
const SNAPSHOT_AT_SKEW_MS = 5 * 60 * 1000;
// Rate limit em memoria: ~12 req/min por user_id (heartbeat 30s + algumas
// mudancas de evento). Bursts curtos passam; flood sustentado bloqueia.
const RL_WINDOW_MS = 60_000;
const RL_MAX_PER_WINDOW = 12;
const rlBuckets = new Map<string, { count: number; resetAt: number }>();

function rateLimit(user_id: string): boolean {
  const now = Date.now();
  const b = rlBuckets.get(user_id);
  if (!b || b.resetAt <= now) {
    rlBuckets.set(user_id, { count: 1, resetAt: now + RL_WINDOW_MS });
    return true;
  }
  if (b.count >= RL_MAX_PER_WINDOW) return false;
  b.count += 1;
  return true;
}

function corsHeaders(origin: string | null): HeadersInit {
  const allow = origin && ALLOWED_ORIGINS.some((p) => origin.startsWith(p))
    ? origin
    : "*";
  return {
    "access-control-allow-origin": allow,
    "access-control-allow-headers": "authorization, apikey, content-type",
    "access-control-allow-methods": "POST, OPTIONS",
  };
}

Deno.serve(async (req) => {
  const t0 = performance.now();
  const origin = req.headers.get("origin");
  const cors = corsHeaders(origin);

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: cors });
  }
  if (req.method !== "POST") {
    log("warn", "method_not_allowed", { method: req.method });
    return new Response("method not allowed", { status: 405, headers: cors });
  }

  const authHeader = req.headers.get("authorization") ?? "";
  if (!authHeader.startsWith("Bearer ")) {
    log("warn", "auth_missing_bearer");
    return new Response("missing bearer token", { status: 401, headers: cors });
  }
  const jwt = authHeader.slice(7).trim();

  // Cliente leve so para validar o JWT. O 2o arg e' a apikey (anon), nao o
  // JWT do usuario — passar o JWT como apikey falha desde a migracao para
  // signing keys assimetricos (ES256), pois gateway valida apikey separado.
  const userClient = createClient(SUPABASE_URL, ANON_KEY, {
    auth: { persistSession: false },
    global: { headers: { Authorization: `Bearer ${jwt}` } },
  });
  const { data: ud, error: userErr } = await userClient.auth.getUser(jwt);
  if (userErr || !ud?.user) {
    log("warn", "auth_invalid_token", { reason: userErr?.message ?? "no_user" });
    return new Response("invalid token", { status: 401, headers: cors });
  }
  const user_id = ud.user.id;

  if (!rateLimit(user_id)) {
    log("warn", "rate_limited", { user_id });
    return new Response("rate limit exceeded", { status: 429, headers: cors });
  }

  const contentLength = parseInt(req.headers.get("content-length") ?? "0", 10);
  if (contentLength > MAX_BODY_BYTES) {
    log("warn", "payload_too_large", { user_id, bytes: contentLength });
    return new Response("payload too large", { status: 413, headers: cors });
  }

  let body: Record<string, unknown>;
  try {
    const raw = await req.text();
    if (raw.length > MAX_BODY_BYTES) {
      log("warn", "payload_too_large", { user_id, bytes: raw.length });
      return new Response("payload too large", { status: 413, headers: cors });
    }
    body = JSON.parse(raw);
  } catch {
    log("warn", "invalid_json", { user_id });
    return new Response("invalid json", { status: 400, headers: cors });
  }

  // Ping mode: confirma so a autenticacao.
  if (body?.ping === true) {
    log("info", "ping_ok", { user_id, ms: Math.round(performance.now() - t0) });
    return new Response(
      JSON.stringify({ ok: true, user_id, mode: "ping" }),
      { status: 200, headers: { ...cors, "content-type": "application/json" } },
    );
  }

  // Validacao minima dos campos obrigatorios.
  const required = ["account_id", "snapshot_at"];
  for (const k of required) {
    if (body[k] === undefined || body[k] === null) {
      log("warn", "missing_field", { user_id, field: k });
      return new Response(`missing required field: ${k}`, {
        status: 400, headers: cors,
      });
    }
  }

  // snapshot_at: ISO 8601 parseable + janela de skew +/-5min.
  const snapTs = Date.parse(String(body.snapshot_at));
  if (!Number.isFinite(snapTs)) {
    log("warn", "invalid_snapshot_at", { user_id });
    return new Response("invalid snapshot_at (must be ISO 8601)", {
      status: 400, headers: cors,
    });
  }
  if (Math.abs(Date.now() - snapTs) > SNAPSHOT_AT_SKEW_MS) {
    log("warn", "snapshot_at_out_of_window", {
      user_id, skew_ms: Date.now() - snapTs,
    });
    return new Response("snapshot_at out of accepted window", {
      status: 400, headers: cors,
    });
  }

  // account_id: cap de tamanho para evitar abuso.
  const accountIdStr = String(body.account_id);
  if (accountIdStr.length === 0 || accountIdStr.length > 64) {
    log("warn", "invalid_account_id_length", { user_id, len: accountIdStr.length });
    return new Response("invalid account_id length", {
      status: 400, headers: cors,
    });
  }

  // Ranges sanos. position_size <= 10k contratos; PnL absoluto <= 10M USD.
  const positionSize = clampInt(toInt(body.position_size, 0), 0, 10_000);
  const unrealized = clampNum(toNum(body.unrealized_pnl, 0), -1e7, 1e7);
  const realized = clampNum(toNum(body.realized_pnl, 0), -1e7, 1e7);
  const dayPnl = clampNum(toNum(body.day_pnl, 0), -1e7, 1e7);
  const drawdown = clampNum(toNum(body.drawdown, 0), -1e7, 1e7);
  const avgPrice = clampNum(toNum(body.position_avg_price), -1e7, 1e7);

  // INSERT como service_role (bypass RLS) — mas o user_id e' do token validado,
  // nunca do payload do cliente.
  const admin = createClient(SUPABASE_URL, SERVICE_KEY, {
    auth: { persistSession: false },
  });

  const row = {
    user_id,
    account_id: accountIdStr,
    snapshot_at: body.snapshot_at,
    position_size: positionSize,
    position_avg_price: avgPrice,
    position_contract: body.position_contract ? String(body.position_contract).slice(0, 32) : null,
    position_side: normSide(body.position_side),
    unrealized_pnl: unrealized,
    realized_pnl: realized,
    day_pnl: dayPnl,
    drawdown: drawdown,
    raw: body,
  };

  // Idempotente: UNIQUE (user_id, account_id, snapshot_at) — ver
  // PRD/m11_live_snapshots_unique.sql. Retries da extensao (rede instavel,
  // worker re-acordando) nao inflam duplicatas. `ignoreDuplicates` faz o
  // PostgREST traduzir para ON CONFLICT DO NOTHING.
  const { error } = await admin
    .from("live_snapshots")
    .upsert(row, {
      onConflict: "user_id,account_id,snapshot_at",
      ignoreDuplicates: true,
    });
  if (error) {
    log("error", "db_error", { user_id, message: error.message, code: error.code });
    return new Response(`db error: ${error.message}`, { status: 500, headers: cors });
  }

  log("info", "ingest_ok", {
    user_id,
    account_id: accountIdStr,
    contract: row.position_contract,
    size: positionSize,
    day_pnl: dayPnl,
    ms: Math.round(performance.now() - t0),
  });
  return new Response(
    JSON.stringify({ ok: true, user_id }),
    { status: 200, headers: { ...cors, "content-type": "application/json" } },
  );
});

function toNum(v: unknown, fallback: number | null = null): number | null {
  if (v === null || v === undefined || v === "") return fallback;
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : fallback;
}
function toInt(v: unknown, fallback: number): number {
  const n = toNum(v, fallback);
  return n === null ? fallback : Math.trunc(n);
}
function normSide(v: unknown): string | null {
  if (!v) return null;
  const s = String(v).toLowerCase();
  if (s.includes("long")) return "Long";
  if (s.includes("short")) return "Short";
  return null;
}
function clampNum(v: number | null, lo: number, hi: number): number | null {
  if (v === null) return null;
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}
function clampInt(v: number, lo: number, hi: number): number {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}
