/**
 * MessageBubble.k1_ship_fallback.test.jsx — Round-2 PR (P0-1, K1).
 *
 * MessageBubble is a large, deeply-wired component (matches the
 * existing convention in MessageBubble.p5.test.jsx: test the
 * exported pure functions rather than mounting the full tree, which
 * needs a large mocked prop/context surface).
 *
 * These tests lock the K1 fallback DECISION logic
 * (detectShipCtaFallback) plus the trackShipRenderFailed analytics
 * wrapper it feeds. MessageBubble.jsx wires both together in a
 * useEffect (fires once per message, guarded by a ref) — the render
 * itself is `{shipCtaFallback && <div data-testid="ship-cta-fallback-…">}`
 * placed immediately before <ShipDialog>.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { detectShipCtaFallback } from "../MessageBubble.jsx";
import { trackShipRenderFailed } from "../../lib/analytics.js";

describe("K1 — ship CTA render-failure fallback (Round-2 P0-1)", () => {
  beforeEach(() => {
    window.gtag = vi.fn();
  });

  it("t_k1_fallback_renders_and_logs: CTA mentioned in prose, no fence -> fallback fires + event logs has_fence:false", () => {
    const prose = "I found the bug. Click the Approve the fix button to commit it.";
    const result = detectShipCtaFallback(prose);
    expect(result.shouldFallback).toBe(true);
    expect(result.hasFence).toBe(false);

    trackShipRenderFailed({ message_id: "m1", model: "gpt", has_fence: result.hasFence });
    expect(window.gtag).toHaveBeenCalledWith(
      "event",
      "chat_ship_render_failed",
      expect.objectContaining({ message_id: "m1", model: "gpt", has_fence: false }),
    );
  });

  it("t_k1_real_fence_renders_button (regression): a message with an attempted fence still flags has_fence:true, but MessageBubble only shows the fallback when extractHandoffBrief() actually failed — a message whose fence PASSES all 7 gates never reaches this check (handoffBrief is truthy, so the fallback branch is skipped entirely)", () => {
    const withFence = "Here's the plan.\n```aurem-handoff\nFix the null check in Signup.jsx line 42.\n```";
    const result = detectShipCtaFallback(withFence);
    // Fence marker present -> hasFence true. MessageBubble only calls
    // detectShipCtaFallback() when handoffBrief is null (real button
    // path short-circuits before this check ever runs), so a passing
    // fence never triggers the fallback in the live component.
    expect(result.hasFence).toBe(true);
  });

  it("t_k1_shipped_task_no_rerender (old bug stays dead): plain conversational text with no CTA mention and no fence never flags", () => {
    const plain = "Sure, I can help with that. What file should I look at first?";
    const result = detectShipCtaFallback(plain);
    expect(result.shouldFallback).toBe(false);
    expect(result.hasFence).toBe(false);
  });

  it("does not flag ordinary messages that never mention shipping at all", () => {
    const result = detectShipCtaFallback("The tests are passing now.");
    expect(result.shouldFallback).toBe(false);
  });
});
