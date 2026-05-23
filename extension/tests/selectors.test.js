import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

const repoSelectors = JSON.parse(
  readFileSync(path.resolve(__dirname, "..", "selectors.json"), "utf-8"),
);

describe("selectors.json (NFR18)", () => {
  it("declara version 1 + validated_at + url_pattern", () => {
    expect(repoSelectors.version).toBe("1");
    expect(repoSelectors.validated_at).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(repoSelectors.url_pattern).toContain("topstepx.com/share");
  });

  it("tem candidatos para total_pnl e trade_count", () => {
    expect(Array.isArray(repoSelectors.selectors.total_pnl)).toBe(true);
    expect(repoSelectors.selectors.total_pnl.length).toBeGreaterThan(0);
    expect(Array.isArray(repoSelectors.selectors.trade_count)).toBe(true);
    expect(repoSelectors.selectors.trade_count.length).toBeGreaterThan(0);
  });

  it("declara fallback_regex valido para total_pnl e trade_count", () => {
    expect(typeof repoSelectors.fallback_regex.total_pnl).toBe("string");
    expect(() => new RegExp(repoSelectors.fallback_regex.total_pnl)).not.toThrow();
    expect(() => new RegExp(repoSelectors.fallback_regex.trade_count)).not.toThrow();
  });
});
