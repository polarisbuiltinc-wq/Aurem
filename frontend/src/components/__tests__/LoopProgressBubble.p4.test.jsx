/**
 * LoopProgressBubble.test.jsx (P4 additions) — 2026-08-27, Journey/
 * Intent-Grounding build round.
 *
 * Reproduces + fixes: the plan card collapsing to a one-line summary
 * the instant it reaches "awaiting approval" (before the user ever
 * saw the plan), because `expanded = streaming || open` treated a
 * pending-approval state identically to a terminal/historical one.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import LoopProgressBubble, { isLoopProgressContent } from "../LoopProgressBubble.jsx";

describe("2026-08-27 P4 · awaiting-approval stays expanded", () => {
  const AWAITING_TEXT = [
    "**Step 1 / 5 — Plan**  · Plan ready — awaiting your approval.",
  ].join("\n");

  it("a plan awaiting approval is expanded by default (streaming=false)", () => {
    render(
      <LoopProgressBubble text={AWAITING_TEXT} streaming={false}>
        <div data-testid="full-body">PLAN BODY</div>
      </LoopProgressBubble>,
    );
    expect(screen.getByTestId("loop-progress-bubble").getAttribute("data-expanded")).toBe("true");
    expect(screen.getByTestId("full-body")).toBeInTheDocument();
    expect(screen.getByTestId("loop-progress-status")).toHaveTextContent("Awaiting approval");
  });

  it("clicking to collapse while awaiting approval has no effect — stays pinned", () => {
    render(
      <LoopProgressBubble text={AWAITING_TEXT} streaming={false}>
        <div data-testid="full-body">PLAN BODY</div>
      </LoopProgressBubble>,
    );
    fireEvent.click(screen.getByTestId("loop-progress-toggle"));
    expect(screen.getByTestId("full-body")).toBeInTheDocument();
    expect(screen.getByTestId("loop-progress-bubble").getAttribute("data-expanded")).toBe("true");
  });

  it("a REAL terminal event after the plan supersedes 'awaiting approval'", () => {
    const failedAfterPlan = AWAITING_TEXT + "\n**Failed**  boom during execute";
    render(<LoopProgressBubble text={failedAfterPlan} streaming={false} />);
    expect(screen.getByTestId("loop-progress-status")).toHaveTextContent("Failed");
  });

  it("isLoopProgressContent matches the real fallback-rendered line shape (with bullet + 'your')", () => {
    expect(isLoopProgressContent("· Plan ready — awaiting your approval.")).toBe(true);
  });
});
