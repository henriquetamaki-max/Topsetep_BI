// BI TopStep — Live Monitor — Popup
//
// Funcoes:
// - Salvar JWT em chrome.storage.local.
// - Mostrar email do usuario decodificado do JWT payload.
// - Mostrar status do scrape (ok/broken).
// - Mostrar resultado do ultimo POST live-ingest.
// - Botao "Test connection" envia PING para background.js.

const $ = (id) => document.getElementById(id);

function decodeJwtPayload(jwt) {
  try {
    const parts = jwt.split(".");
    if (parts.length !== 3) return null;
    const json = atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json);
  } catch (_e) {
    return null;
  }
}

async function loadState() {
  const { jwt, scrape_status, last_status } = await chrome.storage.local.get([
    "jwt", "scrape_status", "last_status",
  ]);

  if (jwt) {
    $("jwt").value = jwt;
    const payload = decodeJwtPayload(jwt);
    const email = payload?.email || payload?.sub || "(token loaded)";
    const expIso = payload?.exp ? new Date(payload.exp * 1000).toISOString() : "?";
    $("userline").textContent = `Signed in as ${email} · expires ${expIso}`;
  } else {
    $("userline").textContent = "No token saved.";
  }

  const cfg = globalThis.BI_TOPSTEP_CONFIG;
  const cfgOk = cfg && !cfg.SUPABASE_URL.includes("YOUR-PROJECT");
  $("config-status").textContent = cfgOk
    ? `Config: ${cfg.SUPABASE_URL}`
    : "Config: NOT SET — edit extension/config.js";
  $("config-status").className = cfgOk ? "small ok" : "small err";
  $("version").textContent = "v" + (cfg?.VERSION || "?");

  if (scrape_status === "broken") {
    $("scrape-status").textContent = "Scrape: selectors out of date — update extension/selectors.json";
    $("scrape-status").className = "small err";
  } else if (scrape_status === "ok") {
    $("scrape-status").textContent = "Scrape: OK";
    $("scrape-status").className = "small ok";
  } else {
    $("scrape-status").textContent = "Scrape: waiting for topstepx.com/trade";
    $("scrape-status").className = "small muted";
  }

  if (last_status) {
    const when = new Date(last_status.at).toLocaleTimeString();
    if (last_status.ok) {
      $("last-status").textContent = `Last send: ${when} · 2XX (${last_status.source})`;
      $("last-status").className = "small ok";
    } else {
      $("last-status").textContent = `Last send: ${when} · ${last_status.code || "ERR"} (${last_status.error || last_status.text || ""})`;
      $("last-status").className = "small err";
    }
  } else {
    $("last-status").textContent = "Last send: never";
    $("last-status").className = "small muted";
  }
}

$("save").addEventListener("click", async () => {
  const jwt = $("jwt").value.trim();
  if (!jwt) return;
  await chrome.storage.local.set({ jwt });
  await loadState();
});

$("ping").addEventListener("click", async () => {
  $("last-status").textContent = "Pinging…";
  chrome.runtime.sendMessage({ type: "PING" }, (resp) => {
    if (chrome.runtime.lastError) {
      $("last-status").textContent = "Ping error: " + chrome.runtime.lastError.message;
      $("last-status").className = "small err";
      return;
    }
    loadState();
  });
});

loadState();
