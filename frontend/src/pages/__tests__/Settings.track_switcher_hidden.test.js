/**
 * Settings.track_switcher_hidden.test.js — Round-2 PR (P0-4).
 *
 * Source-inspection test (same convention as
 * Settings.iter388ab.dup-nav.test.js) locking:
 *   a. TRACK_SWITCHER_ENABLED gate constant exists and is false.
 *   b. The <TrackSwitcher> render is gated behind that constant.
 *   c. The TrackSwitcher import + component are NOT deleted (future
 *      product surface — hide, don't remove, per founder ruling
 *      D-TRACK).
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const SETTINGS = path.resolve(__dirname, "..", "..", "pages", "Settings.jsx");

describe("Settings.jsx — Round-2 P0-4 Personal Track hidden", () => {
  const src = fs.readFileSync(SETTINGS, "utf-8");

  it("t_track_switcher_hidden_in_settings: gate constant exists and is false", () => {
    expect(src).toMatch(/const TRACK_SWITCHER_ENABLED\s*=\s*false;/);
  });

  it("TrackSwitcher render is gated behind the constant, not unconditional", () => {
    expect(src).toMatch(/\{TRACK_SWITCHER_ENABLED\s*&&\s*\(\s*<TrackSwitcher/);
  });

  it("TrackSwitcher component definition stays intact (hide, not delete)", () => {
    expect(src).toMatch(/function TrackSwitcher\(/);
  });
});
