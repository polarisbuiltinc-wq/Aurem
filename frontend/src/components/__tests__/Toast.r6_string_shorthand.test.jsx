/**
 * R6 (overnight round) — toast() bare-string shorthand.
 *
 * Many call sites across the app do `toast(someString)`. Before this
 * fix, destructuring a string as `{message, kind, ...}` silently
 * produced `message: undefined` (a blank toast bubble) — this test
 * proves the fix: a bare string is now normalized to `{message: str}`.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { toast } from "../Toast";

describe("toast() — bare string shorthand (R6)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("dispatches message = the string when called with a bare string", () => {
    let captured = null;
    window.addEventListener("aurem:toast", (e) => { captured = e.detail; }, { once: true });
    toast("Ship failed to start");
    expect(captured).not.toBeNull();
    expect(captured.message).toBe("Ship failed to start");
  });

  it("still supports the object form unchanged", () => {
    let captured = null;
    window.addEventListener("aurem:toast", (e) => { captured = e.detail; }, { once: true });
    toast({ message: "Object form", kind: "error", actions: [{ label: "Retry" }] });
    expect(captured.message).toBe("Object form");
    expect(captured.kind).toBe("error");
    expect(captured.actions[0].label).toBe("Retry");
  });
});
