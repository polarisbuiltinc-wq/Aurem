/**
 * App.build_routes_alive.test.js — Round-2 PR (P0-4).
 *
 * t_build_routes_still_resolve: source-inspection guard that the
 * /build* route family stays registered in App.jsx even though the
 * Settings TrackSwitcher UI that used to link to it is now hidden.
 * Existing personal-track users must not 404.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const APP_JSX = path.resolve(__dirname, "..", "App.jsx");

describe("App.jsx — Round-2 P0-4 /build routes stay alive", () => {
  const src = fs.readFileSync(APP_JSX, "utf-8");

  it("registers /build, /build/:draftId, /build/:draftId/ship, /build/:draftId/success", () => {
    expect(src).toMatch(/path="\/build"/);
    expect(src).toMatch(/path="\/build\/:draftId"/);
    expect(src).toMatch(/path="\/build\/:draftId\/ship"/);
    expect(src).toMatch(/path="\/build\/:draftId\/success"/);
  });

  it("all /build routes stay PrivateRoute-gated (no auth regression)", () => {
    const buildRouteLines = src
      .split("\n")
      .filter((l) => l.includes('path="/build'));
    expect(buildRouteLines.length).toBeGreaterThanOrEqual(4);
    for (const line of buildRouteLines) {
      expect(line).toMatch(/<PrivateRoute>/);
    }
  });
});
