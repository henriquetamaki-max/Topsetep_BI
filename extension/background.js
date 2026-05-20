// BI TopStep — Live Monitor — Service Worker
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
  console.log("[BI TopStep] background installed; heartbeat alarm created");
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(HEARTBEAT_ALARM, { periodInMinutes: HEARTBEAT_PERIOD_MIN });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === HEARTBEAT_ALARM && lastSnapshot) {
    sendSnapshot(lastSnapshot, "heartbeat").catch((e) =>
      console.warn("[BI TopStep] heartbeat send error:", e)
    );
  }
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
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

async function sendSnapshot(payload, source) {
  const cfg = globalThis.BI_TOPSTEP_CONFIG;
  if (!cfg || !cfg.SUPABASE_URL || !cfg.LIVE_INGEST_URL) {
    return { ok: false, error: "config_missing" };
  }
  const { jwt } = await chrome.storage.local.get(["jwt"]);
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

console.log("[BI TopStep] background.js ready (v" +
  (globalThis.BI_TOPSTEP_CONFIG?.VERSION || "?") + ")");
