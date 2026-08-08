/**
 * OraDirect.intent_casual_chat.test.jsx
 * ======================================
 * Session 3 · 3.3 — Frontend contract guard for the new CASUAL_CHAT
 * intent label.
 *
 * The intent chip + Loop CTA in OraDirect.jsx (lines 1285-1310)
 * previously rendered whenever `m.intent && m.intent !== "UNKNOWN"`.
 * After the backend change added a THIRD label (CASUAL_CHAT), that
 * guard would have wrongly rendered a "preview only" chip for
 * greetings/thanks/casual chat — same bad UX as pre-fix, different
 * code path. The fix adds `&& m.intent !== "CASUAL_CHAT"` so casual
 * messages get no chip and no Loop CTA.
 *
 * These tests lock that contract in.
 */
import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

/**
 * Extracted-in-test copy of the exact render logic from
 * OraDirect.jsx:1285-1310. Kept in-lockstep with the real component
 * — if the real logic diverges from this, that's a signal to update
 * this test, not to work around it.
 */
function IntentChip({ intent, isUser, streaming }) {
  if (isUser) return null;
  if (streaming) return null;
  if (!intent) return null;
  if (intent === "UNKNOWN") return null;
  if (intent === "CASUAL_CHAT") return null;  // ← the fix under test
  return (
    <div data-testid={`ora-intent-${intent}`}>
      <span>{intent === "CODE_CHANGE" ? "code change" : "preview only"}</span>
      {intent === "CODE_CHANGE" && (
        <span data-testid="ora-code-change-hint">
          Want ORA to actually make this change? Start a loop run from the dashboard.
        </span>
      )}
    </div>
  );
}

describe("OraDirect intent chip · CASUAL_CHAT contract", () => {
  it("CASUAL_CHAT renders NO intent chip and NO Loop CTA", () => {
    const { container } = render(
      <IntentChip intent="CASUAL_CHAT" isUser={false} streaming={false} />,
    );
    // No chip at all — not "preview only", not "code change".
    expect(screen.queryByTestId("ora-intent-CASUAL_CHAT")).toBeNull();
    expect(screen.queryByTestId("ora-intent-PREVIEW_ONLY")).toBeNull();
    expect(screen.queryByTestId("ora-intent-CODE_CHANGE")).toBeNull();
    // No Loop CTA hint.
    expect(screen.queryByTestId("ora-code-change-hint")).toBeNull();
    // And the container is genuinely empty — no stray fallback text.
    expect(container.textContent).toBe("");
  });

  it("PREVIEW_ONLY still renders its chip (regression guard)", () => {
    render(<IntentChip intent="PREVIEW_ONLY" isUser={false} streaming={false} />);
    expect(screen.getByTestId("ora-intent-PREVIEW_ONLY")).toBeInTheDocument();
    expect(screen.getByText("preview only")).toBeInTheDocument();
    // But no Loop CTA — only CODE_CHANGE gets that.
    expect(screen.queryByTestId("ora-code-change-hint")).toBeNull();
  });

  it("CODE_CHANGE still renders its chip AND the Loop CTA (regression guard)", () => {
    render(<IntentChip intent="CODE_CHANGE" isUser={false} streaming={false} />);
    expect(screen.getByTestId("ora-intent-CODE_CHANGE")).toBeInTheDocument();
    expect(screen.getByText("code change")).toBeInTheDocument();
    expect(screen.getByTestId("ora-code-change-hint")).toBeInTheDocument();
  });

  it("UNKNOWN still renders nothing (regression guard)", () => {
    const { container } = render(
      <IntentChip intent="UNKNOWN" isUser={false} streaming={false} />,
    );
    expect(container.textContent).toBe("");
  });

  it("user messages never get an intent chip regardless of intent", () => {
    // Even if the LLM (weirdly) attached CODE_CHANGE to a user turn,
    // the isUser guard must suppress it.
    const { container } = render(
      <IntentChip intent="CODE_CHANGE" isUser={true} streaming={false} />,
    );
    expect(container.textContent).toBe("");
  });

  it("streaming messages never get an intent chip until final", () => {
    const { container } = render(
      <IntentChip intent="CODE_CHANGE" isUser={false} streaming={true} />,
    );
    expect(container.textContent).toBe("");
  });
});
