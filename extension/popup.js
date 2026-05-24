// X-Metrics — Live Monitor — Popup
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
  // Limite generoso: JWTs assimetricos (RS256/PS256) podem passar de 2KB com
  // claims customizadas e header de chave. 8192 cobre folgadamente.
  if (typeof jwt !== "string" || jwt.length < 20 || jwt.length > 8192) {
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
  // Nao checar header.typ: RFC 9068 introduziu "at+jwt" para access tokens e
  // Supabase pode usar variacoes ao longo da migracao para chaves assimetricas.
  // Servidor valida a assinatura — typ aqui e' so' metadata.
  //
  // Supabase legado usava HS256 (HMAC simetrico). A partir de 2026-Q2 a
  // plataforma migra projetos para chaves assimetricas (ES256 default,
  // RS256/PS256 opcionais; EdDSA para projetos novos). Aceitamos todos
  // os algoritmos modernos comuns. Rejeitamos so' "none" (classic JWT
  // attack) e algoritmos desconhecidos. A validacao real e' no servidor
  // — aqui so' filtramos lixo obvio antes de salvar no chrome.storage.
  if (typeof header.alg !== "string"
      || !/^(HS|RS|ES|PS)(256|384|512)$|^EdDSA$/.test(header.alg)) {
    return { ok: false, code: "alg" };
  }
  if (!payload || typeof payload !== "object") return { ok: false, code: "payload" };
  if (!isUuid(payload.sub)) return { ok: false, code: "sub" };
  // `iat` e' opcional (nem todo emissor inclui). `exp` e' obrigatorio para
  // checagem de expiracao.
  if (!isFiniteNum(payload.exp)) return { ok: false, code: "claims" };
  if ("iat" in payload && !isFiniteNum(payload.iat)) return { ok: false, code: "claims" };
  // `aud` pode ser string ("authenticated") ou array (["authenticated", ...])
  // conforme RFC 7519. Supabase historicamente usa string, mas tokens emitidos
  // via OAuth federation as vezes vem como array.
  const aud = payload.aud;
  const audOk = aud === "authenticated"
    || (Array.isArray(aud) && aud.includes("authenticated"));
  if (!audOk) return { ok: false, code: "aud" };
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
      // Sufixo com o codigo da falha facilita diagnostico ("aud", "alg",
      // "sub", "shape", "decode", ...). Sem isso o usuario ve so' "Token
      // invalido" e nao da pra debugar.
      const suffix = r.code && r.code !== "expired" ? ` [${r.code}]` : "";
      $("userline").textContent = M(key) + suffix;
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

// Aceita 2 formatos no campo de input:
//   - Bundle JSON `{"access_token","refresh_token","expires_at"}` — modo v0.2.0+
//     com refresh automatico no background.js (trader cola 1x, vive ate cancelar).
//   - JWT cru (string base64url.base64url.base64url) — modo legado v0.1.x
//     (trader recola toda 1h ate atualizar a extensao).
// Detecta o formato testando JSON parse + presenca de access_token.
function parseInput(raw) {
  const s = raw.trim();
  if (!s) return null;
  if (s.startsWith("{")) {
    try {
      const obj = JSON.parse(s);
      if (obj && typeof obj.access_token === "string") {
        return {
          mode: "bundle",
          access_token: obj.access_token,
          refresh_token: typeof obj.refresh_token === "string" ? obj.refresh_token : "",
          expires_at: Number.isFinite(obj.expires_at) ? obj.expires_at : null,
        };
      }
    } catch (_e) { /* cai no fallback abaixo */ }
  }
  // Fallback: trata como JWT cru.
  return { mode: "legacy", access_token: s, refresh_token: "", expires_at: null };
}

$("save").addEventListener("click", async () => {
  const parsed = parseInput($("jwt").value);
  if (!parsed) return;
  // Pre-flight: nao deixa o usuario salvar lixo (cola errada, JWT expirado,
  // chave de outro projeto). O servidor ainda valida na hora de usar — mas
  // bloquear aqui dá feedback imediato no popup.
  const r = validateJwt(parsed.access_token);
  if (!r.ok) {
    const key = r.code === "expired" ? "popupTokenExpired" : "popupTokenInvalid";
    const suffix = r.code && r.code !== "expired" ? ` [${r.code}]` : "";
    $("userline").textContent = M(key) + suffix;
    $("userline").className = "small err";
    return;
  }
  // Para o background.js, `jwt` continua sendo a chave do access_token (compat
  // com versoes anteriores). refresh_token e expires_at sao opcionais: se vazios,
  // o background.js fica em modo legado (sem refresh, trader recola quando 401).
  await chrome.storage.local.set({
    jwt: parsed.access_token,
    refresh_token: parsed.refresh_token,
    expires_at: parsed.expires_at,
  });
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
