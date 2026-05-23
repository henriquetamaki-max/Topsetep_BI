// BI TopStep — Live Monitor — Popup
//
// Funcoes:
// - Salvar JWT em chrome.storage.local.
// - Mostrar email do usuario decodificado do JWT payload.
// - Mostrar status do scrape (ok/broken).
// - Mostrar resultado do ultimo POST live-ingest.
// - Botao "Test connection" envia PING para background.js.
//
// i18n: usa chrome.i18n.getMessage(name, [args]) e attribuitos
// data-i18n / data-i18n-placeholder no popup.html.

const $ = (id) => document.getElementById(id);
const M = (k, args) => chrome.i18n.getMessage(k, args) || k;

// Validacao estrutural do JWT antes de exibir claims no popup.
//
// Limites do que da' pra fazer aqui: sem a chave HMAC do Supabase, nao
// conseguimos verificar a assinatura — isso e' funcao do servidor (Edge
// Function valida via supabase.auth.getUser, ver supabase/functions/
// live-ingest/index.ts). O popup nao confia no token para autorizar nada;
// so' o exibe. Mesmo assim, restringir o que e' aceito como "JWT" reduz a
// chance de:
//   - texto colado errado virar "Signed in as undefined" silencioso,
//   - JWT expirado ser exibido como valido,
//   - emoji/script no claim "email" aparecer na UI.
//
// Estrategia: parsing rigoroso (3 partes base64url + header HS256 + payload
// objeto JSON), claims minimas obrigatorias (sub uuid, exp/iat numericos,
// aud=authenticated) e validacao de `exp` > now. Retorna o payload ou um
// codigo de erro pra UI mostrar mensagem util.

function b64urlDecode(seg) {
  const padded = seg.replace(/-/g, "+").replace(/_/g, "/")
    + "=".repeat((4 - seg.length % 4) % 4);
  return atob(padded);
}

function isUuid(s) {
  return typeof s === "string"
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);
}

function isFiniteNum(v) {
  return typeof v === "number" && Number.isFinite(v);
}

// Retorna { ok: true, payload } ou { ok: false, code: "<motivo>" }.
function validateJwt(jwt) {
  if (typeof jwt !== "string" || jwt.length < 20 || jwt.length > 4096) {
    return { ok: false, code: "shape" };
  }
  const parts = jwt.split(".");
  if (parts.length !== 3) return { ok: false, code: "shape" };
  let header, payload;
  try {
    header = JSON.parse(b64urlDecode(parts[0]));
    payload = JSON.parse(b64urlDecode(parts[1]));
  } catch (_e) {
    return { ok: false, code: "decode" };
  }
  if (!header || typeof header !== "object") return { ok: false, code: "header" };
  if (header.typ && header.typ !== "JWT") return { ok: false, code: "typ" };
  // Supabase default e HS256; HS384/HS512 nao sao usados, mas aceitamos
  // qualquer HS* pra nao quebrar se o projeto rotacionar algoritmo.
  if (typeof header.alg !== "string" || !/^HS\d+$/.test(header.alg)) {
    return { ok: false, code: "alg" };
  }
  if (!payload || typeof payload !== "object") return { ok: false, code: "payload" };
  if (!isUuid(payload.sub)) return { ok: false, code: "sub" };
  if (!isFiniteNum(payload.exp) || !isFiniteNum(payload.iat)) {
    return { ok: false, code: "claims" };
  }
  if (payload.aud !== "authenticated") return { ok: false, code: "aud" };
  const nowSec = Math.floor(Date.now() / 1000);
  if (payload.exp <= nowSec) return { ok: false, code: "expired" };
  return { ok: true, payload };
}

// Compat: retorna o payload se valido, senao null. UI usa validateJwt
// diretamente quando precisa diferenciar entre tipos de erro.
function decodeJwtPayload(jwt) {
  const r = validateJwt(jwt);
  return r.ok ? r.payload : null;
}

function applyStaticI18n() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    el.textContent = M(key);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    el.setAttribute("placeholder", M(key));
  });
  document.title = M("popupTitle");
}

async function loadState() {
  const { jwt, scrape_status, last_status } = await chrome.storage.local.get([
    "jwt", "scrape_status", "last_status",
  ]);

  if (jwt) {
    $("jwt").value = jwt;
    const r = validateJwt(jwt);
    if (r.ok) {
      const p = r.payload;
      // email pode ter caracteres especiais; textContent ja' escapa, mas
      // capamos tamanho pra evitar UI estourada.
      const email = String(p.email || p.sub || "(token)").slice(0, 120);
      const expIso = new Date(p.exp * 1000).toISOString();
      $("userline").textContent = M("popupSignedIn", [email, expIso]);
      $("userline").className = "muted small";
    } else {
      const key = r.code === "expired" ? "popupTokenExpired" : "popupTokenInvalid";
      $("userline").textContent = M(key);
      $("userline").className = "small err";
    }
  } else {
    $("userline").textContent = M("popupNoToken");
    $("userline").className = "muted small";
  }

  const cfg = globalThis.BI_TOPSTEP_CONFIG;
  const cfgOk = cfg && !cfg.SUPABASE_URL.includes("YOUR-PROJECT");
  $("config-status").textContent = cfgOk
    ? M("popupConfigOk", [cfg.SUPABASE_URL])
    : M("popupConfigMissing");
  $("config-status").className = cfgOk ? "small ok" : "small err";
  $("version").textContent = "v" + (cfg?.VERSION || "?");

  if (scrape_status === "broken") {
    $("scrape-status").textContent = M("popupScrapeBroken");
    $("scrape-status").className = "small err";
  } else if (scrape_status === "ok") {
    $("scrape-status").textContent = M("popupScrapeOk");
    $("scrape-status").className = "small ok";
  } else {
    $("scrape-status").textContent = M("popupScrapeWaiting");
    $("scrape-status").className = "small muted";
  }

  if (last_status) {
    const when = new Date(last_status.at).toLocaleTimeString();
    if (last_status.ok) {
      $("last-status").textContent = M("popupLastSendOk", [when, last_status.source || ""]);
      $("last-status").className = "small ok";
    } else {
      $("last-status").textContent = M("popupLastSendErr", [
        when,
        String(last_status.code || "ERR"),
        last_status.error || last_status.text || "",
      ]);
      $("last-status").className = "small err";
    }
  } else {
    $("last-status").textContent = M("popupLastSendNever");
    $("last-status").className = "small muted";
  }
}

$("save").addEventListener("click", async () => {
  const jwt = $("jwt").value.trim();
  if (!jwt) return;
  // Pre-flight: nao deixa o usuario salvar lixo (cola errada, JWT expirado,
  // chave de outro projeto). O servidor ainda valida na hora de usar — mas
  // bloquear aqui dá feedback imediato no popup.
  const r = validateJwt(jwt);
  if (!r.ok) {
    const key = r.code === "expired" ? "popupTokenExpired" : "popupTokenInvalid";
    $("userline").textContent = M(key);
    $("userline").className = "small err";
    return;
  }
  await chrome.storage.local.set({ jwt });
  await loadState();
});

$("ping").addEventListener("click", () => {
  $("last-status").textContent = M("popupPingPending");
  chrome.runtime.sendMessage({ type: "PING" }, (_resp) => {
    if (chrome.runtime.lastError) {
      $("last-status").textContent = M("popupPingError", [chrome.runtime.lastError.message]);
      $("last-status").className = "small err";
      return;
    }
    loadState();
  });
});

applyStaticI18n();
loadState();
