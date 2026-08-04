/**
 * ChatPanel.verify_retry_counter_live_sync.test.jsx — Feb 2026
 *
 * Founder-reported bug: "Self-heal retry counter UI mein stale hai —
 * heal X/2 chip hamesha heal 1/2 pe stuck reh raha hai, chahe backend
 * mein actual retry count usse zyada ho chuka ho".
 *
 * Root cause: `loopVerifyRetryCount` was only updated from
 * `paused_for_user` events carrying `data.verify_retry_count`. But
 * auto-retries (independent-verifier rejection re-execute cascades,
 * Ship-block re-executes) never touched that field → chip fell back
 * to "heal ${retryCount}/2" forever.
 *
 * Fix: increment client-side on EVERY terminal "Verify failed after
 * N attempts" narration (step="verify", tone="danger", correlation_id
 * "verify:final"). This is a lock-in test asserting the regex + guard
 * shape survives future refactors.
 */
import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

describe("ChatPanel — verify retry counter live-sync", () => {
  const src = fs.readFileSync(
    path.resolve(__dirname, "../ChatPanel.jsx"),
    "utf-8",
  );

  it("increments loopVerifyRetryCount on every verify-danger narration", () => {
    // The handler must guard on narration type + step + tone AND
    // match the "Verify failed after N attempts" text.
    expect(src).toContain('data.type === "narration"');
    expect(src).toContain('narration_step || "") === "verify"');
    expect(src).toContain('tone || "") === "danger"');
    expect(src).toContain("/verify failed after \\d+ attempts/i");
    expect(src).toContain(
      "setLoopVerifyRetryCount((prev) => Math.min(prev + 1, 99))",
    );
  });

  it("also reads verify_retry_count from paused_for_user (existing path)", () => {
    // Regression guard — the pause-response mirror must still work
    // alongside the new narration-driven increment.
    expect(src).toContain('typeof data.verify_retry_count === "number"');
    expect(src).toContain("setLoopVerifyRetryCount(data.verify_retry_count)");
  });

  it("checks all three text fields for the failed-attempts pattern", () => {
    // The narration text can be delivered on ev.message, data.text,
    // OR data.narration_text — regex must scan all three.
    expect(src).toContain("ev.message");
    expect(src).toContain("data.narration_text");
    expect(src).toContain("data.text");
  });
});
