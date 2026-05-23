import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

const manifest = JSON.parse(
  readFileSync(path.resolve(__dirname, "..", "manifest.json"), "utf-8"),
);

describe("manifest.json", () => {
  it("e MV3", () => {
    expect(manifest.manifest_version).toBe(3);
  });

  it("declara permissoes esperadas (Story 1-5 AC1: notifications + alarms + offscreen)", () => {
    for (const perm of ["notifications", "alarms", "storage", "tabs", "offscreen"]) {
      expect(manifest.permissions).toContain(perm);
    }
  });

  it("registra selectors.json em web_accessible_resources (Story 1-5 T4.3)", () => {
    const resources = manifest.web_accessible_resources?.[0]?.resources ?? [];
    expect(resources).toContain("selectors.json");
    expect(resources).toContain("sounds/alert.mp3");
  });

  it("host_permissions cobre topstepx.com", () => {
    expect(manifest.host_permissions).toContain("https://topstepx.com/*");
  });

  it("background.service_worker e background.js", () => {
    expect(manifest.background.service_worker).toBe("background.js");
  });
});
