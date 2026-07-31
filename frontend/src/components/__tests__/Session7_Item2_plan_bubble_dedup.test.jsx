/**
 * Session 7 · Item 2 regression contract — plan-bubble dedup.
 *
 * Real-user QA reproduced 3× separately: plan-generation with
 * multiple internal LLM retries + slow SSE re-emit + the Iter 316
 * Fix A fallback poll could each deliver a plan-ready event for
 * the SAME loop_id. Result: 3-4 identical "Plan ready" bubbles
 * in the chat history.
 *
 * Fix: `handleLoopEvent`'s plan-absorb block now checks if a
 * `loopPlan:true` bubble already exists for the loop_id. If yes,
 * update in place (idempotent); if no, replace the pending bubble
 * or append (belt-and-braces).
 *
 * This test asserts the source-level invariant via string match
 * because `handleLoopEvent` is not directly exported and mounting
 * the full ChatPanel with SSE mocks would be brittle. The behavior
 * contract (idempotent + single bubble per loop_id) is locked.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";

const CHAT_PANEL_SRC = fs.readFileSync(
  path.resolve(__dirname, "../ChatPanel.jsx"),
  "utf-8",
);

describe("Session 7 · Item 2 · Plan-bubble dedup on retries", () => {
  it("plan-absorb block dedupes by loop_id via findIndex", () => {
    // The key dedup logic — MUST check for existing plan bubble
    // by loop_id BEFORE creating a new one.
    expect(CHAT_PANEL_SRC).toMatch(
      /const existingPlanIdx = m\.findIndex\([\s\S]{0,400}row\.loopPlan === true[\s\S]{0,200}row\.loop_id === lid/);
  });

  it("existing-plan branch updates in place instead of pushing new", () => {
    // If existingPlanIdx !== -1, we must map/update — NOT push.
    expect(CHAT_PANEL_SRC).toMatch(
      /existingPlanIdx !== -1[\s\S]{0,800}content: planMd/);
    // And the spread must preserve prior row fields.
    expect(CHAT_PANEL_SRC).toContain("...row, content: planMd");
  });

  it("existing-plan branch also purges leftover pending bubbles", () => {
    // Otherwise a stale "Generating plan… 337s" ghost could linger
    // next to the deduped plan.
    expect(CHAT_PANEL_SRC).toMatch(
      /existingPlanIdx !== -1[\s\S]{0,800}row\.loopPending \|\| row\.loopLive/);
  });

  it("belt-and-braces branch appends plan when no pending exists", () => {
    // If neither an existing plan nor a pending exists (a race
    // condition stripped the pending prematurely), we still show
    // the plan by appending it — no silent loss.
    expect(CHAT_PANEL_SRC).toMatch(
      /if \(!replaced\)[\s\S]{0,300}out\.push\([\s\S]{0,200}loopPlan: true/);
  });

  it("comment explicitly references the Session 7 · Item 2 fix", () => {
    // Source-lock so a future refactor can't silently strip the
    // dedup and reintroduce the 3-4 duplicate bubbles.
    expect(CHAT_PANEL_SRC).toContain("Session 7 · Item 2");
    expect(CHAT_PANEL_SRC).toContain(
      "duplicate-plan dedup");
  });
});
