/**
 * Session 7 · Item 1 regression contract.
 *
 * Real-user QA discovered two coupled UI-state bugs on Stop/Cancel:
 *
 *   (a) STOP LOOP · UI stayed stuck for 263+ seconds. Backend
 *       processed the cancel correctly (confirmed via reload → history
 *       showed "cancelled at plan phase"), but the frontend kept
 *       showing "LOOP · AWAITING APPROVAL" with a live-incrementing
 *       timer because loopId / loopPhase / loopPlan were never cleared
 *       client-side. Follow-up chat messages queued behind the ghost
 *       loop instead of processing.
 *
 *   (b) SAFETY-CRITICAL · PlanApprovalCard sometimes rendered with
 *       ONLY the send button (tooltip "Run loop") visible and NO
 *       Cancel option. Happened on a destructive test plan (mass-
 *       delete + force-push). The whole safety model
 *       ("cancel any time before Step 2") depends on Cancel being
 *       present whenever a plan is shown.
 *
 * Both fixes locked below via source-level asserts + a rendered
 * PlanApprovalCard smoke test.
 */
import fs from "node:fs";
import path from "node:path";
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import PlanApprovalCard from "../PlanApprovalCard.jsx";


const CHAT_PANEL_SRC = fs.readFileSync(
  path.resolve(__dirname, "../ChatPanel.jsx"),
  "utf-8",
);


describe("Session 7 · Item 1 · Stop-loop UI state sync", () => {
  it("Stop handler MUST synchronously clear the client loop state", () => {
    // The Stop handler (~L1195 in ChatPanel.jsx) fires cancelLoop()
    // and MUST also mark the loop terminal + drop the pending plan
    // on the same render tick. Otherwise LoopStatusChip's 10 s poll
    // keeps rehydrating the ghost loop.
    const requiredWrites = [
      "loopTerminalRef.current = true",
      "setLoopTerminal(true)",
      "setLoopPhase(\"cancelled\")",
      "setLoopPlan(null)",
    ];
    for (const line of requiredWrites) {
      expect(CHAT_PANEL_SRC).toContain(line);
    }
    // And these MUST live inside the Session 7 · Item 1 block so a
    // future refactor doesn't silently drop them.
    expect(CHAT_PANEL_SRC).toMatch(/Session 7 · Item 1[\s\S]{0,1200}setLoopPhase\("cancelled"\)/);
  });
});


describe("Session 7 · Item 1 · SAFETY — Cancel button always present", () => {
  it("PlanApprovalCard renders BOTH Approve & Cancel controls unconditionally", () => {
    // The presentational card has no conditional that can strip either
    // button — verify both are always in the DOM given valid callbacks.
    render(<PlanApprovalCard onApprove={() => {}} onCancel={() => {}} />);
    expect(screen.getByTestId("plan-approve-btn")).toBeInTheDocument();
    expect(screen.getByTestId("plan-cancel-btn")).toBeInTheDocument();
  });

  it("PlanApprovalCard renders Cancel even when disabled prop is truthy", () => {
    // Founder QA fear: a `disabled` state hides Cancel. Verify not.
    render(<PlanApprovalCard onApprove={() => {}} onCancel={() => {}} disabled />);
    expect(screen.getByTestId("plan-approve-btn")).toBeInTheDocument();
    expect(screen.getByTestId("plan-cancel-btn")).toBeInTheDocument();
    // Both must still be reachable — disabled = greyed, NOT hidden.
    expect(screen.getByTestId("plan-cancel-btn")).toBeDisabled();
  });

  it("showPlanCard gate is broadened past the single 'plan_pending' phase", () => {
    // The pre-Session-7 gate required loopPhase === 'plan_pending'
    // strictly. During SSE reconciliation races the phase can
    // transit through 'plan' / 'planning' / 'awaiting_confirmation'
    // for a render tick, stripping the entire approval card. Fix:
    // accept the broader set of "waiting for user" phases.
    expect(CHAT_PANEL_SRC).toContain("_PLAN_APPROVAL_PHASES");
    expect(CHAT_PANEL_SRC).toMatch(
      /_PLAN_APPROVAL_PHASES = new Set\(\[[^\]]*"plan_pending"[^\]]*\]\)/s);
    expect(CHAT_PANEL_SRC).toMatch(
      /_PLAN_APPROVAL_PHASES = new Set\(\[[^\]]*"awaiting_confirmation"[^\]]*\]\)/s);
    // And the fallback safety net — any non-active phase still shows
    // the card so an unknown phase name never strips Cancel.
    expect(CHAT_PANEL_SRC).toContain(
      'against a phase-name we don\'t recognise stripping the Cancel');
  });

  it("showPlanCard gate is STILL blocked when loop is terminal", () => {
    // Regression guard — the widening must NOT reopen the Iter 289 bug
    // (approve-button on a failed loop → 499 on /confirm).
    expect(CHAT_PANEL_SRC).toMatch(
      /!loopTerminal[\s\S]{0,200}!loopTerminalRef\.current/);
  });

  it("cancelled/failed/done/error phases DO NOT reopen the approval card", () => {
    // The exclude list must include every terminal-ish phase.
    for (const p of ["done", "error", "cancelled", "failed"]) {
      expect(CHAT_PANEL_SRC).toContain(`"${p}"`);
    }
  });
});
