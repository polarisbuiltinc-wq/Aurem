/**
 * 2026-08-19 fix — "Manage billing" button disabled-condition bug.
 *
 * Bug: `disabled={isCurrent || !t.paid || busy === t.id}` disabled the
 * button whenever it's the user's current plan — INCLUDING the
 * current-paid-plan case, where the label reads "Manage billing" and
 * a click should open the real Stripe billing portal (openPortal()).
 * Only the current-FREE-plan case (nothing to manage) should stay
 * disabled. Verified live against real Stripe in preview (see PRD.md
 * 2026-08-19 entry) — backend logs showed the real
 * GET /v1/subscriptions/<id> call fire once this landed.
 */
import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const SRC = fs.readFileSync(
  path.resolve(__dirname, "../PricingCards.jsx"), "utf-8",
);

describe("PricingCards manage-billing button", () => {
  it("disabled condition no longer blocks the current-paid-tier case", () => {
    // The exact old buggy condition must be gone.
    expect(SRC).not.toContain("disabled={isCurrent || !t.paid || busy === t.id}");
    // The fixed condition — only current+free (nothing to manage) or
    // an in-flight request should disable the button.
    expect(SRC).toContain("disabled={(isCurrent && !t.paid) || busy === t.id}");
  });

  it("cursor/opacity styling already matched the fixed logic (no regression there)", () => {
    expect(SRC).toContain('(isCurrent && !t.paid) || busy === t.id ? "default" : "pointer"');
  });
});
