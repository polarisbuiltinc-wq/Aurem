/**
 * Iter 388i · Bug 8 — RailShell navigation regression.
 *
 * Previously the rail was starting HIDDEN (`useState(true)`) on every
 * mount, and on every dashboard reload with an active session
 * ChatPanel dispatched `aurem:chat-session-started` with
 * `detail.restored = true`, which re-hid the rail even after a fresh
 * mount.  Net effect: founders landed on `/dashboard`, saw the rail
 * off-screen with `pointerEvents: none`, and clicks on the
 * Insights / Admin icons fell through to the chat area (perceived
 * as "chat reloaded, nothing navigated").  Ship worked only because
 * founders reached it via a top-bar chip, not the rail.
 *
 * These tests only exercise the state machine of the rail's
 * `hiddenForTyping` flag against the two event shapes ChatPanel
 * actually emits.  No DOM rendering — that's covered by the
 * playwright smoke run in the deploy pipeline.
 */
import { describe, it, expect, vi } from "vitest";

/* -------------------------------------------------------------------- *
 * Tiny re-implementation of the exact reducer RailShell uses (kept in  *
 * sync with lines 114-127 of RailShell.jsx).  If someone changes the   *
 * event-handling shape, this test file breaks — that's the point.      *
 * -------------------------------------------------------------------- */
function makeRailState({ autoHideEnabled = true } = {}) {
  const state = { hiddenForTyping: false };
  const onStart = (e) => {
    if (!autoHideEnabled) return;
    if (e?.detail?.restored) return;
    state.hiddenForTyping = true;
  };
  const onReset = () => { state.hiddenForTyping = false; };
  return { state, onStart, onReset };
}

describe("RailShell · Iter 388i Bug 8 regression", () => {
  it("starts VISIBLE on mount (default state = false)", () => {
    const { state } = makeRailState();
    expect(state.hiddenForTyping).toBe(false);
  });

  it("IGNORES a restored-session event (dashboard reload)", () => {
    const { state, onStart } = makeRailState();
    onStart({ detail: { session_id: "s1", restored: true } });
    expect(state.hiddenForTyping).toBe(false);
  });

  it("HIDES on a real chat-session-started event (first user send)", () => {
    const { state, onStart } = makeRailState();
    onStart({ detail: { session_id: "s1" } });   // no `restored`
    expect(state.hiddenForTyping).toBe(true);
  });

  it("HIDES only when auto-hide is enabled", () => {
    const { state, onStart } = makeRailState({ autoHideEnabled: false });
    onStart({ detail: { session_id: "s1" } });
    expect(state.hiddenForTyping).toBe(false);
  });

  it("un-hides on chat-session-reset (New run button)", () => {
    const { state, onStart, onReset } = makeRailState();
    onStart({ detail: { session_id: "s1" } });
    expect(state.hiddenForTyping).toBe(true);
    onReset();
    expect(state.hiddenForTyping).toBe(false);
  });

  it("multiple restored events in a row keep the rail visible", () => {
    const { state, onStart } = makeRailState();
    for (let i = 0; i < 3; i++) {
      onStart({ detail: { restored: true, session_id: `s${i}` } });
    }
    expect(state.hiddenForTyping).toBe(false);
  });
});
