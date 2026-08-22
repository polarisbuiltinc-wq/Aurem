/**
 * LoopLiveFeed.iter362_modal_rollback.test.jsx — Feb 2026 · Iter 362
 *
 * Founder-reported regression (retest 2/2 reproductions):
 *   Bug B (P1) — Rollback used a native `window.confirm()` dialog
 *                that broke the app's dark theme and synchronously
 *                blocked the JS thread.
 *   Bug C (P3) — Button label locked at "ROLLING BACK — SEE HISTORY"
 *                perpetually, even after the rollback op finished.
 *
 * Contract locked in here:
 *   • Rollback pointerdown/click OPENS a themed in-app modal
 *     (`data-testid="rollback-confirm-modal"`). NO `window.confirm`
 *     is ever called.
 *   • Modal "Rollback" button (`rollback-confirm-approve`) fires the
 *     actual POST /rollback and closes the modal.
 *   • Modal "Cancel" button (`rollback-confirm-cancel`) closes the
 *     modal WITHOUT firing the POST.
 *   • Once `markRollbackTerminal(loopId)` is called (simulating the
 *     OperationHistory stream landing a terminal rollback event),
 *     the ShippedRow button label transitions from
 *     "Rolling back — see history" to "Rolled back — view history".
 *   • The button's `data-rollback-phase` attribute reflects the
 *     lifecycle: idle → submitting → handed-off → completed.
 */
import React from "react";
import {
  render, fireEvent, cleanup, act, waitFor,
} from "@testing-library/react";
import {
  describe, it, expect, vi, beforeEach, afterEach,
} from "vitest";

let resolveRollback;
const rollbackLoopMock = vi.fn(
  () => new Promise((res) => { resolveRollback = res; }),
);
vi.mock("../../lib/loopApi", () => ({
  rollbackLoop: (...a) => rollbackLoopMock(...a),
  streamLoopEvents: vi.fn(() => ({ abort: () => {} })),
}));

import {
  ShippedRow, markRollbackTerminal, _resetRollbackRegistriesForTests,
} from "../LoopLiveFeed";

const ship = {
  shortSha: "abc1234",
  fullSha: "abc1234".padEnd(40, "d"),
  htmlUrl: "https://github.com/x/y/commit/abc1234",
};

const btn = () =>
  document.querySelector('[data-testid="loop-shipped-rollback-btn-abc1234"]');
const modal = () =>
  document.querySelector('[data-testid="rollback-confirm-modal"]');
const approveBtn = () =>
  document.querySelector('[data-testid="rollback-confirm-approve"]');
const cancelBtn = () =>
  document.querySelector('[data-testid="rollback-confirm-cancel"]');

describe("Iter 362 · Bug B — in-app themed rollback confirmation modal", () => {
  let confirmSpy;
  beforeEach(() => {
    rollbackLoopMock.mockClear();
    resolveRollback = undefined;
    _resetRollbackRegistriesForTests();
    // Spy on window.confirm — MUST NOT be called by the new flow.
    confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  });
  afterEach(() => {
    confirmSpy.mockRestore();
    cleanup();
  });

  it("pointerdown OPENS the in-app modal — window.confirm is NEVER called", async () => {
    render(<ShippedRow loopId="lp-b1" ship={ship} onRollbackStarted={() => {}} />);
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    // Contract 1a — modal renders.
    expect(modal()).not.toBeNull();
    // Contract 1b — native confirm() is dead code.
    expect(confirmSpy).not.toHaveBeenCalled();
    // Contract 1c — POST has NOT fired yet (waiting on modal approve).
    expect(rollbackLoopMock).not.toHaveBeenCalled();
  });

  it("modal APPROVE → POST fires, modal closes", async () => {
    render(<ShippedRow loopId="lp-b2" ship={ship} onRollbackStarted={() => {}} />);
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(modal()).not.toBeNull();
    await act(async () => {
      fireEvent.click(approveBtn());
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    expect(rollbackLoopMock).toHaveBeenCalledWith("lp-b2");
    // Modal closed post-approve.
    expect(modal()).toBeNull();
  });

  it("modal CANCEL → no POST, modal closes, button returns to idle", async () => {
    render(<ShippedRow loopId="lp-b3" ship={ship} onRollbackStarted={() => {}} />);
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(modal()).not.toBeNull();
    await act(async () => {
      fireEvent.click(cancelBtn());
    });
    expect(rollbackLoopMock).not.toHaveBeenCalled();
    expect(modal()).toBeNull();
    // Button back to idle → can re-open the modal.
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(modal()).not.toBeNull();
  });

  it("Escape key closes the modal (a11y — keyboard cancel path)", async () => {
    render(<ShippedRow loopId="lp-b4" ship={ship} onRollbackStarted={() => {}} />);
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(modal()).not.toBeNull();
    // Iter 388t moved the Escape listener from window to the modal
    // container (WCAG focus-trap) — fire on the modal, matching where
    // a real user's keydown originates (focus is trapped inside it).
    await act(async () => {
      fireEvent.keyDown(modal(), { key: "Escape" });
    });
    expect(modal()).toBeNull();
    expect(rollbackLoopMock).not.toHaveBeenCalled();
  });
});

describe("Iter 362 · Bug C — rollback button label resets after terminal", () => {
  beforeEach(() => {
    rollbackLoopMock.mockClear();
    resolveRollback = undefined;
    _resetRollbackRegistriesForTests();
  });
  afterEach(() => { cleanup(); });

  it("phase transitions: idle → submitting → handed-off → completed", async () => {
    render(<ShippedRow loopId="lp-c1" ship={ship} onRollbackStarted={() => {}} />);

    // Baseline — idle.
    expect(btn().getAttribute("data-rollback-phase")).toBeNull(); // parent div
    const row = document.querySelector(
      '[data-testid="loop-shipped-row-abc1234"]',
    );
    expect(row.getAttribute("data-rollback-phase")).toBe("idle");

    // Open modal + approve → phase becomes submitting → handed-off.
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    await act(async () => {
      fireEvent.click(approveBtn());
      // Resolve the mock POST → the try block completes → phase="handed-off".
      resolveRollback && resolveRollback({ ok: true });
      await Promise.resolve();
    });

    // Post-POST — handed off.
    expect(row.getAttribute("data-rollback-phase")).toBe("handed-off");
    expect(btn().textContent).toContain("Rolling back — see history");

    // Bug C fix — OperationHistory's stream signals terminal completion.
    // ShippedRow's polling effect must transition phase → "completed"
    // and relabel the button.
    await act(async () => {
      markRollbackTerminal("lp-c1");
    });
    // Wait for the 500ms polling interval to observe the terminal flag.
    await waitFor(
      () => expect(row.getAttribute("data-rollback-phase")).toBe("completed"),
      { timeout: 1500 },
    );
    expect(btn().textContent).toContain("Rolled back — view history");
    // Contract 3c — button is NO LONGER stuck at "Rolling back...".
    expect(btn().textContent).not.toContain("Rolling back");
  });

  it("label does NOT flip to completed prematurely — needs the terminal signal", async () => {
    render(<ShippedRow loopId="lp-c2" ship={ship} onRollbackStarted={() => {}} />);
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    await act(async () => {
      fireEvent.click(approveBtn());
      resolveRollback && resolveRollback({ ok: true });
      await Promise.resolve();
    });
    const row = document.querySelector(
      '[data-testid="loop-shipped-row-abc1234"]',
    );
    // Without markRollbackTerminal, phase stays at handed-off.
    // Advance real time a bit to prove the polling doesn't auto-flip.
    await new Promise((r) => setTimeout(r, 800));
    expect(row.getAttribute("data-rollback-phase")).toBe("handed-off");
    expect(btn().textContent).toContain("Rolling back — see history");
  });
});
