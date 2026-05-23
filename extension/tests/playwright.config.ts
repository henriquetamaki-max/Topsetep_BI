import { defineConfig } from "@playwright/test";

/**
 * Story 1-5 AC5 — regression E2E da extensao Chrome carregada.
 *
 * Playwright NAO suporta Chrome extensions em modo headless. Este config
 * roda em headed Chrome local — `npm run test:e2e` (manual).
 *
 * CI ainda nao roda este test (story 1-5 Dev Notes operacionais: "E2E de
 * extensao exige headed Chrome + xvfb no Ubuntu — adicionar quando houver
 * bandwidth, possivel story Wave 2"). `done_when.tests` apontou para
 * `npm test` (Vitest), nao para este.
 */
export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: "list",
  use: {
    headless: false,
    trace: "retain-on-failure",
  },
});
