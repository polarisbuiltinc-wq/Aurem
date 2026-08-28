/**
 * LoopLiveFeed.p2c_pr_ship_status.test.jsx — P2-C/P2-E (2026-08-28).
 *
 * When ship_via_pr landed the commit on a throwaway branch + opened a
 * PR (not yet merged), the row must say "PR opened for {sha}" — not
 * "Shipped {sha}" — and link to the PR, not the unmerged commit. The
 * non-PR (direct-commit) path must render byte-identical to before
 * (regression lock, already covered by iter329_task2 — re-asserted
 * here for the split condition).
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";

vi.mock("../../lib/loopApi", () => ({
  rollbackLoop:  vi.fn(),
  getLoopStatus: vi.fn(),
  streamLoopEvents: vi.fn(() => ({ abort: vi.fn() })),
}));

import LoopLiveFeed, { extractShipInfo } from "../LoopLiveFeed.jsx";

function prShipEvent({ commit_sha = "5d939a4abcd", pr_url = "https://github.com/o/r/pull/42", pr_number = 42 } = {}) {
  return {
    state: "completed",
    phase: "ship",
    data: {
      type: "state",
      commit_sha,
      full_sha: commit_sha,
      html_url: `https://github.com/o/r/commit/${commit_sha}`,
      pr_url, pr_number,
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

describe("P2-C — extractShipInfo carries pr_url/pr_number", () => {
  it("t_extract_pr_fields: prUrl/prNumber present when data carries them", () => {
    const info = extractShipInfo([prShipEvent()]);
    expect(info.prUrl).toBe("https://github.com/o/r/pull/42");
    expect(info.prNumber).toBe(42);
  });

  it("t_extract_pr_fields_absent_for_direct_ship: null when data has no pr_url", () => {
    const info = extractShipInfo([{
      state: "completed", phase: "ship",
      data: { type: "state", commit_sha: "abc1234", html_url: "https://x/commit/abc1234" },
    }]);
    expect(info.prUrl).toBeNull();
    expect(info.prNumber).toBeNull();
  });
});

describe("P2-C/P2-E — ShippedRow renders PR-open state accurately", () => {
  it("t_pr_ship_label_says_pr_opened_not_shipped", () => {
    feedMount({ loopId: "loop_p2c_a", event: prShipEvent(), terminal: true });
    expect(screen.getByTestId("loop-shipped-label-5d939a4"))
      .toHaveTextContent(/PR opened for\s+5d939a4/);
    expect(screen.getByTestId("loop-shipped-label-5d939a4").textContent).not.toMatch(/^Shipped/);
  });

  it("t_pr_ship_links_to_pr_not_commit", () => {
    feedMount({ loopId: "loop_p2c_b", event: prShipEvent(), terminal: true });
    const link = screen.getByTestId("loop-shipped-pr-link-5d939a4");
    expect(link.getAttribute("href")).toBe("https://github.com/o/r/pull/42");
    expect(screen.queryByTestId("loop-shipped-github-5d939a4")).toBeNull();
  });

  it("t_pr_ship_shows_mini_guide_tooltip", () => {
    feedMount({ loopId: "loop_p2c_c", event: prShipEvent(), terminal: true });
    const guide = screen.getByTestId("loop-shipped-pr-guide-5d939a4");
    expect(guide.getAttribute("title")).toMatch(/nothing is live yet/i);
    expect(guide.getAttribute("title")).toMatch(/Merging it there makes it live/i);
  });

  it("t_direct_ship_unchanged: no PR data → still says Shipped + links to commit, no guide icon", () => {
    feedMount({
      loopId: "loop_p2c_d",
      event: {
        state: "completed", phase: "ship",
        data: { type: "state", commit_sha: "abc1234", full_sha: "abc1234",
                html_url: "https://github.com/o/r/commit/abc1234",
                files_changed: [], commit_message: "x" },
      },
      terminal: true,
    });
    expect(screen.getByTestId("loop-shipped-label-abc1234")).toHaveTextContent(/^Shipped\s+abc1234/);
    expect(screen.getByTestId("loop-shipped-github-abc1234")).toBeInTheDocument();
    expect(screen.queryByTestId("loop-shipped-pr-guide-abc1234")).toBeNull();
    expect(screen.queryByTestId("loop-shipped-pr-link-abc1234")).toBeNull();
  });
});
