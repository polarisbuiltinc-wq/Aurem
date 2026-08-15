/**
 * Iter 388-ai (2026-02-14) — sidebar hide belt+suspenders.
 *
 * Prod regression report: Iter 388-af correctly toggled `hiddenForTyping`
 * state (verified by `data-hidden-typing="true"` on the wrapper) but
 * the rail stayed VISUALLY visible. Inner-nav collapse
 * (`transform: translateX(-105%)` + `marginLeft: -56`) apparently
 * didn't shrink the wrapper's flex-column contribution in some
 * browsers / cached bundles.
 *
 * Fix: apply an OUTER wrapper collapse too (`width: 0`,
 * `overflow: hidden`) so the rail vanishes regardless of whether
 * the inner-nav transform succeeds. Belt + suspenders.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const RAIL_SHELL = path.resolve(
  __dirname, "..", "..", "components", "nav", "RailShell.jsx",
);


describe("RailShell — Iter 388-ai outer-wrapper collapse", () => {
  const src = fs.readFileSync(RAIL_SHELL, "utf-8");

  it("outer wrapper collapses width to 0 when hiddenForTyping", () => {
    // The wrapper div's style{}\n block should now include a
    // hiddenForTyping-driven `width: 0` guard.
    expect(src).toMatch(
      /width: hiddenForTyping \? 0 : ["']auto["']/,
    );
  });

  it("outer wrapper hides overflow when hidden (prevents inner peek)", () => {
    expect(src).toMatch(
      /overflow: hiddenForTyping \? ["']hidden["'] : ["']visible["']/,
    );
  });

  it("wrapper transitions width smoothly", () => {
    expect(src).toMatch(/transition:.*width\s+240ms/);
  });

  it("inner-nav transform is still applied (kept as belt of belt+suspenders)", () => {
    expect(src).toMatch(
      /transform: hiddenForTyping \? ["']translateX\(-105%\)["'] : ["']translateX\(0\)["']/,
    );
  });
});
