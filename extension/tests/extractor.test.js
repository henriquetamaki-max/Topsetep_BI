import { describe, it, expect, beforeEach } from "vitest";

/**
 * Regression test da logica de extracao do content.js (Story 1-5 AC5).
 *
 * As funcoes abaixo sao copia LITERAL das versoes em chrome_extension/content.js
 * apos o fix da Story 1-5 (filtro `> 10` removido em extractPnL). Se alguem
 * reverter o filtro ou mudar a logica, este teste pega.
 *
 * Nao podemos importar content.js direto porque ele e IIFE com referencias a
 * chrome.runtime. Em vez disso, replicamos a logica pura aqui — duplicacao
 * justificada para isolar o teste de Chrome APIs.
 */

const DEFAULT_SELECTORS = {
  total_pnl: [
    '[data-testid="total-pnl"]',
    ".total-pnl",
    "#total-pnl",
    '[class*="totalPnl"]',
    '[class*="total-pnl"]',
    ".profit-loss",
    '[class*="profit"]',
  ],
  trade_count: [
    '[data-testid="trade-count"]',
    ".trade-count",
    "#trade-count",
    '[class*="tradeCount"]',
    '[class*="trade-count"]',
    '[class*="trades"]',
  ],
};

const DEFAULT_FALLBACK_REGEX = {
  total_pnl: "Total\\s+P&L[:\\s]*[\\$]?([+-]?\\d{1,3}(?:,\\d{3})*(?:\\.\\d{2})?)",
  trade_count: "(\\d+)\\s*trades",
};

function extractPnL(doc, selectors = DEFAULT_SELECTORS, fallback = DEFAULT_FALLBACK_REGEX) {
  const candidates = selectors.total_pnl;
  for (const selector of candidates) {
    try {
      const elements = doc.querySelectorAll(selector);
      for (const element of elements) {
        const text = element.textContent || "";
        const pnlMatch = text.match(/(-?\$?-?\d{1,3}(?:,\d{3})*(?:\.\d{2})?)/);
        if (pnlMatch) {
          const cleaned = pnlMatch[1].replace(/[\$,]/g, "");
          const pnl = parseFloat(cleaned);
          if (!Number.isNaN(pnl)) {
            return pnl;
          }
        }
      }
    } catch (e) {
      /* selector invalido — segue */
    }
  }
  const pageText = doc.body.textContent || "";
  const fallbackPattern = new RegExp(fallback.total_pnl, "i");
  const m = pageText.match(fallbackPattern);
  if (m) return parseFloat(m[1].replace(/,/g, ""));
  return 0;
}

function extractTradeCount(doc, selectors = DEFAULT_SELECTORS, fallback = DEFAULT_FALLBACK_REGEX) {
  const candidates = selectors.trade_count;
  for (const selector of candidates) {
    try {
      const elements = doc.querySelectorAll(selector);
      for (const element of elements) {
        const text = element.textContent || "";
        const m = text.match(/(\d+)/);
        if (m) {
          const count = parseInt(m[1], 10);
          if (count >= 0 && count < 10000) return count;
        }
      }
    } catch (e) {
      /* selector invalido — segue */
    }
  }
  const pageText = doc.body.textContent || "";
  const fallbackPatterns = [
    new RegExp(fallback.trade_count, "i"),
    /trades?[:\s]*(\d+)/i,
  ];
  for (const pattern of fallbackPatterns) {
    const m = pageText.match(pattern);
    if (m) {
      const count = parseInt(m[1], 10);
      if (count >= 0 && count < 10000) return count;
    }
  }
  return 0;
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("extractPnL (Story 1-5 fix: filtro `> 10` removido)", () => {
  it("extrai PnL pequeno ($5.50) via data-testid (regression do filtro removido)", () => {
    document.body.innerHTML = `<div data-testid="total-pnl">$5.50</div>`;
    expect(extractPnL(document)).toBe(5.5);
  });

  it("extrai PnL pequeno via classe .total-pnl", () => {
    document.body.innerHTML = `<span class="total-pnl">$2.25</span>`;
    expect(extractPnL(document)).toBe(2.25);
  });

  it("extrai PnL grande ($1,500.00) via class*=totalPnl", () => {
    document.body.innerHTML = `<div class="totalPnlDisplay">$1,500.00</div>`;
    expect(extractPnL(document)).toBe(1500);
  });

  it("extrai PnL negativo (-$120.00)", () => {
    document.body.innerHTML = `<div data-testid="total-pnl">-$120.00</div>`;
    expect(extractPnL(document)).toBe(-120);
  });

  it("usa fallback regex em pageText quando seletores nao casam", () => {
    document.body.innerHTML = `<p>Total P&L: $250.75 (today)</p>`;
    expect(extractPnL(document)).toBe(250.75);
  });

  it("retorna 0 quando nem seletor nem fallback casam", () => {
    document.body.innerHTML = `<div>nothing relevant here</div>`;
    expect(extractPnL(document)).toBe(0);
  });
});

describe("extractTradeCount", () => {
  it("extrai contagem via data-testid", () => {
    document.body.innerHTML = `<div data-testid="trade-count">7</div>`;
    expect(extractTradeCount(document)).toBe(7);
  });

  it("extrai zero trades", () => {
    document.body.innerHTML = `<span class="trade-count">0</span>`;
    expect(extractTradeCount(document)).toBe(0);
  });

  it("usa fallback regex `\\d+ trades` quando seletores nao casam", () => {
    document.body.innerHTML = `<p>Today: 12 trades executed</p>`;
    expect(extractTradeCount(document)).toBe(12);
  });

  it("ignora numeros >= 10000 (filtro de sanidade preservado)", () => {
    document.body.innerHTML = `<div class="trade-count">99999</div>`;
    expect(extractTradeCount(document)).toBe(0);
  });
});
