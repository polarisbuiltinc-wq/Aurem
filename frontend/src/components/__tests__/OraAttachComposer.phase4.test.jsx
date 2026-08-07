/**
 * OraAttachComposer.phase4.test.jsx — Feb 2026 · Phase 4
 *
 * Static contract check on the drag-drop + tier-gated attachment
 * composer wired into pages/OraDirect.jsx.  The full flow (real
 * FormData POST, real MarkItDown, real vision) is exercised
 * end-to-end on preview via Playwright — this suite locks the
 * public data-testids and the send-time markdown-prefix contract.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const SRC = readFileSync(
  join(process.cwd(), "src/pages/OraDirect.jsx"),
  "utf-8",
);

describe("Phase 4 · Attach + Vision composer wiring", () => {
  it("exposes the paperclip button + hidden file input", () => {
    expect(SRC).toContain('data-testid="ora-attach-btn"');
    expect(SRC).toContain('data-testid="ora-file-input"');
    expect(SRC).toContain('type="file"');
    expect(SRC).toContain("multiple");
  });

  it("renders per-attachment pill with a status-specific testid", () => {
    expect(SRC).toContain('`ora-attachment-pill-${a.status}`');
  });

  it("shows a drop hint while the user is dragging files over", () => {
    expect(SRC).toContain('data-testid="ora-drop-hint"');
    expect(SRC).toContain("dragActive");
  });

  it("surfaces 402 tier_locked as a persistent upgrade banner", () => {
    expect(SRC).toContain('data-testid="ora-attach-tier-locked"');
    expect(SRC).toContain('data-testid="ora-attach-upgrade-link"');
    expect(SRC).toContain('data-testid="ora-attach-tier-dismiss"');
  });

  it("prepends ATTACHMENT blocks to the outbound message", () => {
    // The LLM MUST see attachments as clearly framed blocks so it
    // doesn't confuse doc contents with the user's own words.
    expect(SRC).toContain("IMAGE ATTACHMENT");
    expect(SRC).toContain("DOCUMENT ATTACHMENT");
    // outbound (not text) is what goes to the /message endpoint.
    expect(SRC).toContain("content: outbound");
  });

  it("only sends attachments in the 'ready' state (skips uploading + errored)", () => {
    expect(SRC).toContain('attachments.filter(a => a.status === "ready")');
  });

  it("hits POST /ora-chat/upload (not /upload/convert) so tier-gate applies", () => {
    // The generic /upload/convert has a 25MB cap and no tier gate —
    // this composer MUST use the tighter /ora-chat/upload path.
    expect(SRC).toContain('`${BASE}/upload`');
  });
});
