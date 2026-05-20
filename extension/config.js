// BI TopStep — Live Monitor extension config
//
// Editar SUPABASE_URL e SUPABASE_ANON_KEY com os valores publicos do seu
// projeto Supabase (Settings → API).
//
// LIVE_INGEST_URL e derivada — nao precisa editar manualmente.
//
// Em distribuicao via .zip (T2.7), o BI TopStep gera este arquivo com os
// valores corretos ja preenchidos antes de empacotar.

const BI_TOPSTEP_CONFIG = {
  SUPABASE_URL: "https://YOUR-PROJECT.supabase.co",
  SUPABASE_ANON_KEY: "YOUR-ANON-KEY",
  VERSION: "0.1.0",
};

BI_TOPSTEP_CONFIG.LIVE_INGEST_URL =
  BI_TOPSTEP_CONFIG.SUPABASE_URL.replace(/\/+$/, "") + "/functions/v1/live-ingest";

// Disponibilizar como global tanto em service worker (self.X) quanto em
// content scripts (window.X). globalThis cobre os dois.
globalThis.BI_TOPSTEP_CONFIG = BI_TOPSTEP_CONFIG;
