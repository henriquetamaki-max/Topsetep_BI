// BI TopStep — Live Monitor — Content Script
//
// Roda em topstepx.com/*. Scrape periodico (5s) de posicao + PnL,
// envia para background.js via runtime.sendMessage quando mudar.
//
// Selectors em selectors.json (web_accessible_resource). Fallback regex para
// quando os data-testid n.o existem.
//
// Resilencia: marca scrape_status = 'broken' apos 3 ciclos sem leitura
// de campo critico (position_size / day_pnl). Popup le esse status.

(() => {
  if (window.__bitopstep_live_injected) return;
  window.__bitopstep_live_injected = true;

  const POLL_MS = 5000;
  const TRADE_URL_REGEX = /\/trade(\b|\/|$)/;
  const FAIL_THRESHOLD = 3;

  let selectorsCfg = null;
  let lastSent = null;
  let failCount = 0;

  async function loadSelectors() {
    try {
      const url = chrome.runtime.getURL("selectors.json");
      const res = await fetch(url);
      if (!res.ok) return null;
      return await res.json();
    } catch (_e) {
      return null;
    }
  }

  function querySelectors(candidates) {
    if (!Array.isArray(candidates)) return null;
    for (const sel of candidates) {
      try {
        const el = document.querySelector(sel);
        if (el && el.textContent && el.textContent.trim()) return el;
      } catch (_e) {
        // selector invalido — ignora
      }
    }
    return null;
  }

  function parseNumber(text) {
    if (!text) return null;
    const m = String(text).replace(/[,$\s]/g, "").match(/-?\d+(?:\.\d+)?/);
    return m ? parseFloat(m[0]) : null;
  }

  function parseInteger(text) {
    if (!text) return null;
    const m = String(text).replace(/[,\s]/g, "").match(/-?\d+/);
    return m ? parseInt(m[0], 10) : null;
  }

  function regexFallback(key) {
    const re = selectorsCfg?.fallback_regex?.[key];
    if (!re) return null;
    try {
      const m = document.body.innerText.match(new RegExp(re));
      return m ? m[1] : null;
    } catch (_e) {
      return null;
    }
  }

  function readField(key, parser) {
    const sels = selectorsCfg?.selectors?.[key];
    const el = querySelectors(sels);
    if (el) return parser(el.textContent);
    const fb = regexFallback(key);
    return fb !== null ? parser(fb) : null;
  }

  function scrapeSnapshot() {
    const account_id = readField("account_id", (t) => String(t).trim()) || "default";
    const position_size = readField("position_size", parseInteger) ?? 0;
    const position_avg_price = readField("position_avg_price", parseNumber);
    const position_contract = readField("position_contract", (t) => String(t).trim());
    const sideRaw = readField("position_side", (t) => String(t).trim());
    const position_side = sideRaw && /short/i.test(sideRaw) ? "Short"
      : sideRaw && /long/i.test(sideRaw) ? "Long"
      : null;
    const unrealized_pnl = readField("unrealized_pnl", parseNumber) ?? 0;
    const realized_pnl = readField("realized_pnl", parseNumber) ?? 0;
    const day_pnl = readField("day_pnl", parseNumber) ?? 0;
    const drawdown = readField("drawdown", parseNumber) ?? 0;

    return {
      account_id,
      snapshot_at: new Date().toISOString(),
      position_size,
      position_avg_price,
      position_contract,
      position_side,
      unrealized_pnl,
      realized_pnl,
      day_pnl,
      drawdown,
      raw: { source: "topstepx.com", path: location.pathname },
    };
  }

  function isMeaningfulChange(curr, prev) {
    if (!prev) return true;
    const keys = ["position_size", "position_avg_price", "position_contract",
                  "position_side", "unrealized_pnl", "realized_pnl",
                  "day_pnl", "drawdown"];
    return keys.some((k) => curr[k] !== prev[k]);
  }

  function isCriticalEmpty(s) {
    // Marca como falha se TODOS os campos criticos sao 0/null — mais provavel
    // que selectors quebraram do que o trader nao ter posicao.
    return s.position_size === 0 && s.day_pnl === 0 && s.realized_pnl === 0
      && s.unrealized_pnl === 0 && !s.position_contract;
  }

  function loopOnce() {
    if (!TRADE_URL_REGEX.test(location.pathname)) return;
    const snap = scrapeSnapshot();
    if (isCriticalEmpty(snap)) {
      failCount += 1;
      if (failCount >= FAIL_THRESHOLD) {
        chrome.runtime.sendMessage({ type: "SCRAPE_STATUS", status: "broken" });
      }
    } else {
      failCount = 0;
      chrome.runtime.sendMessage({ type: "SCRAPE_STATUS", status: "ok" });
    }
    if (isMeaningfulChange(snap, lastSent)) {
      lastSent = snap;
      chrome.runtime.sendMessage({ type: "SNAPSHOT_CHANGED", payload: snap });
    }
  }

  async function init() {
    selectorsCfg = await loadSelectors();
    console.log("[BI TopStep] content.js v" +
      (selectorsCfg?.version || "?") + " ready (path: " + location.pathname + ")");
    setInterval(loopOnce, POLL_MS);
    // primeira chamada imediata
    setTimeout(loopOnce, 1500);
  }

  init();
})();
