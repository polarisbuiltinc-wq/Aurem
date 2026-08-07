/**
 * OraPreviewPanel.phase2_security.test.jsx — Feb 2026 · Phase 2
 *
 * Locks in the security contract that the founder brief lists as
 * non-negotiable for the srcdoc preview panel:
 *
 *   1. iframe sandbox="allow-scripts" ONLY.  Never combined with
 *      allow-same-origin.
 *   2. Strict CSP <meta http-equiv> injected into the srcdoc,
 *      including `connect-src 'none'` (anti-exfil).
 *   3. Vanguard scan gates render — CRITICAL findings block.
 *   4. 300 ms debounce before Vanguard is invoked.
 *   5. 16 MB size cap on the client (never round-trip a 17MB blob).
 *
 * We stub `api.post` so no real backend call is made.
 */
import React from "react";
import { render, cleanup, screen, act } from "@testing-library/react";
import { describe, it, expect, afterEach, vi, beforeEach } from "vitest";

// Mock the api module BEFORE importing the component under test.
vi.mock("../../lib/api", () => ({
  api: {
    post: vi.fn(async (_url, _body) => ({
      data: { ok: true, renderable: true, safe: true,
              blockers: [], warnings: [] },
    })),
  },
}));

import OraPreviewPanel from "../OraPreviewPanel";
import { api } from "../../lib/api";

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  cleanup();
});

async function advance(ms) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

describe("Phase 2 · OraPreviewPanel security contract", () => {
  it("renders iframe with sandbox=\"allow-scripts\" only (no allow-same-origin)", async () => {
    render(<OraPreviewPanel code="<h1>hi</h1>" lang="html" onClose={() => {}} />);
    await advance(400);   // > 300ms debounce
    await advance(50);    // let the async scan promise resolve
    const iframe = document.querySelector('[data-testid="ora-preview-iframe"]');
    expect(iframe).not.toBeNull();
    const sandbox = iframe.getAttribute("sandbox") || "";
    expect(sandbox).toBe("allow-scripts");
    // Explicit anti-regression on the exact string the brief bans.
    expect(sandbox.includes("allow-same-origin")).toBe(false);
  });

  it("injects strict CSP with connect-src 'none' into the srcdoc", async () => {
    render(<OraPreviewPanel code="<h1>hi</h1>" lang="html" onClose={() => {}} />);
    await advance(400);
    await advance(50);
    const iframe = document.querySelector('[data-testid="ora-preview-iframe"]');
    const srcdoc = iframe.getAttribute("srcdoc") || "";
    expect(srcdoc).toContain('http-equiv="Content-Security-Policy"');
    expect(srcdoc).toContain("connect-src 'none'");
    expect(srcdoc).toContain("default-src 'none'");
    expect(srcdoc).toContain("frame-ancestors 'none'");
  });

  it("HTML previews do NOT allow unpkg.com in script-src (tighter per-lang CSP)", async () => {
    render(<OraPreviewPanel code="<h1>hi</h1>" lang="html" onClose={() => {}} />);
    await advance(400);
    await advance(50);
    const srcdoc = document.querySelector('[data-testid="ora-preview-iframe"]')
      .getAttribute("srcdoc") || "";
    // HTML preview should have NO external host in script-src AND
    // no 'unsafe-eval' — it doesn't need either.
    expect(srcdoc).toContain("script-src 'unsafe-inline';");
    expect(srcdoc.includes("https://unpkg.com")).toBe(false);
    expect(srcdoc.includes("'unsafe-eval'")).toBe(false);
  });

  it("JSX previews DO allow unpkg.com AND 'unsafe-eval' — required for Babel+new Function", async () => {
    render(<OraPreviewPanel code="const App = () => <h1>hi</h1>;" lang="jsx"
                             onClose={() => {}} />);
    await advance(400);
    await advance(50);
    const srcdoc = document.querySelector('[data-testid="ora-preview-iframe"]')
      .getAttribute("srcdoc") || "";
    // Both grants are required for JSX rendering to actually work.
    expect(srcdoc).toContain("'unsafe-eval'");
    expect(srcdoc).toContain("https://unpkg.com");
  });

  it("HIGH-severity Vanguard findings BLOCK render until user click-through", async () => {
    api.post.mockResolvedValueOnce({
      data: {
        ok: true, renderable: true, safe: true,
        blockers: [],
        warnings: [{ name: "innerHTML_assignment", severity: "HIGH",
                       line: 3, snippet: "el.innerHTML = x" }],
      },
    });
    render(<OraPreviewPanel code="el.innerHTML = x" lang="js"
                             onClose={() => {}} />);
    await advance(400);
    await advance(50);
    // No iframe yet — waiting for founder ack.
    expect(document.querySelector('[data-testid="ora-preview-iframe"]')).toBeNull();
    // Ack banner is up with a clickable "Preview anyway" button.
    expect(screen.getByTestId("ora-preview-high-ack")).toBeTruthy();
    const ackBtn = screen.getByTestId("ora-preview-ack-btn");
    await act(async () => { ackBtn.click(); });
    // Now the iframe builds.
    expect(document.querySelector('[data-testid="ora-preview-iframe"]')).not.toBeNull();
  });

  it("does NOT invoke the Vanguard scan until debounce elapses", async () => {
    render(<OraPreviewPanel code="<h1>hi</h1>" lang="html" onClose={() => {}} />);
    // Before 300ms: no POST yet.
    await advance(100);
    expect(api.post).not.toHaveBeenCalled();
    // Cross the 300ms threshold.
    await advance(300);
    expect(api.post).toHaveBeenCalledTimes(1);
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe("/ora-chat/preview-scan");
    expect(body.lang).toBe("html");
  });

  it("refuses to render when Vanguard returns safe=false (CRITICAL blocker)", async () => {
    api.post.mockResolvedValueOnce({
      data: {
        ok: true, renderable: true, safe: false,
        blockers: [{ name: "eval_usage", severity: "CRITICAL",
                       line: 1, snippet: "eval('...')" }],
        warnings: [],
      },
    });
    render(<OraPreviewPanel code="eval('boom')" lang="js" onClose={() => {}} />);
    await advance(400);
    await advance(50);
    // No iframe rendered.
    expect(document.querySelector('[data-testid="ora-preview-iframe"]')).toBeNull();
    // Blocker banner is up.
    expect(screen.getByTestId("ora-preview-blocked")).toBeTruthy();
  });

  it("rejects payloads above the 16MB client cap without ever POSTing", async () => {
    // 16 MB + 1 byte of 'a'.
    const oversized = "a".repeat(16 * 1024 * 1024 + 1);
    render(<OraPreviewPanel code={oversized} lang="html" onClose={() => {}} />);
    await advance(400);
    await advance(50);
    // Scan endpoint MUST NOT have been called for oversized payload.
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.getByTestId("ora-preview-scan-error")).toBeTruthy();
  });
});
