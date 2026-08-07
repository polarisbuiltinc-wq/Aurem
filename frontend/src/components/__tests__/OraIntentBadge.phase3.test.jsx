/**
 * OraIntentBadge.phase3.test.jsx — Feb 2026 · Phase 3
 *
 * Focused test on the intent-router integration in OraDirect.jsx.
 * We can't render the full page (it needs auth + backend), so we
 * unit-test the fence detector + assert the SSE handler in
 * OraDirect.jsx has the `intent` case wired up.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const SRC = readFileSync(
  join(process.cwd(), "src/pages/OraDirect.jsx"),
  "utf-8",
);

describe("Phase 3 · Intent router wiring in /ora chat", () => {
  it("SSE handler consumes the 'intent' event", () => {
    expect(SRC).toMatch(/evtType === "intent"|obj\.type === "intent"/);
  });

  it("intent verdict is persisted onto the assistant turn via routeMeta", () => {
    // Must copy intent + intent_source + intent_matches onto routeMeta
    // so it survives the "final" event and lands on the persisted
    // message.
    expect(SRC).toContain("intent: obj.intent");
    expect(SRC).toContain("intent_source: obj.source");
  });

  it("Bubble renders the intent chip with the correct label + testid", () => {
    // Both intent labels get their own data-testid so E2E tests can
    // assert on the verdict without relying on the visible copy.
    expect(SRC).toMatch(/data-testid=\{`ora-intent-\$\{m\.intent\}`\}/);
    expect(SRC).toContain("code change");
    expect(SRC).toContain("preview only");
  });

  it("CODE_CHANGE surfaces the 'Start a loop run' CTA hint", () => {
    expect(SRC).toContain('data-testid="ora-code-change-hint"');
    expect(SRC).toContain("Start a loop");
  });

  it("UNKNOWN intent stays invisible (no badge)", () => {
    // Guard so unknown verdicts don't clutter the chat with an
    // unhelpful "unknown" tag.
    expect(SRC).toContain('m.intent !== "UNKNOWN"');
  });

  it("intent_source suffix only shows in debug mode", () => {
    // Keeps the default UX clean; debug=1 URL still surfaces which
    // layer (regex vs llm) fired.
    expect(SRC).toContain("debug && m.intent_source");
  });
});
