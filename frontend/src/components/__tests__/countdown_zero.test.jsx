/**
 * countdown_zero.test.jsx — Round-2 PR (P0-3).
 *
 * Parameterized across the 3 named cards: when secondsLeft <= 0
 * (expiresAt already in the past), action buttons must be disabled.
 * Above zero, they must stay enabled. Backend cron + LoopExpiredCard
 * swap are unchanged — this is only the client-side race guard.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ShipPendingCard from "../ShipPendingCard.jsx";
import PlanApprovalCard from "../PlanApprovalCard.jsx";
import { UserActionCard } from "../LoopActionCards.jsx";

const PAST = () => new Date(Date.now() - 5000).toISOString();     // already expired
const FUTURE = () => new Date(Date.now() + 120000).toISOString(); // 2 min left

const PENDING_PAYLOAD = {
  owner: "x", repo: "y", branch: "z",
  files: ["a.py"], file_count: 1, commit_message: "msg",
};

describe("t_action_disabled_at_zero — buttons disabled once secondsLeft <= 0", () => {
  it("ShipPendingCard: Ship + Cancel disabled at 0:00", () => {
    render(<ShipPendingCard pending={PENDING_PAYLOAD} busy={false} expiresAt={PAST()} />);
    expect(screen.getByTestId("ship-to-github-btn")).toBeDisabled();
    expect(screen.getByTestId("ship-cancel-btn")).toBeDisabled();
    expect(screen.getByTestId("ship-pending-countdown").textContent).toMatch(/Waiting to expire/i);
  });

  it("PlanApprovalCard: Approve + Cancel disabled at 0:00", () => {
    render(<PlanApprovalCard onApprove={() => {}} onCancel={() => {}} disabled={false} expiresAt={PAST()} />);
    expect(screen.getByTestId("plan-approve-btn")).toBeDisabled();
    expect(screen.getByTestId("plan-cancel-btn")).toBeDisabled();
    expect(screen.getByTestId("plan-approval-countdown").textContent).toMatch(/Waiting to expire/i);
  });

  it("UserActionCard: retry/skip/abort disabled at 0:00", () => {
    render(
      <UserActionCard phase="verify" message="paused" errors={[]}
                       onAction={() => {}} busy={false} expiresAt={PAST()} />
    );
    expect(screen.getByTestId("loop-retry-btn")).toBeDisabled();
    expect(screen.getByTestId("loop-skip-btn")).toBeDisabled();
    expect(screen.getByTestId("loop-abort-btn")).toBeDisabled();
  });

  it("UserActionCard ship_human_review gate: Approve & Ship / Cancel ship disabled at 0:00", () => {
    render(
      <UserActionCard phase="ship" message="review" gateType="ship_human_review"
                       onAction={() => {}} busy={false} expiresAt={PAST()} />
    );
    expect(screen.getByTestId("loop-approve-ship-btn")).toBeDisabled();
    expect(screen.getByTestId("loop-cancel-ship-btn")).toBeDisabled();
  });
});

describe("t_action_enabled_above_zero — buttons stay enabled while time remains", () => {
  it("ShipPendingCard stays enabled with time left", () => {
    render(<ShipPendingCard pending={PENDING_PAYLOAD} busy={false} expiresAt={FUTURE()} />);
    expect(screen.getByTestId("ship-to-github-btn")).not.toBeDisabled();
    expect(screen.getByTestId("ship-cancel-btn")).not.toBeDisabled();
  });

  it("PlanApprovalCard stays enabled with time left", () => {
    render(<PlanApprovalCard onApprove={() => {}} onCancel={() => {}} disabled={false} expiresAt={FUTURE()} />);
    expect(screen.getByTestId("plan-approve-btn")).not.toBeDisabled();
    expect(screen.getByTestId("plan-cancel-btn")).not.toBeDisabled();
  });

  it("UserActionCard stays enabled with time left", () => {
    render(
      <UserActionCard phase="verify" message="paused" errors={[]}
                       onAction={() => {}} busy={false} expiresAt={FUTURE()} />
    );
    expect(screen.getByTestId("loop-retry-btn")).not.toBeDisabled();
    expect(screen.getByTestId("loop-skip-btn")).not.toBeDisabled();
    expect(screen.getByTestId("loop-abort-btn")).not.toBeDisabled();
  });

  it("cards with no expiresAt at all (secondsLeft null) are unaffected — no regression for non-expiring cards", () => {
    render(<ShipPendingCard pending={PENDING_PAYLOAD} busy={false} />);
    expect(screen.getByTestId("ship-to-github-btn")).not.toBeDisabled();
    expect(screen.queryByTestId("ship-pending-countdown")).toBeNull();
  });
});
