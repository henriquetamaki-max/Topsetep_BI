# Changelog — Userscript Pullbacks MNQ

Trilha de versões do `PRD/topstepx_pullback_mnq.user.js`. Entradas mais recentes no topo.
Formato: `## <versão> — AAAA-MM-DD` com bullets agrupados por categoria.

---

## 0.3.0 — 2026-05-20

Refactor amplo: performance, estrutura, robustez e sanidade de runtime. Mantém
todas as funcionalidades de 0.2.x (EMAs, VWAP CME, Bone Zone, A+/B/C, linhas
Entry/Stop/Alvos, log estruturado, beep). Bumped `metainfo.id` para `*_v3` —
indicadores 0.2.x salvos no chart não migram automaticamente; readicionar.

### Performance

- **Cache de boundary VWAP CME**: `Intl.DateTimeFormat.formatToParts` agora é
  chamado apenas quando o timestamp ultrapassa o limite da próxima sessão (18:00 ET),
  em vez de toda barra. Em backfills longos, isso é o ganho dominante.
- **Drop de `ctx.new_var()` ociosos**: `high_s` e `low_s` removidos — nunca
  eram lidos com `.get()`. Auditoria do uso confirmou que `high`/`low` da barra
  atual já bastavam.
- **Consolidação de `isAlta_s`/`isBaixa_s`**: empacotados em 1 único `new_var`
  com bits `(isAlta?1:0) | (isBaixa?2:0)`. Reduz 1 alocação por barra.
- **Cálculo único de alvos**: `tgt2/tgt4/tgt6` calculados uma vez quando há
  sinal e reaproveitados no branch de log e no branch de linhas.
- **Instrumentação opt-in**: `window.__pbDebug = true` ativa medição de
  `ms/barra` agregada (INFO a cada 1000 barras).

### Estrutura

- **Blocos coesos** com cabeçalhos: `CONFIG` / `LOGGING` / `AUDIO` / `BOOTSTRAP`
  / `INDICATOR` / `CONSTRUCTOR` / `UTILS` / `MAIN IMPL`.
- **Constantes mágicas movidas para o topo** (`BOOTSTRAP_FAST_INTERVAL_MS`,
  `BOOTSTRAP_SOFT_AFTER_MS`, `BOOTSTRAP_HARD_TIMEOUT_MS`, `SIGNAL_RECENT_MS`,
  `WIDGET_GLOBAL_PREFIXES`, `CHART_PATH_REGEX`, `INDICATOR_VERSION`, `METAINFO_VERSION`).
- **`LOG_LEVEL` configurável** no topo (`'debug' | 'info' | 'warn' | 'silent'`),
  default `'info'`. Wrappers `DEBUG/INFO/WARN/ERR` respeitam o nível.
- **`mainImpl` extraído** do constructor para função módulo-level (mais legível,
  e o try/catch envolvente em `self.main` não desotimiza o hot path inteiro).
- **`buildIndicator` expõe `version` e `apiVersion`** no objeto retornado para
  facilitar debug em DevTools.

### Robustez / Bootstrap

- **Detecção do widget validada por shape**, não só `getCustomIndicators`:
  exige também 1 entre `chart` / `subscribe` / `setSymbol` / `_options` /
  `_innerAPI`. Reduz falso-positivo se outro objeto global tiver método homônimo.
- **Prefixos múltiplos aceitos**: `tradingview_`, `tradingview_widget_`,
  `TVChartContainer_` — lista configurável no topo.
- **Soft-watch contínuo após 10s**: intervalo de scan cai para 1s mas continua
  rodando até 5min (timeout duro). Capta widget recriado em SPA navigation.
- **Reativação em SPA navigation**: `history.pushState/replaceState` e
  `popstate` disparam re-scan ao entrar em rota `/trade`. Antes exigia F5.
- **Guard de rota**: scan só inicia quando `pathname` contém `/trade`
  (defesa em profundidade além do `@match`).
- **Detecção heurística de patch prévio**: WARN se `getCustomIndicators`
  parece já patched por outro userscript.

### Sanidade de runtime

- **Clamp de inputs numéricos** com WARN single-shot por input
  (`margemToque`, `maxStopLoss`, `minAlvo`, `bandaVolatilidade`,
  `lookbackZLEMA`, `linhasTTLBars`). Antes, `+x || 0` aceitava NaN e negativos
  silenciosamente.
- **Feature-detect de `PineJS.Std.*`** em `buildIndicator`: testa
  `ema/sma/atr/highest/open/high/low/close/volume/time`. Se faltar, WARN com
  lista exata e o indicador não é registrado (vs. erro críptico em runtime).
- **Reset de estado em troca de símbolo/timeframe**: zera contadores
  (`_pullbackCount`, `_aaTrend`), estado VWAP, sinal confirmado e `_signalBarsAgo`.
  Detecta via `ctx.symbol.ticker|short_name|name + period`.
- **TTL configurável das linhas** via novo input `linhasTTLBars`
  (default 50, `0 = infinito`): após N barras sem novo sinal, as linhas
  Entry/Stop/Alvos somem automaticamente. Evita "sinal fantasma" no chart.
- **AudioContext singleton** em escopo de módulo (1 instância compartilhada
  por múltiplas instâncias do indicador). `ac.resume()` chamado antes de cada
  beep para destravar após autoplay block. Exposto via
  `window.__pullbackMNQAudio()` para debug.

### Inputs novos

- `linhasTTLBars` (integer, default 50, min 0): TTL das linhas Entry/Stop/Alvos
  em barras. `0` = infinito.

### Notas de compatibilidade

- `metainfo.id` bumped: `pullbacks_mnq_bone_zone_v2` → `pullbacks_mnq_bone_zone_v3`.
  Instâncias salvas de 0.2.x **não migram**; o usuário precisa adicionar o
  indicador de novo (e a settings panel mostrará 13 inputs em vez de 12).
- `@match` continua broad (`https://topstepx.com/*` + subdomínios) para suportar
  SPA navigation de `/account` → `/trade`. Custo zero fora de `/trade` graças
  ao `CHART_PATH_REGEX` guard.

---

## 0.2.1 — 2026-05-18

- Ajustes menores de polimento; mesma feature surface da 0.2.0.

## 0.2.0 — 2026-05-18

- VWAP com reset por sessão CME (18:00 ET via `Intl.DateTimeFormat` com timezone NY).
- 5 plots de linha persistentes (Entry/Stop/Tgt2/Tgt4/Tgt6) que perduram até
  próximo sinal — substituto do dashboard (a API custom indicators não permite
  desenhar `table`).
- `console.info('[Pullback MNQ] SIGNAL', {...})` estruturado + beep via Web
  Audio API (880 Hz long, 440 Hz short).
- Debounce por timestamp da barra + janela de 5 min para não spammar no
  replay histórico.
- Inputs novos: `mostrarLinhas` (bool, default true), `tocarSom` (bool, default true).

## 0.1.0 — 2026-05-18

- Port inicial do Pine Script "Pullbacks MNQ - Bone Zone v7.1 PRO"
  (`PRD/indicatot_Htt.txt`) para userscript JS no formato custom indicator
  da charting_library da TopstepX.
- Limitações vs. Pine original (duras da API): sem MTF (`request.security`),
  sem `table` (dashboard), sem `alertcondition`.
