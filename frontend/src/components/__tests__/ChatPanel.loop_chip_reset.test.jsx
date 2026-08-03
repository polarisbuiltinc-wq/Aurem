/**
 * ChatPanel.loop_chip_reset.test.jsx — Feb 2026
 *
 * Founder request: "loop complete aur task finish hone ke baad ye loop
 * chip fir se refresh honi chahiye — no green". The 5-step LoopStepBar
 * (PLAN·EXECUTE·VERIFY·SCAN·SHIP) was staying frozen with all-green
 * checkmarks forever after a terminal event. Users want it to auto-
 * reset to idle so the next loop starts on a clean chip.
 *
 * This test is a SOURCE-level lock-in — it asserts that ChatPanel
 * schedules a delayed reset of `loopPhase` + `loopStepTones` + related
 * fields once the loop reaches terminal (loopTerminal=true). We
 * verify by scanning the source for the exact hook shape rather than
 * mounting the full ChatPanel (which requires SSE fixtures + Mongo).
 */
import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

describe("ChatPanel — LoopStepBar auto-reset on terminal", () => {
  const src = fs.readFileSync(
    path.resolve(__dirname, "../ChatPanel.jsx"),
    "utf-8",
  );

  it("declares the CHIP_RESET_DELAY_MS constant", () => {
    expect(src).toContain("CHIP_RESET_DELAY_MS = 8000");
  });

  it("schedules a delayed reset guarded on loopTerminal + loopPhase", () => {
    // The effect body must gate on both flags — resetting on
    // loopTerminal alone would fire during unmount races.
    expect(src).toMatch(/if\s*\(!loopTerminal\s*\|\|\s*!loopPhase\)\s*return;/);
    // The effect must clear all four chip-driving pieces of state.
    expect(src).toContain("setLoopPhase(null)");
    expect(src).toContain("setLoopStepTones({})");
    expect(src).toContain("setLoopErrorPhase(null)");
    expect(src).toContain("setLoopRetryCount(0)");
  });

  it("uses a setTimeout with the shared delay constant", () => {
    expect(src).toMatch(/setTimeout\s*\(\s*\(\s*\)\s*=>\s*\{[\s\S]{0,400}CHIP_RESET_DELAY_MS/);
  });

  it("returns a cleanup that clears the timer to avoid stale sets", () => {
    // Regression guard: without the cleanup, a rapid re-loop kick-off
    // would fire the reset AFTER the new loop started → the new
    // loop's PLAN step would blink back to future.
    expect(src).toMatch(/return\s*\(\s*\)\s*=>\s*clearTimeout\(t\)/);
  });
});
