/**
 * Iter 389 — Meta Pixel conversion event helpers.
 *
 * Locks in the contract for `metaCompleteRegistration`, `metaLead`,
 * and `metaPurchase` helpers in `lib/analytics.js`:
 *
 *   • Each helper must call `window.fbq('track', <EventName>, params
 *     [, { eventID }])` with the correct standard event name and
 *     required parameter shape.
 *   • When `window.fbq` is undefined (ad-blocker / SSR) every helper
 *     must silently return `false` — never throw.
 *   • `metaPurchase` MUST guardrail against invalid inputs:
 *       - null / NaN / <= 0 value  → skip
 *       - missing / non-string currency → skip
 *     This prevents polluting Meta's ad account with unknown-value
 *     purchase events.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  metaCompleteRegistration,
  metaLead,
  metaPurchase,
} from "../analytics";

describe("Iter 389 — Meta Pixel conversion helpers", () => {
  beforeEach(() => {
    // Reset window.fbq between tests.
    delete window.fbq;
  });

  describe("no-op when fbq is unavailable", () => {
    it("metaCompleteRegistration returns false without throwing", () => {
      expect(() => metaCompleteRegistration("email")).not.toThrow();
      expect(metaCompleteRegistration("email")).toBe(false);
    });

    it("metaLead returns false without throwing", () => {
      expect(() => metaLead("project_added")).not.toThrow();
      expect(metaLead("project_added")).toBe(false);
    });

    it("metaPurchase returns false without throwing", () => {
      expect(() => metaPurchase(9, "USD", "cs_test_1")).not.toThrow();
      expect(metaPurchase(9, "USD", "cs_test_1")).toBe(false);
    });
  });

  describe("metaCompleteRegistration", () => {
    it("fires fbq('track', 'CompleteRegistration', ...) with method", () => {
      const fbq = vi.fn();
      window.fbq = fbq;

      const ok = metaCompleteRegistration("email");

      expect(ok).toBe(true);
      expect(fbq).toHaveBeenCalledTimes(1);
      expect(fbq).toHaveBeenCalledWith("track", "CompleteRegistration", {
        content_name: "email",
        status: true,
      });
    });

    it("passes the OAuth provider through as content_name", () => {
      const fbq = vi.fn();
      window.fbq = fbq;

      metaCompleteRegistration("github");

      expect(fbq).toHaveBeenCalledWith("track", "CompleteRegistration", {
        content_name: "github",
        status: true,
      });
    });
  });

  describe("metaLead", () => {
    it("fires fbq('track', 'Lead', ...) with source", () => {
      const fbq = vi.fn();
      window.fbq = fbq;

      const ok = metaLead("project_added");

      expect(ok).toBe(true);
      expect(fbq).toHaveBeenCalledTimes(1);
      expect(fbq).toHaveBeenCalledWith("track", "Lead", {
        content_name: "project_added",
      });
    });

    it("defaults content_name to 'project_added' when omitted", () => {
      const fbq = vi.fn();
      window.fbq = fbq;

      metaLead();

      expect(fbq).toHaveBeenCalledWith("track", "Lead", {
        content_name: "project_added",
      });
    });
  });

  describe("metaPurchase", () => {
    it("fires fbq('track', 'Purchase', ...) with value + currency + sid dedup", () => {
      const fbq = vi.fn();
      window.fbq = fbq;

      const ok = metaPurchase(19, "USD", "cs_test_pro_1");

      expect(ok).toBe(true);
      expect(fbq).toHaveBeenCalledTimes(1);
      expect(fbq).toHaveBeenCalledWith(
        "track",
        "Purchase",
        {
          value: 19,
          currency: "USD",
          content_ids: ["cs_test_pro_1"],
        },
        { eventID: "cs_test_pro_1" }
      );
    });

    it("still fires without a sid but omits content_ids/eventID", () => {
      const fbq = vi.fn();
      window.fbq = fbq;

      const ok = metaPurchase(9, "USD");

      expect(ok).toBe(true);
      expect(fbq).toHaveBeenCalledWith("track", "Purchase", {
        value: 9,
        currency: "USD",
      });
    });

    it("GUARDRAIL: skips when value is null / NaN / <= 0", () => {
      const fbq = vi.fn();
      window.fbq = fbq;

      expect(metaPurchase(null, "USD", "cs_x")).toBe(false);
      expect(metaPurchase(undefined, "USD", "cs_x")).toBe(false);
      expect(metaPurchase(0, "USD", "cs_x")).toBe(false);
      expect(metaPurchase(-5, "USD", "cs_x")).toBe(false);
      expect(metaPurchase("not-a-number", "USD", "cs_x")).toBe(false);

      expect(fbq).not.toHaveBeenCalled();
    });

    it("GUARDRAIL: skips when currency is missing or non-string", () => {
      const fbq = vi.fn();
      window.fbq = fbq;

      expect(metaPurchase(9, null, "cs_x")).toBe(false);
      expect(metaPurchase(9, undefined, "cs_x")).toBe(false);
      expect(metaPurchase(9, "", "cs_x")).toBe(false);
      expect(metaPurchase(9, 123, "cs_x")).toBe(false);

      expect(fbq).not.toHaveBeenCalled();
    });

    it("swallows exceptions from fbq (never throws to business flow)", () => {
      window.fbq = () => {
        throw new Error("adblock injected");
      };

      expect(() => metaPurchase(9, "USD", "cs_x")).not.toThrow();
      expect(metaPurchase(9, "USD", "cs_x")).toBe(false);
    });
  });
});
