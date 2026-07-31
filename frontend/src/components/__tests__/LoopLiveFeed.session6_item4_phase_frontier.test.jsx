/**
 * LoopLiveFeed.session6_item4_phase_frontier.test.jsx
 *
 * Session 6 · Item 4 regression contract.
 *
 * Real-user QA repro: the LiveFeed panel showed
 * "Writing README.md (stalled)" for 1m 41s WHILE the Ship panel
 * simultaneously showed "Ready to ship · manual confirmation
 * required". Contradictory state — either the execute step is
 * stuck OR the ship gate is ready, both cannot be true.
 *
 * Root cause: `resolveStalePendingByFrontier` derived its frontier
 * from NARRATION steps only. When the engine emitted
 * `state=paused_for_user, phase=ship, data.kind=awaiting_ship`
 * WITHOUT an accompanying narration frame of `step=ship`, the
 * frontier stayed at execute → the stale-execute pending narration
 * was never auto-resolved.
 *
 * Fix: the frontier now also considers each raw event's `phase`
 * field via PHASE_FRONTIER lookup. Seeing `phase=ship` or
 * `phase=paused_for_user` in the raw stream proves execute
 * finished — the pending execute narration must be resolved.
 *
 * The tests below exercise `resolveStalePendingByFrontier` directly
 * (it's exported from the module) so we don't need to mount the
 * full component. Zero mocks.
 */
import { describe, it, expect } from "vitest";
import {
  resolveStalePendingByFrontier,
  foldNarrations,
} from "../LoopLiveFeed.jsx";


// Helper — mint a raw event that will fold into ONE narration line.
// Event shape matches `extractNarration` in LoopLiveFeed.jsx:
// `{data: {type:"narration", narration_step, narration_text, tone,
//          correlation_id, ts_epoch}}`.
function mkNarrationEvent({ step, text, tone, correlationId, tsEpoch }) {
  return {
    phase: step,
    data: {
      type: "narration",
      tone,
      narration_step: step,
      narration_text: text,
      correlation_id: correlationId,
      ts_epoch: tsEpoch,
    },
  };
}

// Helper — mint a raw phase-only event with no narration payload
// (this is the shape that used to defeat the old frontier logic).
function mkPhaseOnlyEvent({ state, phase, kind }) {
  return {
    state,
    phase,
    data: kind ? { kind } : {},
  };
}


describe("resolveStalePendingByFrontier — Session 6 · Item 4 phase awareness", () => {
  it("resolves pending EXECUTE narration when a ship phase-only event lands", () => {
    // 1. execute-step narration lands in pending tone (LLM was writing)
    // 2. Later: engine emits paused_for_user/ship WITHOUT any ship narration
    //    (this is the founder's real repro)
    // Expected: execute narration should be auto-resolved to success.
    const events = [
      mkNarrationEvent({
        step: "execute",
        text: "Writing README.md",
        tone: "pending",
        correlationId: "c1",
        tsEpoch: 100,
      }),
      mkPhaseOnlyEvent({
        state: "paused_for_user",
        phase: "ship",
        kind: "awaiting_ship",
      }),
    ];
    const folded = foldNarrations(events);
    const resolved = resolveStalePendingByFrontier(folded, events);
    expect(resolved).toHaveLength(1);
    expect(resolved[0].step).toBe("execute");
    expect(resolved[0].tone).toBe("success");
    expect(resolved[0].__resolvedByFrontier).toBe(true);
  });

  it("resolves pending EXECUTE narration when phase=paused_for_user with no explicit ship data", () => {
    // Same class of bug — phase transition alone is enough proof.
    const events = [
      mkNarrationEvent({
        step: "execute",
        text: "Writing api.py",
        tone: "pending",
        correlationId: "c2",
        tsEpoch: 200,
      }),
      mkPhaseOnlyEvent({ state: "paused_for_user", phase: "paused_for_user" }),
    ];
    const folded   = foldNarrations(events);
    const resolved = resolveStalePendingByFrontier(folded, events);
    expect(resolved[0].tone).toBe("success");
    expect(resolved[0].__resolvedByFrontier).toBe(true);
  });

  it("DOES NOT resolve pending execute if no phase advancement happened", () => {
    // A pending execute with nothing after it — must stay pending.
    // Otherwise we'd be lying about a still-in-flight step.
    const events = [
      mkNarrationEvent({
        step: "execute",
        text: "Writing slow-file.py",
        tone: "pending",
        correlationId: "c3",
        tsEpoch: 300,
      }),
    ];
    const folded   = foldNarrations(events);
    const resolved = resolveStalePendingByFrontier(folded, events);
    expect(resolved[0].tone).toBe("pending");
    expect(resolved[0].__resolvedByFrontier).toBeUndefined();
  });

  it("preserves original narration-step frontier behavior", () => {
    // Regression check for the ORIGINAL Iter 331 behavior — a
    // ship-step NARRATION also advances the frontier and resolves
    // stale execute narrations. Same rule the old code enforced.
    const events = [
      mkNarrationEvent({
        step: "execute",
        text: "Writing x.py",
        tone: "pending",
        correlationId: "cA",
        tsEpoch: 400,
      }),
      mkNarrationEvent({
        step: "ship",
        text: "Committed abc123",
        tone: "success",
        correlationId: "cB",
        tsEpoch: 450,
      }),
    ];
    const folded   = foldNarrations(events);
    const resolved = resolveStalePendingByFrontier(folded, events);
    const executeLine = resolved.find((l) => l.step === "execute");
    expect(executeLine.tone).toBe("success");
    expect(executeLine.__resolvedByFrontier).toBe(true);
  });

  it("second call without events arg still works (backwards-compat)", () => {
    // Some legacy tests / call sites may not pass the events arg.
    // The function must gracefully fall back to narration-only.
    const events = [
      mkNarrationEvent({
        step: "execute", text: "x", tone: "pending",
        correlationId: "y", tsEpoch: 500,
      }),
      mkNarrationEvent({
        step: "ship", text: "done", tone: "success",
        correlationId: "z", tsEpoch: 501,
      }),
    ];
    const folded = foldNarrations(events);
    const r1 = resolveStalePendingByFrontier(folded);      // no events arg
    const executeLine = r1.find((l) => l.step === "execute");
    expect(executeLine.tone).toBe("success");
  });

  it("execute narration DOES NOT resolve pending PLAN narration prematurely", () => {
    // Nuance: a phase=paused_for_user/ship event resolves execute,
    // but it also advances past plan → plan pending must resolve too.
    // (Both are < 5.) This mirrors the Iter 331 rule that steps < frontier
    // resolve. Extra guard so a future refactor doesn't break plan too.
    const events = [
      mkNarrationEvent({
        step: "plan",
        text: "Planning changes",
        tone: "pending",
        correlationId: "p1",
        tsEpoch: 600,
      }),
      mkNarrationEvent({
        step: "execute",
        text: "Writing README.md",
        tone: "pending",
        correlationId: "e1",
        tsEpoch: 610,
      }),
      mkPhaseOnlyEvent({ state: "paused_for_user", phase: "ship" }),
    ];
    const folded   = foldNarrations(events);
    const resolved = resolveStalePendingByFrontier(folded, events);
    // Both plan AND execute pendings must be resolved.
    for (const line of resolved) {
      expect(line.tone).toBe("success");
    }
  });
});
