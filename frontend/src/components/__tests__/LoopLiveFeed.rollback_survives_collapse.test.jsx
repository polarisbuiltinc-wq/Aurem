/**
 * LoopLiveFeed.rollback_survives_collapse.test.jsx
 *
 * Feb 2026 · Rollback-visibility regression fix — founder report:
 * "rollback not showing after successfully shipping" on production.
 *
 * Root cause: The persistent Shipped row (with the Rollback button)
 * lived INSIDE the `{!collapsed && ...}` scroller. When the founder
 * collapsed the feed panel via the chevron toggle in the header, the
 * Shipped label + Rollback button vanished with it — there was no
 * visible way to trigger a rollback.
 *
 * Fix: Move <ShippedRow /> OUTSIDE the collapse gate so ship-success
 * + Rollback stay visible regardless of panel state (which is the
 * whole point of a "persistent" row).
 *
 * This test locks in that contract: after a terminal ship event
 * arrives, collapsing the feed MUST NOT hide the Rollback button.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";

vi.mock("../../lib/loopApi", () => ({
  rollbackLoop:  vi.fn(),
  getLoopStatus: vi.fn(),
  streamLoopEvents: vi.fn(() => ({ abort: vi.fn() })),
}));

import LoopLiveFeed from "../LoopLiveFeed.jsx";

function shipEvent(sha = "abcdef1234") {
  return {
    state: "completed",
    phase: "ship",
    data: {
      type: "state",
      commit_sha: sha,
      full_sha: sha,
      html_url: `https://github.com/tj/repo/commit/${sha}`,
      files_changed: ["README.md"],
      commit_message: "test-ship",
    },
  };
}

describe("LoopLiveFeed — Rollback button survives feed collapse", () => {
  it("Rollback button stays visible after user collapses the feed", () => {
    const props = {
      loopId: "loop_collapse_test",
      event: null,
      terminal: false,
      phase: "shipping",
    };
    const { rerender } = render(<LoopLiveFeed {...props} />);

    // Feed OPEN by default — no ship event yet, so no Rollback button.
    expect(screen.queryByTestId(/loop-shipped-row-/)).toBeNull();

    // Terminal ship event arrives.
    rerender(
      <LoopLiveFeed
        {...props}
        event={shipEvent("abc1234f")}
        terminal
        phase="completed"
      />,
    );

    // Rollback button visible in the expanded (default) state.
    const rowOpen = screen.getByTestId(/loop-shipped-row-abc1234/);
    expect(rowOpen).toBeTruthy();
    expect(
      screen.getByTestId(/loop-shipped-rollback-btn-abc1234/),
    ).toBeTruthy();

    // User clicks the header chevron to collapse the feed body.
    const collapseToggle = screen.getByTestId("loop-live-feed-collapse-toggle");
    act(() => { fireEvent.click(collapseToggle); });

    // Scroller (narration list) is now GONE …
    expect(screen.queryByTestId("loop-live-feed-scroller")).toBeNull();

    // … BUT the persistent Shipped row + Rollback button MUST remain.
    expect(screen.getByTestId(/loop-shipped-row-abc1234/)).toBeTruthy();
    expect(
      screen.getByTestId(/loop-shipped-rollback-btn-abc1234/),
    ).toBeTruthy();
  });
});
