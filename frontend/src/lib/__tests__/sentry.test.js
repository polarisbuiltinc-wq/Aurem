/**
 * lib/__tests__/sentry.test.js — Iter 388-p1
 *
 * Contract-level tests for the Sentry wiring shim.  Priority is
 * proving the NO-OP path is safe when DSN is missing, so nothing
 * crashes for the founder before they paste their DSN.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock @sentry/react so tests never touch the network.
vi.mock("@sentry/react", () => {
  const captureException = vi.fn();
  const init = vi.fn();
  return {
    default: {},
    init,
    captureException,
    browserTracingIntegration: () => "browserTracingIntegration",
    replayIntegration: () => "replayIntegration",
    ErrorBoundary: function DummyBoundary(_props) { return null; },
  };
});

// Get a fresh module import each test so the internal `_initialized`
// flag doesn't leak.
async function reload() {
  vi.resetModules();
  return await import("../sentry.js");
}

describe("initSentry", () => {
  const origHref = { hostname: window?.location?.hostname };
  beforeEach(() => {
    delete process.env.REACT_APP_SENTRY_DSN;
    vi.clearAllMocks();
  });
  afterEach(() => {
    // no-op — process.env cleanup handled per-test
  });

  it("returns false and does NOT crash when DSN is missing", async () => {
    const mod = await reload();
    expect(mod.initSentry()).toBe(false);
    const Sentry = await import("@sentry/react");
    expect(Sentry.init).not.toHaveBeenCalled();
  });

  it("returns true and calls Sentry.init when DSN is present", async () => {
    process.env.REACT_APP_SENTRY_DSN = "https://abc@o1.ingest.sentry.io/1";
    const mod = await reload();
    expect(mod.initSentry()).toBe(true);
    const Sentry = await import("@sentry/react");
    expect(Sentry.init).toHaveBeenCalledOnce();
    const cfg = Sentry.init.mock.calls[0][0];
    expect(cfg.dsn).toBe("https://abc@o1.ingest.sentry.io/1");
    expect(cfg.tracesSampleRate).toBe(0.1);
    expect(cfg.replaysSessionSampleRate).toBe(0.0);
    expect(cfg.replaysOnErrorSampleRate).toBe(1.0);
  });

  it("trims whitespace-only DSN and treats as missing", async () => {
    process.env.REACT_APP_SENTRY_DSN = "   ";
    const mod = await reload();
    expect(mod.initSentry()).toBe(false);
  });

  it("is idempotent — second call after success is a no-op", async () => {
    process.env.REACT_APP_SENTRY_DSN = "https://abc@o1.ingest.sentry.io/1";
    const mod = await reload();
    mod.initSentry();
    mod.initSentry();
    const Sentry = await import("@sentry/react");
    expect(Sentry.init).toHaveBeenCalledOnce();
  });
});

describe("reportSentryException", () => {
  beforeEach(() => {
    delete process.env.REACT_APP_SENTRY_DSN;
    vi.clearAllMocks();
  });

  it("no-ops silently when Sentry never initialised", async () => {
    const mod = await reload();
    // Must not throw even though init was skipped.
    expect(() => mod.reportSentryException(new Error("test"))).not.toThrow();
    const Sentry = await import("@sentry/react");
    expect(Sentry.captureException).not.toHaveBeenCalled();
  });

  it("forwards to Sentry.captureException when initialised", async () => {
    process.env.REACT_APP_SENTRY_DSN = "https://abc@o1.ingest.sentry.io/1";
    const mod = await reload();
    mod.initSentry();
    const err = new Error("boom");
    mod.reportSentryException(err, { component_stack: "X" });
    const Sentry = await import("@sentry/react");
    expect(Sentry.captureException).toHaveBeenCalledOnce();
    expect(Sentry.captureException.mock.calls[0][0]).toBe(err);
    expect(Sentry.captureException.mock.calls[0][1]).toEqual({
      extra: { component_stack: "X" },
    });
  });
});
