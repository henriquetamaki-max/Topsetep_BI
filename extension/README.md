# X-Metrics — Live Monitor (Chrome MV3)

Extensão que captura posição e PnL em `topstepx.com/trade` e envia para o X-Metrics via Supabase Edge Function (`live-ingest`).

## Instalação (load unpacked)

1. Descompacte o `.zip` numa pasta que você vai manter (a extensão é carregada desse caminho).
2. Abra `chrome://extensions` e ative o **Modo Desenvolvedor** (toggle no canto superior direito).
3. Clique em **"Carregar sem compactação"** e selecione a pasta descompactada.
4. Clique no ícone da extensão na barra de ferramentas → cole o **JWT** que você copiou em **X-Metrics → Account → Extensão Live Monitor**.
5. Clique em **Save token** e depois em **Test connection**. Resposta verde = pronto.
6. Abra https://topstepx.com/trade. A posição deve aparecer no painel **Live** do X-Metrics em poucos segundos.

## Arquivos

| Arquivo | Função |
|---|---|
| `manifest.json` | Manifest MV3 (permissions, content scripts, background worker). |
| `config.js` | URLs públicas do Supabase (SUPABASE_URL, ANON_KEY). Pré-preenchido pelo `package_extension.py` durante o build do `.zip`. |
| `background.js` | Service worker. Heartbeat 30s + push imediato por evento. Envia para `/functions/v1/live-ingest`. |
| `content.js` | Scrapa o DOM de `topstepx.com/trade` (a cada 5s) e manda `SNAPSHOT_CHANGED` para o background. |
| `popup.html/js/css` | UI mínima: input do JWT, status do scrape, último envio. |
| `selectors.json` | Seletores DOM versionados. Quando o TopstepX mudar o markup, editar aqui e recarregar a extensão. |

## Atualizando selectors

Se o popup mostrar **"Scrape: selectors out of date"**:

1. Abra DevTools em `topstepx.com/trade`.
2. Inspecione os campos que devem ser lidos (position size, PnL, etc.).
3. Edite `selectors.json`, adicionando o seletor novo **no topo** do array correspondente.
4. Em `chrome://extensions`, clique no ícone de **recarregar** da extensão.

## Privacidade

- A extensão envia **apenas** o snapshot da sua conta TopStep (campos listados em `selectors.json`) para o seu projeto Supabase autenticado com seu próprio JWT.
- Nenhum dado vai para servidores externos. RLS isola sua linha de qualquer outro usuário.
- O JWT fica em `chrome.storage.local` da extensão. Não é compartilhado com nenhum domínio.

## Suporte

Veja `live.install.steps` em [X-Metrics — aba Live].
