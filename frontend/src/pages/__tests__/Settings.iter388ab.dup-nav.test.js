/**
 * Iter 388-ab (2026-02-14) — Settings duplicate-navigation cleanup.
 *
 * Founder review flagged that /settings had TWO navigation surfaces:
 *   1. A top tab bar inside the Settings page (Profile / Plans / Integrations / Vault)
 *   2. The left rail drawer already shows the same four items (+ IDE setup)
 *
 * The top tab bar was removed. The rail drawer is now the single source
 * of truth. Settings.jsx keeps the `tab` state (driven by `?tab=…` URL
 * param) and shows a small section header instead.
 *
 * These tests lock in three things via source inspection:
 *   a. The `role="tablist"` element is gone from Settings.jsx.
 *   b. The new `settings-section-header-<id>` testid element exists.
 *   c. Settings.jsx contains a useEffect that reacts to
 *      `location.search` — so clicking a rail item while already on
 *      /settings updates the visible content.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const SETTINGS = path.resolve(
  __dirname, "..", "..", "pages", "Settings.jsx",
);

describe("Settings.jsx — Iter 388-ab duplicate-nav cleanup", () => {
  const src = fs.readFileSync(SETTINGS, "utf-8");

  it("removes the visible tab bar (no role=\"tablist\" anymore)", () => {
    expect(src).not.toMatch(/role="tablist"/);
  });

  it("removes the per-tab settings-tab-<id> testid buttons", () => {
    expect(src).not.toMatch(/data-testid=`settings-tab-\$\{id\}`/);
    expect(src).not.toMatch(/data-testid="settings-tab-/);
  });

  it("keeps TABS metadata so the section header can render", () => {
    expect(src).toMatch(/const TABS = \[/);
    expect(src).toMatch(/"profile"/);
    expect(src).toMatch(/"plans"/);
    expect(src).toMatch(/"integrations"/);
    expect(src).toMatch(/"vault"/);
  });

  it("renders a section header with settings-section-header-<id> testid", () => {
    expect(src).toMatch(/data-testid=\{`settings-section-header-\$\{current\.id\}`\}/);
  });

  it("syncs `tab` state when URL search string changes (rail drawer path)", () => {
    // The rail drawer navigates to `/settings?tab=<id>` which only
    // changes location.search — without the effect below, the Settings
    // page would not update its content after a same-page rail click.
    expect(src).toMatch(/useEffect\(\(\)\s*=>\s*\{[\s\S]*?location\.search[\s\S]*?\},\s*\[location\.search/);
  });

  it("no longer calls setTab directly outside the URL-sync effect", () => {
    // switchTab(id) is the only allowed mutator — it also updates the URL.
    // A raw setTab('plans') without navigate() is a regression.
    const rawSetTabCalls = src.match(/[^w]setTab\(/g) || [];
    // Exactly two allowed occurrences: the useState hook and the sync effect.
    // Any third occurrence means someone forgot to use switchTab().
    expect(rawSetTabCalls.length).toBeLessThanOrEqual(2);
  });
});
