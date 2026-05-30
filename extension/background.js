// X-Metrics — Live Monitor — Service Worker
//
// Responsabilidade: enviar snapshots ao Supabase Edge Function `live-ingest`.
// - Heartbeat de 30s (chrome.alarms) com last-known snapshot do content script.
// - Push imediato quando content.js manda SNAPSHOT_CHANGED (mudanca relevante).
// - JWT do usuario fica em chrome.storage.local (configurado via popup).
//
// Configuracao de URLs: extension/config.js (importado via importScripts).
// chrome.storage.local guarda: { jwt, last_snapshot, last_status, scrape_status }

importScripts("config.js");

const HEARTBEAT_ALARM = "live-ingest-heartbeat";
const HEARTBEAT_PERIOD_MIN = 30 / 60; // 30 segundos

let lastSnapshot = null;
let lastEnqueueAt = 0;
const MIN_GAP_MS = 5000; // debounce: no maximo 1 envio a cada 5s

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(HEARTBEAT_ALARM, { periodInMinutes: HEARTBEAT_PERIOD_MIN });
  console.log("[X-Metrics] background installed; heartbeat alarm created");
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(HEARTBEAT_ALARM, { periodInMinutes: HEARTBEAT_PERIOD_MIN });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === HEARTBEAT_ALARM && lastSnapshot) {
    sendSnapshot(lastSnapshot, "heartbeat").catch((e) =>
      console.warn("[X-Metrics] heartbeat send error:", e)
    );
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // So aceita mensagens da propria extensao (content script em topstepx.com /
  // popup). Sem externally_connectable no manifest, paginas web ja nao alcancam
  // o background; este check e defesa-em-profundidade contra msg forjada.
  if (sender?.id !== chrome.runtime.id) {
    return; // ignora origem externa
  }
  (async () => {
    if (msg?.type === "SNAPSHOT_CHANGED" && msg.payload) {
      lastSnapshot = msg.payload;
      const now = Date.now();
      if (now - lastEnqueueAt < MIN_GAP_MS) {
        sendResponse({ ok: true, debounced: true });
        return;
      }
      lastEnqueueAt = now;
      const r = await sendSnapshot(msg.payload, "event");
      sendResponse(r);
      return;
    }
    if (msg?.type === "PING") {
      const r = await sendSnapshot({ ping: true }, "ping");
      sendResponse(r);
      return;
    }
    if (msg?.type === "SCRAPE_STATUS" && msg.status) {
      chrome.storage.local.set({ scrape_status: msg.status });
      sendResponse({ ok: true });
      return;
    }
    sendResponse({ ok: false, error: "unknown_message" });
  })();
  // sendResponse async — must return true.
  return true;
});

// Refresh do access_token via Supabase Auth REST.
//
// Quando a extensao foi configurada via bundle JSON (v0.2.0+), o storage tem
// `refresh_token` + `expires_at`. Esta funcao roda antes de cada sendSnapshot:
// se o access_token expira em < REFRESH_THRESHOLD_SEC, troca o refresh_token
// por par novo (rotation default do Supabase) e atualiza o storage.
//
// Modo legado (so' `jwt`, sem refresh_token): nao faz nada — extensao usa o
// JWT ate 401 chegar e trader recolar manualmente.
const REFRESH_THRESHOLD_SEC = 300; // 5 min antes da expiracao

async function maybeRefreshToken(cfg) {
  const { jwt, refresh_token, expires_at } = await chrome.storage.local.get([
    "jwt", "refresh_token", "expires_at",
  ]);
  if (!jwt) return { jwt: null };
  // Sem refresh_token ou sem expires_at = modo legado, segue com o jwt atual.
  if (!refresh_token || !Number.isFinite(expires_at)) {
    return { jwt };
  }
  const nowSec = Math.floor(Date.now() / 1000);
  // Ainda longe da expiracao? Devolve jwt atual sem chamar o servidor.
  if (expires_at - nowSec > REFRESH_THRESHOLD_SEC) {
    return { jwt };
  }
  // Trocar refresh_token por novo par. POST /auth/v1/token?grant_type=refresh_token
  // retorna { access_token, refresh_token (rotacionado), expires_in, ... }.
  try {
    const url = `${cfg.SUPABASE_URL}/auth/v1/token?grant_type=refresh_token`;
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        apikey: cfg.SUPABASE_ANON_KEY,
      },
      body: JSON.stringify({ refresh_token }),
    });
    if (!res.ok) {
      // refresh_token revogado (logout no app), expirado (60 dias sem uso)
      // ou plano cancelado. Sinaliza no last_status p/ popup mostrar e
      // pede repaste do bundle.
      const text = await res.text().catch(() => "");
      chrome.storage.local.set({
        last_status: {
          ok: false,
          code: res.status,
          at: new Date().toISOString(),
          source: "refresh",
          error: "refresh_failed",
          text: text.slice(0, 200),
        },
      });
      // Devolve jwt antigo mesmo assim — server vai responder 401 e UI mostra.
      return { jwt };
    }
    const data = await res.json();
    if (!data?.access_token) return { jwt };
    const newJwt = data.access_token;
    const newRefresh = data.refresh_token || refresh_token;
    const newExpiresAt = Math.floor(Date.now() / 1000)
      + (Number.isFinite(data.expires_in) ? data.expires_in : 3600);
    await chrome.storage.local.set({
      jwt: newJwt,
      refresh_token: newRefresh,
      expires_at: newExpiresAt,
    });
    return { jwt: newJwt };
  } catch (e) {
    // Rede instavel: usa jwt atual. Se ele tambem ja expirou, sendSnapshot
    // vai pegar 401 e o trader vera no popup.
    // Loga so' o tipo/mensagem do erro — nunca o objeto resposta, que pode
    // conter o token na mensagem de 401 do gotrue.
    console.warn("[X-Metrics] refresh error:", e?.name || "error");
    return { jwt };
  }
}

async function sendSnapshot(payload, source) {
  const cfg = globalThis.BI_TOPSTEP_CONFIG;
  if (!cfg || !cfg.SUPABASE_URL || !cfg.LIVE_INGEST_URL) {
    return { ok: false, error: "config_missing" };
  }
  const { jwt } = await maybeRefreshToken(cfg);
  if (!jwt) return { ok: false, error: "no_jwt" };

  const body = payload?.ping ? { ping: true } : enrichPayload(payload);

  try {
    const res = await fetch(cfg.LIVE_INGEST_URL, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${jwt}`,
        apikey: cfg.SUPABASE_ANON_KEY,
      },
      body: JSON.stringify(body),
    });
    const status = { ok: res.ok, code: res.status, at: new Date().toISOString(), source };
    if (!res.ok) {
      status.text = await res.text().catch(() => "");
    }
    chrome.storage.local.set({ last_status: status });
    return status;
  } catch (e) {
    const status = {
      ok: false,
      code: 0,
      error: String(e?.message || e),
      at: new Date().toISOString(),
      source,
    };
    chrome.storage.local.set({ last_status: status });
    return status;
  }
}

function enrichPayload(p) {
  const out = {
    account_id: p.account_id || "unknown",
    snapshot_at: p.snapshot_at || new Date().toISOString(),
    position_size: Number.isFinite(p.position_size) ? p.position_size : 0,
    position_avg_price: p.position_avg_price ?? null,
    position_contract: p.position_contract ?? null,
    position_side: p.position_side ?? null,
    unrealized_pnl: Number.isFinite(p.unrealized_pnl) ? p.unrealized_pnl : 0,
    realized_pnl: Number.isFinite(p.realized_pnl) ? p.realized_pnl : 0,
    day_pnl: Number.isFinite(p.day_pnl) ? p.day_pnl : 0,
    drawdown: Number.isFinite(p.drawdown) ? p.drawdown : 0,
    raw: p.raw || null,
  };
  return out;
}

console.log("[X-Metrics] background.js ready (v" +
  (globalThis.BI_TOPSTEP_CONFIG?.VERSION || "?") + ")");
