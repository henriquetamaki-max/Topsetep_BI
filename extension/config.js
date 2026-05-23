// BI TopStep — Live Monitor extension config
//
// Editar SUPABASE_URL e SUPABASE_ANON_KEY com os valores publicos do seu
// projeto Supabase (Settings → API).
//
// LIVE_INGEST_URL e derivada — nao precisa editar manualmente.
//
// Em distribuicao via .zip (T2.7), o BI TopStep gera este arquivo com os
// valores corretos ja preenchidos antes de empacotar.
//
// ⚠️ NUNCA cole aqui a SERVICE_ROLE_KEY do Supabase. Apenas a ANON_KEY (a
// chave publica em Settings → API). A service_role bypassa RLS e abriria
// vazamento total de dados se distribuida com a extensao. Se nao tem
// certeza qual chave e' qual: a ANON_KEY costuma ter "anon" no JWT
// payload (decodificavel em jwt.io); a service_role tem "service_role".

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
