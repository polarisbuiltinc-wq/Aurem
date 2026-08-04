/**
 * Iter 339j → Iter 342 → Iter 362 — prod bug regression history:
 *
 * Iter 339j: rollback clicks produced ZERO network calls under SSE
 *            remount churn.
 * Iter 342 : moved to pointerdown + native window.confirm() as a
 *            synchronous safety gate.
 * Iter 362 : replaced native window.confirm() with a themed in-app
 *            modal (Bug B — dark theme break + JS-thread block).
 *
 * Contract locked in here (post-Iter-362):
 *   • `pointerdown` opens the in-app RollbackConfirmModal.
 *   • Modal approve → POST /rollback fires IMMEDIATELY.
 *   • Modal cancel  → no POST.
 *   • window.confirm is NEVER called in the rollback flow.
 *   • Synthetic `click` after handled `pointerdown` is swallowed
 *     (exactly one modal opens per user interaction).
 *   • Module-level in-flight registry blocks re-fires across remounts.
 */
import React from "react";
import {
  render, fireEvent, cleanup, act,
} from "@testing-library/react";
import {
  describe, it, expect, vi, beforeEach, afterEach,
} from "vitest";

const rollbackLoopMock = vi.fn(() => Promise.resolve({ ok: true }));
vi.mock("../../lib/loopApi", () => ({
  rollbackLoop: (...a) => rollbackLoopMock(...a),
  streamLoopEvents: vi.fn(() => ({ abort: () => {} })),
}));

import {
  ShippedRow, _resetRollbackRegistriesForTests,
} from "../LoopLiveFeed";

const ship = {
  shortSha: "cc60342",
  fullSha: "cc60342".padEnd(40, "a"),
  htmlUrl: "https://github.com/x/y/commit/cc60342",
};

const btn = () =>
  document.querySelector('[data-testid="loop-shipped-rollback-btn-cc60342"]');
const modal = () =>
  document.querySelector('[data-testid="rollback-confirm-modal"]');
const approveBtn = () =>
  document.querySelector('[data-testid="rollback-confirm-approve"]');
const cancelBtn = () =>
  document.querySelector('[data-testid="rollback-confirm-cancel"]');

describe("Iter 362 — pointerdown + in-app modal rollback", () => {
  let confirmSpy;
  beforeEach(() => {
    rollbackLoopMock.mockClear();
    _resetRollbackRegistriesForTests();
    // Sentinel: the new flow MUST NOT invoke window.confirm.
    confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  });
  afterEach(() => {
    confirmSpy.mockRestore();
    cleanup();
  });

  it("single pointerdown → modal opens; approve → POST fires", async () => {
    render(
      <ShippedRow loopId="i362-a" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(modal()).not.toBeNull();
    await act(async () => {
      fireEvent.click(approveBtn());
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    expect(rollbackLoopMock).toHaveBeenCalledWith("i362-a");
  });

  it("modal Cancel → NO POST", async () => {
    render(
      <ShippedRow loopId="i362-b" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(modal()).not.toBeNull();
    await act(async () => {
      fireEvent.click(cancelBtn());
    });
    expect(rollbackLoopMock).not.toHaveBeenCalled();
  });

  it("pointerdown followed by synthetic click → exactly ONE modal open", async () => {
    render(
      <ShippedRow loopId="i362-c" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
      fireEvent.click(btn());
    });
    // A single modal — the 800ms lastFire guard swallows the click.
    const modals = document.querySelectorAll(
      '[data-testid="rollback-confirm-modal"]',
    );
    expect(modals.length).toBe(1);
    // Approve once → single POST.
    await act(async () => {
      fireEvent.click(approveBtn());
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
  });

  it("REMOUNT between render and press cannot eat the action", async () => {
    const { unmount } = render(
      <ShippedRow loopId="i362-d" ship={ship} onRollbackStarted={() => {}} />,
    );
    unmount();
    render(
      <ShippedRow loopId="i362-d" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(modal()).not.toBeNull();
    await act(async () => {
      fireEvent.click(approveBtn());
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    expect(rollbackLoopMock).toHaveBeenCalledWith("i362-d");
  });

  it("in-flight guard survives remount — second press after success does NOT re-open", async () => {
    const { unmount } = render(
      <ShippedRow loopId="i362-e" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    await act(async () => {
      fireEvent.click(approveBtn());
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    unmount();
    render(
      <ShippedRow loopId="i362-e" ship={ship} onRollbackStarted={() => {}} />,
    );
    // Remounted instance resyncs phase from the module registry.
    expect(btn().disabled).toBe(true);
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    // Modal must NOT re-open — disabled button + in-flight guard.
    expect(modal()).toBeNull();
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
  });

  it("keyboard activation (click without pointerdown) still works", async () => {
    render(
      <ShippedRow loopId="i362-f" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.click(btn());
    });
    expect(modal()).not.toBeNull();
    await act(async () => {
      fireEvent.click(approveBtn());
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    expect(rollbackLoopMock).toHaveBeenCalledWith("i362-f");
  });
});
