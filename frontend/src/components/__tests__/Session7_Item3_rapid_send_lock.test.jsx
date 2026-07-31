/**
 * Session 7 · Item 3 regression contract — rapid double-send lock.
 *
 * Real-user QA: sending two rapid chat/loop messages back-to-back
 * (~1s gap) triggered the app's own F12 error-capture widget to
 * show "1 console error" → "2 console errors". Root cause: React's
 * `busy` state is async — rapid double-clicks let both send()
 * invocations read `busy=false` from stale closures and both pass
 * the guard, spawning two racing startLoop/chat API calls (second
 * one hits 409 conflict → surfaces on console).
 *
 * Fix: synchronous `sendInFlightRef` ref-lock armed BEFORE any
 * await point in `send()`, released either when `busy` toggles back
 * to false (useEffect on [busy]) or via a 5 s safety timeout.
 *
 * Assertions locked at source level — testing the real double-send
 * behavior end-to-end requires a full ChatPanel mount with mocked
 * axios + SSE, which is disproportionate for what is fundamentally
 * a single-ref-guard contract.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";

const CHAT_PANEL_SRC = fs.readFileSync(
  path.resolve(__dirname, "../ChatPanel.jsx"),
  "utf-8",
);

describe("Session 7 · Item 3 · Rapid-send race guard", () => {
  it("sendInFlightRef is declared", () => {
    expect(CHAT_PANEL_SRC).toContain(
      "const sendInFlightRef = useRef(false)");
  });

  it("send() checks the ref BEFORE any await point", () => {
    // The check must come BEFORE the busy-based guard so a stale
    // closure of `busy=false` still gets blocked.
    expect(CHAT_PANEL_SRC).toMatch(
      /async function send\([\s\S]{0,2000}sendInFlightRef\.current[\s\S]{0,500}dropped rapid duplicate/);
  });

  it("lock is armed AFTER validity guards, not before", () => {
    // Otherwise an invalid send (empty text) would arm the lock
    // and starve the next legitimate send.
    expect(CHAT_PANEL_SRC).toMatch(
      /!sessionId[\s\S]{0,300}sendInFlightRef\.current = true/);
  });

  it("lock releases on busy transition to false via useEffect", () => {
    expect(CHAT_PANEL_SRC).toMatch(
      /useEffect\(\(\) => \{[\s\S]{0,500}!busy[\s\S]{0,400}sendInFlightRef\.current = false/);
  });

  it("belt-and-braces 5 s safety timeout is armed", () => {
    // If a promise crashes without setBusy(false), the safety timer
    // still releases the lock so the next send isn't stuck.
    expect(CHAT_PANEL_SRC).toMatch(
      /sendLockTimeoutRef\.current = setTimeout\([\s\S]{0,300}sendInFlightRef\.current = false[\s\S]{0,80}5000/);
  });

  it("debug log fires on dropped duplicate (aids diagnosis)", () => {
    // Should be a console.debug (NOT error) so we can trace race
    // condition hits in prod without surfacing them as user-visible
    // errors on the F12 widget.
    expect(CHAT_PANEL_SRC).toMatch(
      /console\.debug\(["'`]\[send\] dropped rapid duplicate/);
  });
});
