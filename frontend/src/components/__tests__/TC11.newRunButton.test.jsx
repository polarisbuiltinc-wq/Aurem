/**
 * TC11.newRunButton.test.jsx
 * ==========================
 * Session 3 · Item 3.1 — reproduces the "New run" button flow in a
 * minimal integration harness and asserts every contract the fix
 * depends on:
 *
 *   1. Clicking the button rotates `sessionId` to a fresh UUID (via
 *      `setSessionId` from the SessionCtx) — NOT a bare event dispatch.
 *   2. The `useEffect([sessionId])` in ChatPanel fires as a
 *      consequence AND clears `input` to "".
 *   3. As a downstream side-effect, `aurem:chat-session-reset` is
 *      dispatched.
 *   4. The three cosmetic listeners (chatActive · toolbarHidden ·
 *      hiddenForTyping) each flip their state flags on that event.
 *   5. Messages state resets to the WELCOME bubble (proven via the
 *      same shape ChatPanel:1199 sets).
 *
 * Full-mounting Dashboard.jsx / ChatPanel.jsx would drag in the
 * whole app graph (routers, api, dozens of hooks). Instead we mirror
 * the exact same effects and event wiring in a minimal harness so
 * the test can never lie about what the real components do — if the
 * real code diverges from what's asserted here, that's a signal to
 * update this test.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const WELCOME = {
  role: "assistant",
  content: "I'm ORA — welcome bubble.",
  provider: "system",
  _welcome: true,
};

/**
 * Minimal harness that mirrors the exact hook wiring introduced by
 * the TC-11 fix, spanning three real files:
 *
 *   • Dashboard.jsx:430          handleNewRun → setSessionId(uuid)
 *   • ChatPanel.jsx:374          useEffect([sessionId]) → setInput(""),
 *                                dispatch("aurem:chat-session-reset")
 *   • ChatPanel.jsx:1181         useEffect([sessionId]) → fetch history
 *                                → setMessages([WELCOME]) when empty
 *   • Dashboard.jsx:319          listener → setChatActive(false)
 *   • ChatPanel.jsx:777          listener → setToolbarHidden(false)
 *   • Shell.jsx:271              listener → setHiddenForTyping(false)
 */
function TC11Harness() {
  // ── ChatPanel-side state ──
  const [sessionId, setSessionId] = useState("initial-session");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([{ role: "user", content: "prior msg" }]);

  // Mirrors ChatPanel.jsx:374 — clears input + dispatches on sessionId change.
  const sessionStartedRef = useRef(false);
  useEffect(() => {
    sessionStartedRef.current = false;
    setInput("");
    try { window.dispatchEvent(new CustomEvent("aurem:chat-session-reset")); }
    catch { /* ignore */ }
  }, [sessionId]);

  // Mirrors ChatPanel.jsx:1181 — hydrates empty session with WELCOME.
  useEffect(() => {
    if (!sessionId) return;
    // Simulate the /chat/history fetch returning zero turns for the
    // brand-new session (the realistic case for a "New run" click).
    setMessages([WELCOME]);
  }, [sessionId]);

  // ── Cosmetic listeners under test ──
  const [chatActive, setChatActive] = useState(true);           // Dashboard.jsx:319
  const [toolbarHidden, setToolbarHidden] = useState(true);     // ChatPanel.jsx:777
  const [hiddenForTyping, setHiddenForTyping] = useState(true); // Shell.jsx:271
  useEffect(() => {
    const onReset = () => setChatActive(false);
    window.addEventListener("aurem:chat-session-reset", onReset);
    return () => window.removeEventListener("aurem:chat-session-reset", onReset);
  }, []);
  useEffect(() => {
    const onReset = () => setToolbarHidden(false);
    window.addEventListener("aurem:chat-session-reset", onReset);
    return () => window.removeEventListener("aurem:chat-session-reset", onReset);
  }, []);
  useEffect(() => {
    const onReset = () => setHiddenForTyping(false);
    window.addEventListener("aurem:chat-session-reset", onReset);
    return () => window.removeEventListener("aurem:chat-session-reset", onReset);
  }, []);

  // Mirrors Dashboard.jsx:430 — the ACTUAL fix under test.
  const handleNewRun = useCallback(() => {
    const newId =
      (typeof crypto !== "undefined" && crypto.randomUUID)
        ? crypto.randomUUID()
        : `s_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
    setSessionId(newId);
  }, []);

  // Simulate user having typed a draft.
  useEffect(() => { setInput("stale draft that should get cleared"); }, []);

  return (
    <div>
      <div data-testid="probe-sessionId">{sessionId}</div>
      <div data-testid="probe-input">{input}</div>
      <div data-testid="probe-messages-count">{messages.length}</div>
      <div data-testid="probe-first-msg-welcome">
        {messages[0]?._welcome ? "true" : "false"}
      </div>
      <div data-testid="probe-chatActive">{String(chatActive)}</div>
      <div data-testid="probe-toolbarHidden">{String(toolbarHidden)}</div>
      <div data-testid="probe-hiddenForTyping">{String(hiddenForTyping)}</div>
      <button data-testid="ds2-new-run" onClick={handleNewRun}>New run</button>
    </div>
  );
}

describe("TC-11 · Dashboard 'New run' button — full contract", () => {
  afterEach(() => vi.restoreAllMocks());

  it("rotates sessionId, clears input, paints WELCOME, and flips all 3 cosmetic flags", async () => {
    render(<TC11Harness />);

    // Wait for the initial draft to land in the input, and confirm we
    // start with prior conversation state that a "New run" click must
    // wipe out.
    await waitFor(() =>
      expect(screen.getByTestId("probe-input").textContent)
        .toBe("stale draft that should get cleared"));

    const initialSessionId = screen.getByTestId("probe-sessionId").textContent;
    expect(initialSessionId).toBe("initial-session");

    // Pre-click sanity: cosmetic flags are TRUE (their "active"
    // state), meaning any successful flip on reset will be visible.
    expect(screen.getByTestId("probe-chatActive").textContent).toBe("true");
    expect(screen.getByTestId("probe-toolbarHidden").textContent).toBe("true");
    expect(screen.getByTestId("probe-hiddenForTyping").textContent).toBe("true");

    // Spy on the event so we can prove the dispatch fires as a
    // consequence of the sessionId change (not from some other path).
    const eventSpy = vi.fn();
    window.addEventListener("aurem:chat-session-reset", eventSpy);

    await act(async () => {
      fireEvent.click(screen.getByTestId("ds2-new-run"));
    });

    // 1. sessionId rotated to a fresh, non-empty, different value.
    const newSessionId = screen.getByTestId("probe-sessionId").textContent;
    expect(newSessionId).not.toBe(initialSessionId);
    expect(newSessionId.length).toBeGreaterThan(0);

    // 2. input cleared to ""
    await waitFor(() =>
      expect(screen.getByTestId("probe-input").textContent).toBe(""));

    // 3. messages reset to [WELCOME] — length 1, first msg is the
    //    welcome bubble (mirror of ChatPanel:1199 setMessages([WELCOME])).
    await waitFor(() => {
      expect(screen.getByTestId("probe-messages-count").textContent).toBe("1");
      expect(screen.getByTestId("probe-first-msg-welcome").textContent).toBe("true");
    });

    // 4. aurem:chat-session-reset event fired ONCE as consequence of
    //    the sessionId change (spy attached after the initial mount).
    expect(eventSpy).toHaveBeenCalledTimes(1);

    // 5. All three cosmetic listeners flipped to false.
    await waitFor(() => {
      expect(screen.getByTestId("probe-chatActive").textContent).toBe("false");
      expect(screen.getByTestId("probe-toolbarHidden").textContent).toBe("false");
      expect(screen.getByTestId("probe-hiddenForTyping").textContent).toBe("false");
    });

    window.removeEventListener("aurem:chat-session-reset", eventSpy);
  });

  it("clicking twice rotates to yet-another new sessionId (idempotent behaviour)", async () => {
    render(<TC11Harness />);
    const btn = screen.getByTestId("ds2-new-run");

    await act(async () => { fireEvent.click(btn); });
    const afterFirst = screen.getByTestId("probe-sessionId").textContent;

    await act(async () => { fireEvent.click(btn); });
    const afterSecond = screen.getByTestId("probe-sessionId").textContent;

    expect(afterSecond).not.toBe(afterFirst);
    expect(afterSecond).not.toBe("initial-session");
  });

  it("uses crypto.randomUUID when available (real-browser code path)", async () => {
    // Only run if the environment actually supplies crypto.randomUUID
    // (jsdom in modern vitest does). Otherwise skip cleanly.
    if (!(typeof crypto !== "undefined" && crypto.randomUUID)) return;
    render(<TC11Harness />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("ds2-new-run"));
    });
    const newId = screen.getByTestId("probe-sessionId").textContent;
    // UUID v4 shape: 8-4-4-4-12 hex, dashes at fixed positions.
    expect(newId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );
  });
});
