/**
 * LoopLiveFeed.wave2_pr_status_chip.test.jsx — Wave 2 (2026-09-08).
 *
 * The PR "Review PR on GitHub" link never updated after a ship — even
 * once the PR was merged or closed on GitHub, the row still just said
 * "PR opened for {sha}" forever. This adds a live status chip that
 * polls GET /loop/{id}/status (via getLoopStatus) and reflects the
 * real open/merged/closed state.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";

const mockGetLoopStatus = vi.fn();
vi.mock("../../lib/loopApi", () => ({
  rollbackLoop: vi.fn(),
  getLoopStatus: (...args) => mockGetLoopStatus(...args),
  streamLoopEvents: vi.fn(() => ({ abort: vi.fn() })),
}));

import LoopLiveFeed from "../LoopLiveFeed.jsx";

function prShipEvent() {
  return {
    state: "completed",
    phase: "ship",
    data: {
      type: "state",
      commit_sha: "5d939a4abcd",
      full_sha: "5d939a4abcd",
      html_url: "https://github.com/o/r/commit/5d939a4abcd",
      pr_url: "https://github.com/o/r/pull/42",
      pr_number: 42,
      files_changed: ["ROLLBACKTEST.md"],
      commit_message: "auto-commit",
    },
  };
}

function feedMount(props) {
  const { event, ...rest } = props;
  let handle;
  act(() => { handle = render(<LoopLiveFeed {...rest} event={null} />); });
  if (event) {
    act(() => { handle.rerender(<LoopLiveFeed {...rest} event={event} />); });
  }
  return handle;
}

describe("Wave 2 — PR status chip", () => {
  beforeEach(() => {
    mockGetLoopStatus.mockReset();
  });

  it("t_chip_defaults_to_open_before_first_poll_resolves", () => {
    mockGetLoopStatus.mockReturnValue(new Promise(() => {})); // never resolves
    feedMount({ loopId: "loop_w2_a", event: prShipEvent(), terminal: true });
    const chip = screen.getByTestId("loop-shipped-pr-status-chip-5d939a4");
    expect(chip).toHaveTextContent("Open");
    expect(chip.getAttribute("data-pr-status")).toBe("open");
  });

  it("t_chip_updates_to_merged_after_poll", async () => {
    mockGetLoopStatus.mockResolvedValue({ pr_status: "merged" });
    feedMount({ loopId: "loop_w2_b", event: prShipEvent(), terminal: true });
    await waitFor(() => {
      const chip = screen.getByTestId("loop-shipped-pr-status-chip-5d939a4");
      expect(chip.getAttribute("data-pr-status")).toBe("merged");
    });
    expect(screen.getByTestId("loop-shipped-pr-status-chip-5d939a4")).toHaveTextContent("Merged");
  });

  it("t_chip_updates_to_closed_after_poll", async () => {
    mockGetLoopStatus.mockResolvedValue({ pr_status: "closed" });
    feedMount({ loopId: "loop_w2_c", event: prShipEvent(), terminal: true });
    await waitFor(() => {
      const chip = screen.getByTestId("loop-shipped-pr-status-chip-5d939a4");
      expect(chip.getAttribute("data-pr-status")).toBe("closed");
    });
    expect(screen.getByTestId("loop-shipped-pr-status-chip-5d939a4")).toHaveTextContent("Closed");
  });

  it("t_no_chip_for_direct_commit_ship_no_pr_url", () => {
    feedMount({
      loopId: "loop_w2_d",
      event: {
        state: "completed", phase: "ship",
        data: { type: "state", commit_sha: "abc1234", full_sha: "abc1234",
                html_url: "https://github.com/o/r/commit/abc1234",
                files_changed: [], commit_message: "x" },
      },
      terminal: true,
    });
    expect(screen.queryByTestId("loop-shipped-pr-status-chip-abc1234")).toBeNull();
    expect(mockGetLoopStatus).not.toHaveBeenCalled();
  });
});
