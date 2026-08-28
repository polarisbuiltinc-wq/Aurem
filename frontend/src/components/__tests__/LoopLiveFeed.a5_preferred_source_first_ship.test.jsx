/**
 * LoopLiveFeed.a5_preferred_source_first_ship.test.jsx — Visibility Kit
 * Phase A / A5 (2026-08-28). "Moment of delight" — PreferredSourceButton
 * shows exactly once, on the first-ever completed ship, never again.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";

vi.mock("../../lib/loopApi", () => ({
  rollbackLoop:  vi.fn(),
  getLoopStatus: vi.fn(),
  streamLoopEvents: vi.fn(() => ({ abort: vi.fn() })),
}));

import LoopLiveFeed from "../LoopLiveFeed.jsx";

function shipEvent(sha) {
  return {
    state: "completed", phase: "ship",
    data: { type: "state", commit_sha: sha, full_sha: sha,
            html_url: `https://github.com/o/r/commit/${sha}`,
            files_changed: [], commit_message: "x" },
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

describe("A5 — PreferredSourceButton shown once at first completed ship", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("t_shown_on_first_ship", () => {
    feedMount({ loopId: "loop_a5_1", event: shipEvent("aaa1111"), terminal: true });
    expect(screen.getByTestId("preferred-source-button")).toBeInTheDocument();
  });

  it("t_not_shown_again_on_second_ship_same_session", () => {
    feedMount({ loopId: "loop_a5_2", event: shipEvent("bbb2222"), terminal: true });
    expect(screen.getByTestId("preferred-source-button")).toBeInTheDocument();
    feedMount({ loopId: "loop_a5_3", event: shipEvent("ccc3333"), terminal: true });
    expect(screen.queryAllByTestId("preferred-source-button").length).toBe(1); // still just the first one
  });
});
