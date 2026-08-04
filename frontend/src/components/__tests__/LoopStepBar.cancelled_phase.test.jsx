/**
 * LoopStepBar.cancelled_phase.test.jsx — Feb 2026
 *
 * Founder repro: "stop/abort click krna pe loop chip stop kyo nhi hoti".
 *
 * Trace:
 *   1. User hits Stop → ChatPanel sets loopPhase="cancelled" +
 *      loopTerminal=true, clears stepTones, sets errorPhase="cancelled".
 *   2. Pre-fix LoopStepBar: "cancelled" was NOT in isError list AND
 *      NOT in PHASE_TO_STEP map → treated as neutral → prior "success"
 *      tones lingered → chip looked like a happy completed loop AFTER
 *      the user hit stop (green checks for 8s until auto-reset).
 *   3. Fix: "cancelled" added to both isError and PHASE_TO_STEP → chip
 *      instantly flips into the danger-red rendering path.
 *
 * This test locks in that contract so no future refactor drops the
 * "cancelled" alias and re-introduces the ghost-green chip bug.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import LoopStepBar, { __PHASE_TO_STEP } from "../LoopStepBar.jsx";

describe("LoopStepBar — cancelled phase (Stop click)", () => {
  it('exposes "cancelled" in PHASE_TO_STEP as step 0 (parity with aborted)', () => {
    expect(__PHASE_TO_STEP.cancelled).toBe(0);
    expect(__PHASE_TO_STEP.aborted).toBe(0);
  });

  it('renders without lingering stepTones when phase is "cancelled"', () => {
    // Simulate the moment RIGHT after the Stop click: ChatPanel has
    // wiped stepTones AND set phase="cancelled". No step should be
    // painted as "success" (green) any longer.
    const { container } = render(
      <LoopStepBar
        phase="cancelled"
        errorStep={0}
        stepTones={{}}
      />,
    );
    // Chip is still mounted (the CHIP_RESET_DELAY_MS useEffect keeps
    // it visible for 8s so the user sees the cancelled acknowledgement).
    expect(container.firstChild).toBeTruthy();
    // No ECG variant="success" allowed after Stop — the previous
    // greens must not linger. We scan the rendered DOM for any
    // testid ending in "-ecg-success" and assert none exist.
    const successNodes = container.querySelectorAll('[data-testid$="-ecg-success"]');
    expect(successNodes.length).toBe(0);
  });

  it('does NOT return null on "cancelled" — user must see the flip', () => {
    const { container } = render(
      <LoopStepBar phase="cancelled" stepTones={{}} />,
    );
    // Chip stays mounted so the founder sees an ACK. The parent's
    // CHIP_RESET_DELAY_MS timer unmounts it 8 s later by nulling phase.
    expect(container.firstChild).not.toBeNull();
  });
});
