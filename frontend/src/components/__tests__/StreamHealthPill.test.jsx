/**
 * StreamHealthPill.test.jsx — Iter 302 (Frontend QA Charter Layer 1 audit)
 *
 * State-sync behavior tests — same 3-test template as
 * LoopStepBar.test.jsx (iter294) / AgentStatusBar.test.jsx (iter295) /
 * IntentTierIndicator.test.jsx (iter296).
 *
 * StreamHealthPill is the tiny status pill that surfaces when the
 * `/chat/stream` SSE stalls — driven purely by the `state.phase`
 * prop (idle / slow / reconnecting). The invariants this file locks:
 *
 *   1. `state.phase !== "idle"` renders the pill with a reassuring
 *      rotating phrase — NEVER raw numbers ("Ns silent", "auto-retry
 *      in Ms") or the literal words "slow"/"reconnecting" (2026-08-22
 *      — masking how slow the system actually is is intentional).
 *   2. Transition back to `idle` in a same-instance rerender REMOVES
 *      the pill entirely — no stale text left behind.
 *   3. Race-condition: `state.phase === "idle"` (or nullish state)
 *      returns null unconditionally; NO combination of other state
 *      fields can force the pill to render.
 *   4. There is no manual "Retry now" button — retries are fully
 *      automatic now (2026-08-22).
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import StreamHealthPill from "../chat/StreamHealthPill.jsx";


describe("StreamHealthPill — masked-copy behavior (2026-08-22)", () => {
  it("renders a reassuring phrase, never raw numbers or 'slow'/'reconnecting' text, and flips data-stream-phase in one render", () => {
    const { rerender } = render(
      <StreamHealthPill state={{ phase: "slow", silentFor: 8, retryEtaSec: 4 }} />
    );
    const pill = screen.getByTestId("chat-stream-health-pill");
    expect(pill.getAttribute("data-stream-phase")).toBe("slow");
    expect(pill.textContent).not.toMatch(/slow response/i);
    expect(pill.textContent).not.toContain("8s silent");
    expect(pill.textContent).not.toMatch(/auto-retry in 4s/i);
    expect(screen.getByTestId("chat-stream-health-phrase").textContent.length).toBeGreaterThan(0);

    rerender(
      <StreamHealthPill state={{ phase: "reconnecting", silentFor: 12 }} />
    );
    const pill2 = screen.getByTestId("chat-stream-health-pill");
    expect(pill2.getAttribute("data-stream-phase")).toBe("reconnecting");
    expect(pill2.textContent).not.toMatch(/reconnecting/i);
    expect(screen.queryByText(/12s silent/i)).toBeNull();
  });

  it("clears-stale-prior-state: transitioning to phase='idle' removes the pill entirely", () => {
    const { rerender, container } = render(
      <StreamHealthPill state={{ phase: "slow", silentFor: 5 }} />
    );
    expect(screen.getByTestId("chat-stream-health-pill")).toBeInTheDocument();

    rerender(<StreamHealthPill state={{ phase: "idle" }} />);
    expect(screen.queryByTestId("chat-stream-health-pill")).toBeNull();
    expect(container.firstChild).toBeNull();
  });

  it("race-condition: idle/null state is the sole gate — no other prop combination forces render", () => {
    const { container: c1 } = render(
      <StreamHealthPill state={{ phase: "idle", silentFor: 99, retryEtaSec: 99 }} />
    );
    expect(c1.firstChild).toBeNull();

    const { container: c2 } = render(<StreamHealthPill state={null} />);
    expect(c2.firstChild).toBeNull();

    const { container: c3 } = render(<StreamHealthPill state={undefined} />);
    expect(c3.firstChild).toBeNull();
  });

  it("no manual retry button is rendered — retries are fully automatic (2026-08-22)", () => {
    render(
      <StreamHealthPill state={{ phase: "reconnecting", silentFor: 30 }} />
    );
    expect(screen.queryByTestId("chat-stream-retry-now")).toBeNull();
    expect(screen.queryByText(/retry now/i)).toBeNull();
  });
});
