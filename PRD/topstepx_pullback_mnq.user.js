// ==UserScript==
// @name         TopstepX - Pullbacks MNQ Bone Zone (Custom Indicator)
// @namespace    https://topstepx.com/
// @version      0.3.2
// @description  Port JS do indicador Pine "Pullbacks MNQ - Bone Zone v7.1 PRO" para a charting_library da Topstep — refactor v0.3 (perf, estrutura, segurança); v0.3.1 marcadores como rótulos A+/B/C
// @author       Henrique
// @match        https://topstepx.com/*
// @match        https://*.topstepx.com/*
// @run-at       document-start
// @grant        none
// ==/UserScript==
//
// Como usar:
// 1. Instale o Tampermonkey no Chrome (https://www.tampermonkey.net/).
// 2. Tampermonkey -> Create a new script -> cole este arquivo inteiro -> salve (Ctrl+S).
// 3. Abra a aba de gráfico do TopstepX (https://topstepx.com/trade...).
// 4. No menu de Indicators do gráfico, busque por "Pullbacks MNQ" — aparece em "Custom".
// 5. Se não aparecer: F12 -> Console -> procure linhas com prefixo "[Pullback MNQ]".
//    A linha esperada é "custom indicator injetado v0.3.2".
//
// Novidades v0.3.0:
//   - Performance: cache de boundary VWAP CME elimina Intl.DateTimeFormat por barra.
//     Drop de ctx.new_var() ociosos (high_s/low_s). Consolidação de cálculos de alvos.
//     Instrumentação opt-in via window.__pbDebug = true (INFO a cada 1000 barras).
//   - Estrutura: blocos coesos (CONFIG / LOGGING / AUDIO / BOOTSTRAP / INDICATOR /
//     CONSTRUCTOR / UTILS / MAIN IMPL), constantes mágicas no topo, LOG_LEVEL ajustável.
//   - Robustez: detecção do widget validada por shape (não só getCustomIndicators),
//     soft-watch contínuo após 10s, reativação automática em SPA navigation,
//     guard de rota /trade, single AudioContext módulo-level.
//   - Sanidade: clamp de inputs com WARN single-shot, feature-detect de PineJS.Std.*,
//     reset de estado em troca de símbolo/timeframe, TTL configurável das linhas.
//
// Suposições (documentar se TopstepX mudar):
//   - charting_library v31.1.0 (_metainfoVersion: 51).
//   - Objeto global de config prefixo `tradingview_*` (aceita também `tradingview_widget_*`, `TVChartContainer_*`).
//   - Rota do chart contém "/trade" no pathname.
//   - PineJS.Std expõe: ema, sma, atr, highest, open, high, low, close, volume, time.
//
// Limitações (vs Pine original — duras da API custom indicators):
//   - Sem MTF (request.security não exposto). exigirAlgoAlpha valida AlgoAlpha do TF atual.
//   - Sem dashboard/tabela. Substituído por linhas Entry/Stop/Alvos persistentes.
//   - Sem alertcondition. Beep via Web Audio API.

(function () {
  'use strict';

  // ========================================================================
  // CONFIG — constantes ajustáveis no topo
  // ========================================================================

  const INDICATOR_VERSION = '0.3.2';
  const METAINFO_VERSION  = 51;

  // Verbosidade do console: 'debug' < 'info' < 'warn' < 'silent'
  const LOG_LEVEL = 'info';

  // Bootstrap: scan do widget
  const BOOTSTRAP_FAST_INTERVAL_MS = 100;
  const BOOTSTRAP_SLOW_INTERVAL_MS = 1000;
  const BOOTSTRAP_SOFT_AFTER_MS    = 10_000;
  const BOOTSTRAP_HARD_TIMEOUT_MS  = 300_000;

  // Prefixos aceitos para o objeto global de config do widget
  const WIDGET_GLOBAL_PREFIXES = ['tradingview_', 'tradingview_widget_', 'TVChartContainer_'];

  // Rota do chart na TopstepX (guard adicional além do @match)
  const CHART_PATH_REGEX = /\/trade/;

  // Janela "tempo real" para log/beep de sinais (não spam em replay histórico)
  const SIGNAL_RECENT_MS = 5 * 60 * 1000;

  // Instrumentação opcional de performance (window.__pbDebug = true)
  const PERF_REPORT_EVERY = 1000;

  // ========================================================================
  // LOGGING — níveis configuráveis
  // ========================================================================

  const __LEVELS = { debug: 10, info: 20, warn: 30, silent: 99 };
  const __LV = __LEVELS[LOG_LEVEL] != null ? __LEVELS[LOG_LEVEL] : __LEVELS.info;
  const DEBUG = __LV <= 10 ? (...a) => console.debug('[Pullback MNQ]', ...a) : () => {};
  const INFO  = __LV <= 20 ? (...a) => console.info ('[Pullback MNQ]', ...a) : () => {};
  const WARN  = __LV <= 30 ? (...a) => console.warn ('[Pullback MNQ]', ...a) : () => {};
  const ERR   = (...a) => console.error('[Pullback MNQ]', ...a);

  INFO(`carregado v${INDICATOR_VERSION} (metainfoVersion ${METAINFO_VERSION}, log ${LOG_LEVEL})`);

  // ========================================================================
  // AUDIO — singleton de módulo (1 AudioContext compartilhado entre instâncias)
  // ========================================================================

  let __audioCtx = null;
  function playBeep(direction) {
    try {
      if (!__audioCtx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        __audioCtx = new AC();
      }
      const ac = __audioCtx;
      try { if (ac.state === 'suspended') ac.resume(); } catch (_) {}
      const osc = ac.createOscillator();
      const gain = ac.createGain();
      osc.type = 'sine';
      osc.frequency.value = direction === 'long' ? 880 : 440;
      const t0 = ac.currentTime;
      gain.gain.setValueAtTime(0.0001, t0);
      gain.gain.exponentialRampToValueAtTime(0.2, t0 + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.25);
      osc.connect(gain);
      gain.connect(ac.destination);
      osc.start(t0);
      osc.stop(t0 + 0.3);
    } catch (_) {
      // AudioContext bloqueado por autoplay policy antes da 1a interação — ignora
    }
  }
  try { window.__pullbackMNQAudio = () => __audioCtx; } catch (_) {}

  // ========================================================================
  // BOOTSTRAP — scan, patch idempotente, SPA navigation
  // ========================================================================

  let __patchedCfg   = null;
  let __scanTimer    = null;
  let __scanStart    = 0;
  let __scanInterval = 0;

  function isChartRoute() {
    try { return CHART_PATH_REGEX.test(location.pathname); } catch (_) { return false; }
  }

  // Único sinal-base obrigatório: getCustomIndicators. Sinais extras são bônus
  // (logamos quais existem no primeiro patch para futura calibração).
  function looksLikeWidgetCfg(obj) {
    if (!obj) return false;
    try { return typeof obj.getCustomIndicators === 'function'; }
    catch (_) { return false; }
  }

  function describeCfgShape(obj) {
    const probes = ['chart', 'subscribe', 'setSymbol', '_options', '_innerAPI',
                    'activeChart', 'symbolInterval', 'onChartReady', 'addCustomIndicator'];
    const found = [];
    for (let i = 0; i < probes.length; i++) {
      try { if (obj[probes[i]] != null) found.push(probes[i]); } catch (_) {}
    }
    return found;
  }

  function findWidgetConfig() {
    const keys = Object.getOwnPropertyNames(window);
    for (let i = 0; i < keys.length; i++) {
      const key = keys[i];
      let prefixMatch = false;
      for (let p = 0; p < WIDGET_GLOBAL_PREFIXES.length; p++) {
        if (key.indexOf(WIDGET_GLOBAL_PREFIXES[p]) === 0) { prefixMatch = true; break; }
      }
      if (!prefixMatch) continue;
      let obj;
      try { obj = window[key]; } catch (_) { continue; }
      if (looksLikeWidgetCfg(obj)) return obj;
    }
    return null;
  }

  function patchCfg(cfg) {
    if (cfg.__pullbackMNQPatched) return;
    try {
      const src = cfg.getCustomIndicators.toString();
      if (src.length > 500) {
        WARN('possível conflito: getCustomIndicators já parece patched por outro script');
      }
    } catch (_) {}

    const orig = cfg.getCustomIndicators.bind(cfg);
    cfg.getCustomIndicators = (PineJS) =>
      Promise.resolve(orig(PineJS)).then((list) => {
        try {
          const ind = buildIndicator(PineJS);
          if (!ind) return list;
          return [...(Array.isArray(list) ? list : []), ind];
        } catch (e) {
          ERR('falha ao construir indicador:', e);
          return list;
        }
      });
    cfg.__pullbackMNQPatched = true;
    __patchedCfg = cfg;
    const shape = describeCfgShape(cfg);
    INFO(`custom indicator injetado v${INDICATOR_VERSION} (cfg shape: ${shape.length ? shape.join(', ') : 'só getCustomIndicators'})`);
  }

  function stopScan() {
    if (__scanTimer) { clearInterval(__scanTimer); __scanTimer = null; }
  }

  function tickScan() {
    const cfg = findWidgetConfig();
    if (cfg) {
      if (cfg !== __patchedCfg) patchCfg(cfg);
      stopScan();
      return;
    }
    const elapsed = Date.now() - __scanStart;
    if (elapsed > BOOTSTRAP_HARD_TIMEOUT_MS) {
      stopScan();
      WARN(`timeout ${BOOTSTRAP_HARD_TIMEOUT_MS / 1000}s: widget não encontrado — F5 ou volte para /trade`);
      return;
    }
    if (__scanInterval === BOOTSTRAP_FAST_INTERVAL_MS && elapsed > BOOTSTRAP_SOFT_AFTER_MS) {
      stopScan();
      __scanInterval = BOOTSTRAP_SLOW_INTERVAL_MS;
      __scanTimer = setInterval(tickScan, __scanInterval);
      DEBUG('scan desacelerado para 1s');
    }
  }

  function startScan() {
    if (__scanTimer) return;
    if (__patchedCfg && looksLikeWidgetCfg(__patchedCfg)) return; // já patched + cfg vivo
    if (!isChartRoute()) { DEBUG('fora da rota /trade, scan adiado'); return; }
    __patchedCfg = null;
    __scanStart = Date.now();
    __scanInterval = BOOTSTRAP_FAST_INTERVAL_MS;
    __scanTimer = setInterval(tickScan, __scanInterval);
    DEBUG('scan iniciado');
  }

  function onRouteChange() {
    if (isChartRoute()) {
      // Se cfg patched anterior não existe mais (widget destruído), reseta
      if (__patchedCfg && !looksLikeWidgetCfg(__patchedCfg)) __patchedCfg = null;
      startScan();
    } else {
      stopScan();
    }
  }

  function setupSPAObserver() {
    try {
      const origPush = history.pushState;
      history.pushState = function (...args) {
        const r = origPush.apply(this, args);
        try { onRouteChange(); } catch (_) {}
        return r;
      };
      const origReplace = history.replaceState;
      history.replaceState = function (...args) {
        const r = origReplace.apply(this, args);
        try { onRouteChange(); } catch (_) {}
        return r;
      };
    } catch (e) { WARN('falha ao monkey-patch history:', e); }
    window.addEventListener('popstate', onRouteChange);
  }

  setupSPAObserver();
  startScan();

  // ========================================================================
  // INDICATOR — factory que monta metainfo e devolve o objeto registrável
  // ========================================================================

  function buildIndicator(PineJS) {
    // Feature-detect das funções consumidas em mainImpl
    const std = PineJS && PineJS.Std;
    if (!std) { WARN('PineJS.Std ausente — não foi possível registrar indicador'); return null; }
    const needed = ['ema', 'sma', 'atr', 'highest', 'open', 'high', 'low', 'close', 'volume', 'time'];
    const missing = [];
    for (let i = 0; i < needed.length; i++) {
      if (typeof std[needed[i]] !== 'function') missing.push(needed[i]);
    }
    if (missing.length) {
      WARN('PineJS.Std faltando:', missing.join(', '));
      return null;
    }

    const COLORS = {
      EMA9:    '#2196F3', EMA21:   '#FFEB3B', EMA50:   '#FF9800', EMA200:  '#BDBDBD',
      VWAP:    '#FFFFFF',
      LONG_A:  '#00C853', LONG_B:  '#76FF03', LONG_C:  '#9E9E9E',
      SHORT_A: '#D50000', SHORT_B: '#FF6D00', SHORT_C: '#800000',
      BONE:    '#2196F3',
      ENTRY:   '#FFFFFF', STOP:    '#FF1744',
      TGT2:    '#00E676', TGT4:    '#00B0FF', TGT6:    '#7C4DFF',
    };

    const metainfo = {
      _metainfoVersion: METAINFO_VERSION,
      id: 'pullbacks_mnq_bone_zone_v3@tv-basicstudies-1',
      scriptIdPart: '',
      name: 'pullbacks_mnq_bone_zone_v3',
      description: `Pullbacks MNQ — Bone Zone v7.1 (port v${INDICATOR_VERSION})`,
      shortDescription: `Pullback MNQ v${INDICATOR_VERSION}`,
      is_price_study: true,
      isCustomIndicator: true,
      isHidden: false,
      format: { type: 'inherit' },

      plots: [
        { id: 'ema9',     type: 'line' },
        { id: 'ema21',    type: 'line' },
        { id: 'ema50',    type: 'line' },
        { id: 'ema200',   type: 'line' },
        { id: 'vwap',     type: 'line' },
        { id: 'long_a',   type: 'shapes' },
        { id: 'long_b',   type: 'shapes' },
        { id: 'long_c',   type: 'shapes' },
        { id: 'short_a',  type: 'shapes' },
        { id: 'short_b',  type: 'shapes' },
        { id: 'short_c',  type: 'shapes' },
        { id: 'entry',    type: 'line' },
        { id: 'stop',     type: 'line' },
        { id: 'target_2', type: 'line' },
        { id: 'target_4', type: 'line' },
        { id: 'target_6', type: 'line' },
      ],

      filledAreas: [
        { id: 'bone_fill', objAId: 'ema9', objBId: 'ema21', type: 'plot_plot', title: 'Bone Zone' },
      ],

      styles: {
        ema9:     { title: 'EMA 9',     histogramBase: 0 },
        ema21:    { title: 'EMA 21',    histogramBase: 0 },
        ema50:    { title: 'EMA 50',    histogramBase: 0 },
        ema200:   { title: 'EMA 200',   histogramBase: 0 },
        vwap:     { title: 'VWAP CME',  histogramBase: 0 },
        long_a:   { title: 'Long A+',   isHidden: false, location: 'BelowBar', plottype: 'shape_diamond'       },
        long_b:   { title: 'Long B',    isHidden: false, location: 'BelowBar', plottype: 'shape_triangle_up'   },
        long_c:   { title: 'Long C',    isHidden: false, location: 'BelowBar', plottype: 'shape_xcross'        },
        short_a:  { title: 'Short A+',  isHidden: false, location: 'AboveBar', plottype: 'shape_diamond'       },
        short_b:  { title: 'Short B',   isHidden: false, location: 'AboveBar', plottype: 'shape_triangle_down' },
        short_c:  { title: 'Short C',   isHidden: false, location: 'AboveBar', plottype: 'shape_xcross'        },
        entry:    { title: 'Entrada',   histogramBase: 0 },
        stop:     { title: 'Stop Loss', histogramBase: 0 },
        target_2: { title: 'Alvo 1:2',  histogramBase: 0 },
        target_4: { title: 'Alvo 1:4',  histogramBase: 0 },
        target_6: { title: 'Alvo 1:6',  histogramBase: 0 },
      },

      filledAreasStyle: {
        bone_fill: { color: COLORS.BONE, transparency: 80, visible: true },
      },

      defaults: {
        styles: {
          ema9:     { linestyle: 0, linewidth: 1, plottype: 0, trackPrice: false, transparency: 0, visible: true, color: COLORS.EMA9 },
          ema21:    { linestyle: 0, linewidth: 1, plottype: 0, trackPrice: false, transparency: 0, visible: true, color: COLORS.EMA21 },
          ema50:    { linestyle: 0, linewidth: 2, plottype: 0, trackPrice: false, transparency: 0, visible: true, color: COLORS.EMA50 },
          ema200:   { linestyle: 0, linewidth: 2, plottype: 0, trackPrice: false, transparency: 0, visible: true, color: COLORS.EMA200 },
          vwap:     { linestyle: 0, linewidth: 1, plottype: 0, trackPrice: false, transparency: 0, visible: true, color: COLORS.VWAP },
          long_a:   { color: COLORS.LONG_A,  textColor: COLORS.LONG_A,  transparency: 0, visible: true, size: 'small',  location: 'BelowBar', plottype: 'shape_diamond',       text: 'A+' },
          long_b:   { color: COLORS.LONG_B,  textColor: COLORS.LONG_B,  transparency: 0, visible: true, size: 'small',  location: 'BelowBar', plottype: 'shape_triangle_up',   text: 'B'  },
          long_c:   { color: COLORS.LONG_C,  textColor: COLORS.LONG_C,  transparency: 0, visible: true, size: 'small',  location: 'BelowBar', plottype: 'shape_xcross',        text: 'C'  },
          short_a:  { color: COLORS.SHORT_A, textColor: COLORS.SHORT_A, transparency: 0, visible: true, size: 'small',  location: 'AboveBar', plottype: 'shape_diamond',       text: 'A+' },
          short_b:  { color: COLORS.SHORT_B, textColor: COLORS.SHORT_B, transparency: 0, visible: true, size: 'small',  location: 'AboveBar', plottype: 'shape_triangle_down',  text: 'B'  },
          short_c:  { color: COLORS.SHORT_C, textColor: COLORS.SHORT_C, transparency: 0, visible: true, size: 'small',  location: 'AboveBar', plottype: 'shape_xcross',        text: 'C'  },
          entry:    { linestyle: 2, linewidth: 1, plottype: 0, trackPrice: false, transparency: 0, visible: true, color: COLORS.ENTRY },
          stop:     { linestyle: 2, linewidth: 1, plottype: 0, trackPrice: false, transparency: 0, visible: true, color: COLORS.STOP },
          target_2: { linestyle: 2, linewidth: 1, plottype: 0, trackPrice: false, transparency: 0, visible: true, color: COLORS.TGT2 },
          target_4: { linestyle: 2, linewidth: 1, plottype: 0, trackPrice: false, transparency: 0, visible: true, color: COLORS.TGT4 },
          target_6: { linestyle: 2, linewidth: 1, plottype: 0, trackPrice: false, transparency: 0, visible: true, color: COLORS.TGT6 },
        },
        filledAreasStyle: {
          bone_fill: { color: COLORS.BONE, transparency: 80, visible: true },
        },
        inputs: {
          filtrarEmas: true,
          exigirReversao: true,
          margemToque: 0,
          mostrarB: true,
          mostrarC: true,
          maxStopLoss: 100,
          minAlvo: 0,
          exigirAlgoAlpha: false,
          lookbackZLEMA: 20,
          bandaVolatilidade: 1.2,
          mostrarLinhas: true,
          tocarSom: true,
          linhasTTLBars: 50,
        },
      },

      inputs: [
        { id: 'filtrarEmas',       name: 'Filtrar por EMAs alinhadas (Bone Zone)',          defval: true,  type: 'bool'    },
        { id: 'exigirReversao',    name: 'Exigir vela anterior de reversão',                defval: true,  type: 'bool'    },
        { id: 'margemToque',       name: 'Margem/folga de toque (pontos)',                  defval: 0,     type: 'float', min: 0 },
        { id: 'mostrarB',          name: 'Mostrar entradas tipo B',                         defval: true,  type: 'bool'    },
        { id: 'mostrarC',          name: 'Mostrar entradas tipo C',                         defval: true,  type: 'bool'    },
        { id: 'maxStopLoss',       name: 'Stop Loss máximo (pontos)',                       defval: 100,   type: 'float', min: 0 },
        { id: 'minAlvo',           name: 'Alvo mínimo (pontos) para validação',             defval: 0,     type: 'float', min: 0 },
        { id: 'exigirAlgoAlpha',   name: 'Exigir AlgoAlpha confirmar (sem MTF nesta versão)', defval: false, type: 'bool' },
        { id: 'lookbackZLEMA',     name: 'Lookback ZLEMA (AlgoAlpha)',                      defval: 20,    type: 'integer', min: 1 },
        { id: 'bandaVolatilidade', name: 'Banda volatilidade ATR (AlgoAlpha)',              defval: 1.2,   type: 'float', min: 0 },
        { id: 'mostrarLinhas',     name: 'Mostrar linhas Entry/Stop/Alvos do último sinal', defval: true,  type: 'bool'    },
        { id: 'tocarSom',          name: 'Tocar beep (Web Audio) ao detectar sinal',        defval: true,  type: 'bool'    },
        { id: 'linhasTTLBars',     name: 'TTL das linhas em barras (0 = infinito)',         defval: 50,    type: 'integer', min: 0 },
      ],
    };

    return {
      name: 'Pullbacks MNQ - Bone Zone v7.1 (port)',
      version: INDICATOR_VERSION,
      apiVersion: METAINFO_VERSION,
      metainfo,
      constructor: function () { indicatorConstructor.call(this, PineJS); },
    };
  }

  // ========================================================================
  // CONSTRUCTOR — estado da instância + wrappers init/main
  // ========================================================================

  function indicatorConstructor(PineJS) {
    const self = this;

    // ---- estado da instância ----
    self._ctx = null;
    self._input = null;
    self._pullbackCount = 0;
    self._aaTrend = 0;
    // VWAP CME (com cache de boundary)
    self._vwapNum = 0;
    self._vwapDen = 0;
    self._vwapSessionKey = null;
    self._sessionBoundaryTs = -Infinity;
    self._tzFmt = null;
    // sinal confirmado + TTL
    self._confirmedDir = null;
    self._confirmedEntry = NaN;
    self._confirmedStop = NaN;
    self._confirmedRisk = NaN;
    self._signalBarsAgo = 0;
    // log/beep
    self._lastSignalTs = 0;
    self._loadTime = Date.now();
    self._mainErrLogged = false;
    // reset em troca de símbolo/timeframe
    self._lastSymbolKey = null;
    // clamp warnings (single-shot por input)
    self._warnedInputs = Object.create(null);
    // perf (opt-in via window.__pbDebug)
    self._perfCount = 0;
    self._perfTotalMs = 0;

    self.init = function (ctx, inputCallback) {
      try {
        self._ctx = ctx;
        self._input = inputCallback;
        try {
          self._tzFmt = new Intl.DateTimeFormat('en-CA', {
            timeZone: 'America/New_York',
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', hour12: false,
          });
        } catch (e) {
          self._tzFmt = null;
          WARN('Intl.DateTimeFormat(NY) falhou, VWAP usará reset UTC:', e);
        }
        DEBUG('init OK');
      } catch (e) {
        ERR('init falhou:', e);
      }
    };

    self.main = function (ctx, inputCallback) {
      const debug = window.__pbDebug === true;
      const t0 = debug ? performance.now() : 0;
      try {
        const r = mainImpl(self, PineJS, ctx, inputCallback);
        if (debug) {
          self._perfTotalMs += performance.now() - t0;
          self._perfCount++;
          if (self._perfCount % PERF_REPORT_EVERY === 0) {
            INFO(`perf: ${(self._perfTotalMs / self._perfCount).toFixed(3)} ms/barra (${self._perfCount} barras)`);
          }
        }
        return r;
      } catch (e) {
        if (!self._mainErrLogged) {
          ERR('main falhou (primeira ocorrência logada):', e);
          self._mainErrLogged = true;
        }
        return [NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN];
      }
    };
  }

  // ========================================================================
  // UTILS — leitura/clamp de inputs (WARN single-shot)
  // ========================================================================

  function readNum(self, idx, def, min, max, label) {
    const raw = self._input(idx);
    const n = Number(raw);
    if (!Number.isFinite(n)) {
      if (!self._warnedInputs[label]) {
        WARN(`input '${label}' inválido (${raw}); usando default ${def}`);
        self._warnedInputs[label] = true;
      }
      return def;
    }
    let v = n;
    if (typeof min === 'number' && v < min) {
      if (!self._warnedInputs[label]) {
        WARN(`input '${label}' (${n}) abaixo do mínimo ${min}; clampado`);
        self._warnedInputs[label] = true;
      }
      v = min;
    }
    if (typeof max === 'number' && v > max) {
      if (!self._warnedInputs[label]) {
        WARN(`input '${label}' (${n}) acima do máximo ${max}; clampado`);
        self._warnedInputs[label] = true;
      }
      v = max;
    }
    return v;
  }

  // ========================================================================
  // UTILS — VWAP CME com cache de boundary (evita Intl por barra)
  // ========================================================================

  // Retorna {key, boundaryTs} a partir do timestamp em ms epoch.
  // boundaryTs = próximo "18:00 NY" em ms UTC. Re-validado quando ts >= boundaryTs
  // (corrige DST automaticamente na primeira barra após transição).
  function computeCmeSession(self, ts) {
    if (self._tzFmt) {
      try {
        const parts = self._tzFmt.formatToParts(new Date(ts));
        let y = 0, m = 0, d = 0, h = 0;
        for (let i = 0; i < parts.length; i++) {
          const p = parts[i];
          if (p.type === 'year')       y = +p.value;
          else if (p.type === 'month') m = +p.value;
          else if (p.type === 'day')   d = +p.value;
          else if (p.type === 'hour')  h = +p.value;
        }
        const utcDay = Math.floor(Date.UTC(y, m - 1, d) / 86400000);
        const key = h >= 18 ? utcDay : utcDay - 1;

        // Offset NY-UTC observado neste timestamp (em ms; positivo: NY atrás de UTC)
        const tsHourFloor = Math.floor(ts / 3600000) * 3600000;
        const nyHourAsIfUtc = Date.UTC(y, m - 1, d, h, 0, 0);
        const offsetMs = tsHourFloor - nyHourAsIfUtc;
        // 18:00 NY (data NY de hoje) convertido para UTC
        const today18UtcMs = Date.UTC(y, m - 1, d, 18, 0, 0) + offsetMs;
        const boundaryTs = ts < today18UtcMs ? today18UtcMs : today18UtcMs + 86400000;
        return { key, boundaryTs };
      } catch (_) { /* cai no fallback */ }
    }
    // Fallback: reset por dia UTC
    const dayKey = Math.floor(ts / 86400000);
    return { key: dayKey, boundaryTs: (dayKey + 1) * 86400000 };
  }

  function cmeVwap(self, ts, high, low, close, volume) {
    if (ts >= self._sessionBoundaryTs || self._vwapSessionKey === null) {
      const s = computeCmeSession(self, ts);
      if (s.key !== self._vwapSessionKey) {
        self._vwapNum = 0;
        self._vwapDen = 0;
        self._vwapSessionKey = s.key;
      }
      self._sessionBoundaryTs = s.boundaryTs;
    }
    const tp = (high + low + close) / 3;
    if (Number.isFinite(volume) && volume > 0 && Number.isFinite(tp)) {
      self._vwapNum += tp * volume;
      self._vwapDen += volume;
    }
    return self._vwapDen > 0 ? self._vwapNum / self._vwapDen : close;
  }

  // ========================================================================
  // MAIN IMPL — loop por barra (extraído do constructor para evitar
  // desotimização do try/catch envolvente em self.main)
  // ========================================================================

  function mainImpl(self, PineJS, ctx, inputCallback) {
    self._ctx = ctx;
    self._input = inputCallback;
    ctx.select_sym(0);

    // -- reset em troca de símbolo/timeframe --
    let symKey = null;
    try {
      const sym = ctx.symbol;
      if (sym) symKey = (sym.ticker || sym.short_name || sym.name || '') + '|' + (sym.period_back || sym.period || sym.interval || '');
    } catch (_) {}
    if (symKey && symKey !== self._lastSymbolKey) {
      if (self._lastSymbolKey != null) DEBUG(`reset por troca de símbolo: ${self._lastSymbolKey} -> ${symKey}`);
      self._lastSymbolKey = symKey;
      self._pullbackCount = 0;
      self._aaTrend = 0;
      self._vwapNum = 0;
      self._vwapDen = 0;
      self._vwapSessionKey = null;
      self._sessionBoundaryTs = -Infinity;
      self._confirmedDir = null;
      self._confirmedEntry = NaN;
      self._confirmedStop = NaN;
      self._confirmedRisk = NaN;
      self._signalBarsAgo = 0;
    }

    // -- inputs (com clamp e WARN single-shot) --
    const filtrarEmas       = self._input(0);
    const exigirReversao    = self._input(1);
    const margemToque       = readNum(self, 2,  0,   0, undefined, 'margemToque');
    const mostrarB          = self._input(3);
    const mostrarC          = self._input(4);
    const maxStopLoss       = readNum(self, 5,  100, 0, undefined, 'maxStopLoss');
    const minAlvo           = readNum(self, 6,  0,   0, undefined, 'minAlvo');
    const exigirAlgoAlpha   = self._input(7);
    const lookbackZLEMA     = Math.floor(readNum(self, 8,  20, 1, undefined, 'lookbackZLEMA'));
    const bandaVolatilidade = readNum(self, 9,  1.2, 0, undefined, 'bandaVolatilidade');
    const mostrarLinhas     = self._input(10);
    const tocarSom          = self._input(11);
    const linhasTTLBars     = Math.floor(readNum(self, 12, 50, 0, undefined, 'linhasTTLBars'));

    // -- OHLCV --
    const open   = PineJS.Std.open(ctx);
    const high   = PineJS.Std.high(ctx);
    const low    = PineJS.Std.low(ctx);
    const close  = PineJS.Std.close(ctx);
    const volume = PineJS.Std.volume(ctx);

    // Apenas séries efetivamente lidas com .get() permanecem como new_var
    const close_s  = ctx.new_var(close);
    const open_s   = ctx.new_var(open);
    const volume_s = ctx.new_var(volume);

    // -- EMAs --
    const ema9   = PineJS.Std.ema(close_s, 9,   ctx);
    const ema21  = PineJS.Std.ema(close_s, 21,  ctx);
    const ema50  = PineJS.Std.ema(close_s, 50,  ctx);
    const ema200 = PineJS.Std.ema(close_s, 200, ctx);

    const smaVol20 = PineJS.Std.sma(volume_s, 20, ctx);

    // -- VWAP sessão CME (cache de boundary) --
    const ts = PineJS.Std.time(ctx);
    const vwap = cmeVwap(self, ts, high, low, close, volume);

    // -- AlgoAlpha local (sem MTF) --
    const lag = Math.floor((lookbackZLEMA - 1) / 2);
    const closeLag = close_s.get(lag);
    const zlemaSrc = close + (close - (Number.isFinite(closeLag) ? closeLag : close));
    const zlemaSrc_s = ctx.new_var(zlemaSrc);
    const zlema = PineJS.Std.ema(zlemaSrc_s, lookbackZLEMA, ctx);

    const atrVal = PineJS.Std.atr(lookbackZLEMA, ctx);
    const atr_s = ctx.new_var(atrVal);
    const atrHighest = PineJS.Std.highest(atr_s, lookbackZLEMA * 3, ctx);
    const volatility = atrHighest * bandaVolatilidade;

    let aaTrend = self._aaTrend;
    if (close > zlema + volatility) aaTrend = 1;
    else if (close < zlema - volatility) aaTrend = -1;
    self._aaTrend = aaTrend;

    const algoAlphaAlta  = exigirAlgoAlpha ? (aaTrend ===  1) : true;
    const algoAlphaBaixa = exigirAlgoAlpha ? (aaTrend === -1) : true;

    // -- Tendência --
    const tendenciaForteAlta  = ema9 > ema21 && ema21 > ema50 && ema50 > ema200;
    const tendenciaNormalAlta = close > ema50 && close > ema200 && ema9 > ema21;
    const isAlta  = filtrarEmas ? tendenciaForteAlta  : tendenciaNormalAlta;

    const tendenciaForteBaixa  = ema9 < ema21 && ema21 < ema50 && ema50 < ema200;
    const tendenciaNormalBaixa = close < ema50 && close < ema200 && ema9 < ema21;
    const isBaixa = filtrarEmas ? tendenciaForteBaixa : tendenciaNormalBaixa;

    // 1 new_var só (bit 0 = alta, bit 1 = baixa) em vez de 2
    const trendBits = (isAlta ? 1 : 0) | (isBaixa ? 2 : 0);
    const trend_s = ctx.new_var(trendBits);
    const prevBits = trend_s.get(1) | 0;
    const prevAlta  = (prevBits & 1) !== 0;
    const prevBaixa = (prevBits & 2) !== 0;

    if ((isAlta && !prevAlta) || (isBaixa && !prevBaixa)) {
      self._pullbackCount = 0;
    }

    // -- Toques (current bar high/low — não precisa new_var) --
    const zoneMax = Math.max(ema9, ema21) + margemToque;
    const zoneMin = Math.min(ema9, ema21) - margemToque;

    const tocouBoneZone = low <= zoneMax && high >= zoneMin;
    const tocouEMA50    = low <= ema50 + margemToque && high >= ema50 - margemToque;
    const tocouVWAP     = low <= vwap  + margemToque && high >= vwap  - margemToque;
    const isTouched     = tocouBoneZone || tocouEMA50 || tocouVWAP;

    // -- Vela anterior --
    const open1  = open_s.get(1);
    const close1 = close_s.get(1);
    const velaAnteriorNegativa = close1 < open1;
    const velaAnteriorPositiva = close1 > open1;
    const filtroVelaAntAlta  = exigirReversao ? velaAnteriorNegativa : true;
    const filtroVelaAntBaixa = exigirReversao ? velaAnteriorPositiva : true;

    // -- Gatilhos e risco --
    const gatilhoAlta  = close > open;
    const gatilhoBaixa = close < open;

    const stopLong  = Math.min(low,  ema21);
    const stopShort = Math.max(high, ema21);
    const riscoLong  = close - stopLong;
    const riscoShort = stopShort - close;

    const riscoValidoLong  = riscoLong  <= maxStopLoss && (riscoLong  * 2) >= minAlvo;
    const riscoValidoShort = riscoShort <= maxStopLoss && (riscoShort * 2) >= minAlvo;

    const preSinalAlta  = isAlta  && isTouched && filtroVelaAntAlta  && gatilhoAlta  && riscoValidoLong  && algoAlphaAlta;
    const preSinalBaixa = isBaixa && isTouched && filtroVelaAntBaixa && gatilhoBaixa && riscoValidoShort && algoAlphaBaixa;

    // -- Classificação A+/B/C --
    let longA = false, longB = false, longC = false;
    let shortA = false, shortB = false, shortC = false;

    if (preSinalAlta) {
      self._pullbackCount += 1;
      const cnt = self._pullbackCount;
      if ((tocouEMA50 || cnt === 2) && mostrarB) longB = true;
      else if ((cnt >= 3 || volume > smaVol20) && mostrarC) longC = true;
      else if (cnt === 1 && volume <= smaVol20) longA = true;
    }
    if (preSinalBaixa) {
      self._pullbackCount += 1;
      const cnt = self._pullbackCount;
      if ((tocouEMA50 || cnt === 2) && mostrarB) shortB = true;
      else if ((cnt >= 3 || volume > smaVol20) && mostrarC) shortC = true;
      else if (cnt === 1 && volume <= smaVol20) shortA = true;
    }

    // -- Persistência do sinal confirmado + TTL --
    const isLong  = longA  || longB  || longC;
    const isShort = shortA || shortB || shortC;

    let tgt2 = NaN, tgt4 = NaN, tgt6 = NaN;

    if (isLong || isShort) {
      const sigType = longA || shortA ? 'A+' : longB || shortB ? 'B' : 'C';
      const sigDir  = isLong ? 'long' : 'short';
      const dirMult = isLong ? 1 : -1;
      self._confirmedDir   = sigDir;
      self._confirmedEntry = close;
      self._confirmedStop  = isLong ? stopLong : stopShort;
      self._confirmedRisk  = isLong ? riscoLong : riscoShort;
      self._signalBarsAgo  = 0;

      // calcula alvos uma vez, reutiliza em log + linhas
      tgt2 = close + dirMult * self._confirmedRisk * 2;
      tgt4 = close + dirMult * self._confirmedRisk * 4;
      tgt6 = close + dirMult * self._confirmedRisk * 6;

      if (ts > self._lastSignalTs) {
        self._lastSignalTs = ts;
        if (ts >= Date.now() - SIGNAL_RECENT_MS) {
          INFO('SIGNAL', {
            ts:    new Date(ts).toISOString(),
            type:  sigType,
            dir:   sigDir,
            entry: close,
            stop:  self._confirmedStop,
            risk:  self._confirmedRisk,
            tgt2, tgt4, tgt6,
          });
          if (tocarSom) playBeep(sigDir);
        }
      }
    } else if (self._confirmedDir) {
      self._signalBarsAgo += 1;
      if (linhasTTLBars > 0 && self._signalBarsAgo > linhasTTLBars) {
        self._confirmedDir = null;
      }
    }

    // -- Linhas Entry/Stop/Alvos (persistem até próximo sinal ou TTL) --
    let entryL = NaN, stopL = NaN, t2L = NaN, t4L = NaN, t6L = NaN;
    if (mostrarLinhas && self._confirmedDir) {
      if (Number.isFinite(tgt2)) {
        // sinal nesta barra: reusa alvos calculados acima
        entryL = self._confirmedEntry;
        stopL  = self._confirmedStop;
        t2L = tgt2; t4L = tgt4; t6L = tgt6;
      } else {
        const dirMult = self._confirmedDir === 'long' ? 1 : -1;
        entryL = self._confirmedEntry;
        stopL  = self._confirmedStop;
        t2L = self._confirmedEntry + dirMult * self._confirmedRisk * 2;
        t4L = self._confirmedEntry + dirMult * self._confirmedRisk * 4;
        t6L = self._confirmedEntry + dirMult * self._confirmedRisk * 6;
      }
    }

    return [
      ema9, ema21, ema50, ema200, vwap,
      longA  ? low  : NaN,
      longB  ? low  : NaN,
      longC  ? low  : NaN,
      shortA ? high : NaN,
      shortB ? high : NaN,
      shortC ? high : NaN,
      entryL, stopL, t2L, t4L, t6L,
    ];
  }
})();
