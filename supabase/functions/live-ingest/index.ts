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

const ALLOWED_ORIGINS = [
  "chrome-extension://",  // qualquer ext local (load unpacked)
];

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
  const origin = req.headers.get("origin");
  const cors = corsHeaders(origin);

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: cors });
  }
  if (req.method !== "POST") {
    return new Response("method not allowed", { status: 405, headers: cors });
  }

  const authHeader = req.headers.get("authorization") ?? "";
  if (!authHeader.startsWith("Bearer ")) {
    return new Response("missing bearer token", { status: 401, headers: cors });
  }
  const jwt = authHeader.slice(7).trim();

  // Cliente leve so para validar o JWT.
  const userClient = createClient(SUPABASE_URL, jwt, {
    auth: { persistSession: false },
  });
  const { data: ud, error: userErr } = await userClient.auth.getUser(jwt);
  if (userErr || !ud?.user) {
    return new Response("invalid token", { status: 401, headers: cors });
  }
  const user_id = ud.user.id;

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return new Response("invalid json", { status: 400, headers: cors });
  }

  // Ping mode: confirma so a autenticacao.
  if (body?.ping === true) {
    return new Response(
      JSON.stringify({ ok: true, user_id, mode: "ping" }),
      { status: 200, headers: { ...cors, "content-type": "application/json" } },
    );
  }

  // Validacao minima dos campos obrigatorios.
  const required = ["account_id", "snapshot_at"];
  for (const k of required) {
    if (body[k] === undefined || body[k] === null) {
      return new Response(`missing required field: ${k}`, {
        status: 400, headers: cors,
      });
    }
  }

  // INSERT como service_role (bypass RLS) — mas o user_id e' do token validado,
  // nunca do payload do cliente.
  const admin = createClient(SUPABASE_URL, SERVICE_KEY, {
    auth: { persistSession: false },
  });

  const row = {
    user_id,
    account_id: String(body.account_id),
    snapshot_at: body.snapshot_at,
    position_size: toInt(body.position_size, 0),
    position_avg_price: toNum(body.position_avg_price),
    position_contract: body.position_contract ? String(body.position_contract) : null,
    position_side: normSide(body.position_side),
    unrealized_pnl: toNum(body.unrealized_pnl, 0),
    realized_pnl: toNum(body.realized_pnl, 0),
    day_pnl: toNum(body.day_pnl, 0),
    drawdown: toNum(body.drawdown, 0),
    raw: body,
  };

  const { error } = await admin.from("live_snapshots").insert(row);
  if (error) {
    console.error("[live-ingest] db error", error);
    return new Response(`db error: ${error.message}`, { status: 500, headers: cors });
  }

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
