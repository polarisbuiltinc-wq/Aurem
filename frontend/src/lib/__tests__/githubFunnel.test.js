/**
 * githubFunnel.test.js — 2026-08-01
 * Unit tests for the GitHub Connect funnel telemetry helper.
 *
 * Uses vitest + jsdom (already the frontend default per Session 7 tests).
 * We spy on window.fetch to verify the correct payload gets POSTed
 * without hitting the real backend.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  trackFunnel,
  getFunnelSessionId,
  withFunnelParams,
} from "../githubFunnel";

// The lib reads API_BASE from ./api which reads env vars. Vitest env
// sets process.env.REACT_APP_BACKEND_URL from setup; we assert the
// POST hits `/funnel/github/event` (path check, not full URL).

describe("githubFunnel telemetry helper", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("getFunnelSessionId creates a stable c_-prefixed id and reuses it", () => {
    const sid1 = getFunnelSessionId();
    const sid2 = getFunnelSessionId();
    expect(sid1).toBe(sid2);
    expect(sid1.startsWith("c_")).toBe(true);
    expect(sid1.length).toBeGreaterThanOrEqual(16);
  });

  it("trackFunnel POSTs correct payload with keepalive", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true, json: async () => ({ ok: true, event_id: "e_abc" }),
    });

    await trackFunnel("cta_click", "login", { intent: "login" });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchSpy.mock.calls[0];
    expect(url).toContain("/funnel/github/event");
    expect(opts.method).toBe("POST");
    expect(opts.keepalive).toBe(true);

    const body = JSON.parse(opts.body);
    expect(body.stage).toBe("cta_click");
    expect(body.source).toBe("login");
    expect(body.session_id.startsWith("c_")).toBe(true);
    expect(body.meta).toEqual({ intent: "login" });
  });

  it("trackFunnel swallows fetch failures silently", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new Error("network down"));
    // Should NOT throw:
    await expect(trackFunnel("cta_click", "signup")).resolves.toBeUndefined();
  });

  it("withFunnelParams appends fs + fsrc to URL", () => {
    const url = withFunnelParams(
      "https://x.test/api/aurem-dev/github/oauth/connect?signup=1",
      "signup",
    );
    expect(url).toMatch(/[?&]fs=c_/);
    expect(url).toMatch(/[?&]fsrc=signup/);
  });

  it("withFunnelParams handles URL without existing query", () => {
    const url = withFunnelParams("/plain/path", "wizard");
    expect(url).toContain("?fs=");
    expect(url).toContain("&fsrc=wizard");
  });

  it("trackFunnel uses `unknown` when source is omitted", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true, json: async () => ({}),
    });
    await trackFunnel("repo_selected");
    const body = JSON.parse(fetchSpy.mock.calls[0][1].body);
    expect(body.source).toBe("unknown");
    expect(body.meta).toEqual({});
  });
});
