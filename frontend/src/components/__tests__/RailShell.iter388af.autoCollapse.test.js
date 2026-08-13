/**
 * Iter 388-af (2026-02-14) — sidebar auto-collapse routing fix.
 *
 * Founder feedback: "sidebar in user interface not collapsed after
 * clicking auto too. and not even when starting chatting that time too."
 *
 * TWO ROUTING BUGS were behind this:
 *
 * 1. **Chat send never dispatched `aurem:chat-session-started` in
 *    AUTO/Loop mode.** The dispatch used to live in `send()` AFTER
 *    the `if (execMode === LOOP) { runLoopPlan(); return; }` early
 *    return, so the event fired for plain-chat sends only. Loop mode
 *    (which is what AUTO enables), `/diagram`, and every other
 *    branch that returned early never signalled the rail to collapse.
 *
 * 2. **Toggling AUTO ON mid-conversation didn't collapse the rail.**
 *    RailShell.jsx had `if (!autoHideEnabled) setHiddenForTyping(false)`
 *    — only handled the OFF → show direction. Turning AUTO back ON
 *    while a chat was already active required a fresh message send
 *    before the rail would hide, which is surprising for a pill
 *    labelled "AUTO".
 *
 * These tests lock the routing contract via source inspection so any
 * future refactor that reintroduces either bug fails fast.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const CHAT_PANEL = path.resolve(
  __dirname, "..", "..", "components", "ChatPanel.jsx",
);
const RAIL_SHELL = path.resolve(
  __dirname, "..", "..", "components", "nav", "RailShell.jsx",
);


describe("ChatPanel.send() — Iter 388-af routing", () => {
  const src = fs.readFileSync(CHAT_PANEL, "utf-8");

  it("dispatches chat-session-started BEFORE the /diagram slash branch", () => {
    const dispatchIdx  = src.indexOf('dispatchEvent(new CustomEvent("aurem:chat-session-started"');
    const diagramIdx   = src.indexOf('const slashMatch = text.match(/^\\/diagram');
    expect(dispatchIdx).toBeGreaterThan(0);
    expect(diagramIdx).toBeGreaterThan(0);
    expect(dispatchIdx).toBeLessThan(diagramIdx);
  });

  it("dispatches chat-session-started BEFORE the LOOP-mode early return", () => {
    const dispatchIdx = src.indexOf('dispatchEvent(new CustomEvent("aurem:chat-session-started"');
    const loopIdx     = src.indexOf('if (execMode === EXEC_MODES.LOOP && !opts.loopPhase && !opts.forceChat)');
    expect(dispatchIdx).toBeGreaterThan(0);
    expect(loopIdx).toBeGreaterThan(0);
    expect(dispatchIdx).toBeLessThan(loopIdx);
  });

  it("still gates on sessionStartedRef.current for idempotency", () => {
    // Otherwise every keystroke (via the auto-send retry logic in
    // the LOOP queue path) would re-fire the event.
    expect(src).toMatch(/if \(!sessionStartedRef\.current && promptOverride == null\)/);
  });

  it("guards against internal re-invocations (promptOverride != null)", () => {
    // Plan-approve execute-phase calls send() with a promptOverride —
    // those must NOT re-fire the event. The gate above catches it,
    // but assert it explicitly so a future refactor doesn't drop the
    // clause and cause spurious rail flicker.
    expect(src).toMatch(/promptOverride == null/);
  });
});


describe("RailShell — Iter 388-af AUTO-toggle routing", () => {
  const src = fs.readFileSync(RAIL_SHELL, "utf-8");

  it("tracks session-active state via a ref", () => {
    expect(src).toMatch(/sessionActiveRef\s*=\s*useRef\(false\)/);
  });

  it("marks session active on `chat-session-started` (any restored flag)", () => {
    // Even a restored=true event should update sessionActiveRef so
    // the AUTO-ON toggle can collapse the rail. But it MUST NOT
    // immediately hide the rail (Bug 8 fix — restored=true = landing,
    // needs navigation).
    const onStartMatch = src.match(/const onStart = \(e\) => \{[\s\S]*?\};/);
    expect(onStartMatch, "onStart handler not found").toBeTruthy();
    const body = onStartMatch[0];
    expect(body).toMatch(/sessionActiveRef\.current = true/);
    expect(body).toMatch(/if \(e\?\.detail\?\.restored\) return/);
  });

  it("collapses the rail when AUTO toggles ON during an active session", () => {
    // The mirror to the OFF → show behaviour: ON + already-active
    // session should immediately collapse.
    expect(src).toMatch(
      /else if \(sessionActiveRef\.current\) \{\s*setHiddenForTyping\(true\);/,
    );
  });

  it("still shows the rail when AUTO toggles OFF", () => {
    expect(src).toMatch(/if \(!autoHideEnabled\) \{\s*setHiddenForTyping\(false\);/);
  });

  it("resets sessionActiveRef when the chat session is reset", () => {
    const onResetMatch = src.match(/const onReset = \(\) => \{[\s\S]*?\};/);
    expect(onResetMatch, "onReset handler not found").toBeTruthy();
    const body = onResetMatch[0];
    expect(body).toMatch(/sessionActiveRef\.current = false/);
    expect(body).toMatch(/setHiddenForTyping\(false\)/);
  });
});
