/**
 * OraImageSlash.phase5.test.jsx — Feb 2026 · Phase 5
 *
 * Static contract check on the `/image <prompt>` client-side
 * slash command wired into pages/OraDirect.jsx.  Runtime E2E is
 * exercised on preview via Playwright; this suite locks the
 * safety invariants that we don't want to regress silently:
 *
 *   1. The slash regex accepts `/image` AND `/image-gen`, requires
 *      at least one non-empty prompt char.
 *   2. It POSTs to `/image-generate` (NOT the generic /message SSE
 *      endpoint) so the founder-tier + $3 daily + 10/mo caps fire.
 *   3. Success path renders the returned base64 as a data-URL
 *      inline in a normal assistant Bubble (no bespoke component).
 *   4. Error responses show the structured `error` code + message —
 *      no silent fail, no "network error" for a real 402/429.
 *   5. The quota status line ("N/10 images used this month · $x/$3
 *      today") is surfaced under every generated image so the
 *      founder never has to guess remaining capacity.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const SRC = readFileSync(
  join(process.cwd(), "src/pages/OraDirect.jsx"),
  "utf-8",
);

describe("Phase 5 · /image slash command wiring", () => {
  it("matches /image AND /image-gen prefixes, requires a prompt", () => {
    expect(SRC).toContain("/^\\/image(?:-gen)?\\s+([\\s\\S]+)$/i");
  });

  it("hits POST /ora-chat/image-generate (not /message SSE)", () => {
    // If this ever drifts to /message, the founder-tier / daily-cap
    // gates would be bypassed.
    expect(SRC).toContain("`${BASE}/image-generate`");
    expect(SRC).toContain('method: "POST"');
  });

  it("renders the returned base64 as an inline data URL", () => {
    expect(SRC).toContain('`data:${j.mime || "image/png"};base64,${j.image_base64}`');
  });

  it("surfaces structured error kind + message on non-2xx", () => {
    // Users must see the SPECIFIC error (e.g. "daily_cap_reached"),
    // not a generic "Image generation failed".
    expect(SRC).toContain("detail?.error || `HTTP_${r.status}`");
    expect(SRC).toContain("Image generation blocked");
  });

  it("appends a quota status line under every generated image", () => {
    expect(SRC).toContain("images used this month");
    expect(SRC).toContain("today.");
  });

  it("uses setSending guard so slash-image can't overlap a live send", () => {
    // The slash handler sits inside the same `send()` and returns
    // early — verify the outer send() has a `if (... sending) return`.
    expect(SRC).toMatch(/sending\)\s*return/);
  });
});
