-- BI TopStep — M13 — Release da extensao v0.2.0 (refresh automatico do JWT)
-- Rodar 1x no Supabase SQL Editor (como `postgres`/service_role), apos
-- `m12_app_releases.sql` (a tabela `app_releases` ja' precisa existir).
--
-- Promove v0.2.0 como latest:
-- 1. Marca v0.1.0 como is_latest=false (libera o partial unique index
--    `app_releases_one_latest_per_component`).
-- 2. Insere v0.2.0 com is_latest=true.
--
-- Tudo em transacao para garantir que nenhum select intermediario veja zero
-- ou duas linhas latest.
--
-- IDEMPOTENTE: rodar 2x e' seguro (on conflict do nothing no INSERT, UPDATE
-- nao re-marca o que ja esta false).

set client_min_messages = warning;

begin;

-- Tira o is_latest do v0.1.0 (e qualquer outra linha 'extension' por garantia).
update public.app_releases
   set is_latest = false
 where component = 'extension'
   and is_latest = true
   and version <> '0.2.0';

-- Insere v0.2.0 como nova latest. download_url reusa o caminho fixo no Storage
-- (extension-latest.zip e' sobrescrito a cada release). Em releases futuras
-- considerar usar caminho versionado para permitir downgrade.
insert into public.app_releases
    (component, version, release_notes, download_url, is_latest)
values (
    'extension',
    '0.2.0',
    $$### v0.2.0 — Refresh automatico do JWT (2026-05-24)

**Principal mudanca:** trader cola o "bundle JWT" UMA VEZ ao instalar e a extensao mantem
o access_token vivo sozinha enquanto o plano estiver ativo. Adeus ao re-paste de hora em hora.

**Como atualizar:**
1. Clique em Download para baixar o `.zip` e descompacte numa pasta.
2. Em `chrome://extensions/` (modo desenvolvedor ligado), clique em "Recarregar"
   na extensao "BI TopStep — Live Monitor" — ou "Carregar sem compactar" apontando
   pra pasta nova se for a primeira vez.
3. Abra o popup e cole o **bundle JSON** que aparece na aba **Account → Extensao Chrome**
   (copie o bloco inteiro, formato `{"access_token":...,"refresh_token":...,"expires_at":...}`).
4. Pronto. Enquanto o plano estiver em dia, nada mais a fazer.

**Compatibilidade reversa:** colando so' o JWT cru (formato v0.1.x), continua funcionando
em modo legado — renovacao manual quando aparecer 401.

**Atencao:** se voce usar a extensao em 2 maquinas (PC trabalho + casa), cole o bundle
em cada uma separadamente. A rotacao do refresh_token invalida copias em outras maquinas.$$,
    'https://qjzouhdrhoxmtinidsqv.supabase.co/storage/v1/object/public/extension/extension-latest.zip',
    true
)
on conflict (component, version) do update
    set is_latest    = excluded.is_latest,
        release_notes = excluded.release_notes,
        download_url = excluded.download_url;

commit;

-- Verificacao (descomentar para conferir manualmente):
-- select component, version, is_latest, released_at
--   from public.app_releases
--  where component = 'extension'
--  order by released_at desc;
