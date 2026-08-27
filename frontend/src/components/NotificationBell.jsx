/**
 * NotificationBell.jsx — Cockpit-Bell UI (Feb 2026 · Bell-1 + Bell-2 pass)
 *
 * Short-polls /admin/status/notifications every 12s (env-tunable via
 * REACT_APP_BELL_POLL_MS). Renders a badge with the unread count and
 * a dropdown listing newest-first transitions. RED-only badge — gray
 * transitions never touch this UI per the founder's 3-state discipline.
 *
 * Feb 2026 · Bell-1: clicking a specific notification row now marks
 * THAT row as read (previously only "Mark all read" worked). Badge
 * updates optimistically before the POST completes, then reconciles
 * with the authoritative server unread_count returned in the same
 * response — no waiting for the next 12s poll cycle to see the drop.
 *
 * Feb 2026 · Bell-2: plays a short beep on the FIRST poll cycle where
 * unread_count genuinely increased vs the last known value. Never
 * fires on repeat polls while a notification stays unread (no spam).
 * Browsers block autoplay until a user gesture; sound is gated behind
 * a one-click "🔊 enable alerts" toggle in the dropdown that also
 * plays a test beep to unlock the AudioContext.
 *
 * Zero mocks — everything from the real /admin/status/* endpoints.
 */
import React, { useEffect, useRef, useState, useCallback } from "react";
import { api } from "../lib/api";

const POLL_MS = Number(import.meta?.env?.REACT_APP_BELL_POLL_MS || 12000);
const SOUND_ENABLED_KEY = "aurem_bell_sound_enabled";

const C = {
  border: "rgba(255,255,255,0.10)",
  bg:     "rgba(12,12,14,0.98)",
  text:   "#e5e5e5",
  faint:  "#6b6b6b",
  dim:    "#8a8a8a",
  amber:  "#f5a524",
  red:    "#ef4444",
  green:  "#22c55e",
  mono:   "SFMono-Regular, Menlo, Consolas, monospace",
};

// ── Bell-2 · sound helper ───────────────────────────────────────────
// Single shared AudioContext — created lazily on first user gesture so
// the browser autoplay policy doesn't block it.  A 700Hz sine for 90ms
// with a quick attack-release envelope; short enough to be non-
// intrusive, distinct enough to catch attention across an admin room.
let _audioCtx = null;
function _getAudioCtx() {
  if (_audioCtx) return _audioCtx;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    _audioCtx = new Ctx();
    return _audioCtx;
  } catch { return null; }
}
function playBellBeep() {
  const ctx = _getAudioCtx();
  if (!ctx) return false;
  try {
    // Resume if the tab was backgrounded (browsers auto-suspend).
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(700, now);
    // Attack-hold-release envelope so it doesn't click.
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.18, now + 0.010);
    gain.gain.setValueAtTime(0.18, now + 0.070);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.150);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.160);
    return true;
  } catch { return false; }
}

export default function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(false);
  const [soundEnabled, setSoundEnabled] = useState(() => {
    try { return localStorage.getItem(SOUND_ENABLED_KEY) === "1"; }
    catch { return false; }
  });
  const dropdownRef = useRef(null);

  // Bell-2 tracking — previous unread count so we only beep on genuine
  // increase, not on every poll or on the initial mount. `null` on first
  // mount means "no baseline yet — don't play, just record".
  const prevUnreadRef = useRef(null);

  const applyFetchResult = useCallback((d) => {
    const list = Array.isArray(d?.notifications) ? d.notifications : [];
    const newUnread = Number(d?.unread_count || 0);

    // Bell-2 · genuine-increase beep gate.  Only when:
    //   (1) a baseline exists (skip the very first fetch), AND
    //   (2) unread strictly increased, AND
    //   (3) sound is enabled.
    // Read the CURRENT localStorage flag inside the closure so a fresh
    // toggle within this tab takes effect immediately, without waiting
    // for a re-render.
    const prev = prevUnreadRef.current;
    if (prev !== null && newUnread > prev) {
      let live = false;
      try { live = localStorage.getItem(SOUND_ENABLED_KEY) === "1"; }
      catch { live = false; }
      if (live) playBellBeep();
    }
    prevUnreadRef.current = newUnread;

    setItems(list);
    setUnread(newUnread);
  }, []);

  const fetchNow = useCallback(async () => {
    try {
      const r = await api.get("/admin/status/notifications?limit=30");
      applyFetchResult(r.data || {});
    } catch {
      // silent — bell must never crash the admin surface
    }
  }, [applyFetchResult]);

  useEffect(() => {
    fetchNow();
    const t = setInterval(fetchNow, POLL_MS);
    return () => clearInterval(t);
  }, [fetchNow]);

  // 2026-08-27 · P7 — Journey Watch card's "View in bell log" deep
  // link. The bell is a dropdown widget, not a page, so "deep link"
  // means: open it + refresh, from anywhere on the admin surface.
  useEffect(() => {
    const onOpenBell = () => {
      setOpen(true);
      fetchNow();
    };
    window.addEventListener("aurem:open-bell", onOpenBell);
    return () => window.removeEventListener("aurem:open-bell", onOpenBell);
  }, [fetchNow]);

  // Close dropdown on outside click.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    // Iter 388t · Bug 28 · Escape key closes the dropdown for keyboard
    // users.  Doesn't need arrow-nav since items are read-only (no
    // per-row action to select); Tab through the list is the natural
    // keyboard workflow.
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const markAllRead = async () => {
    setLoading(true);
    // Optimistic — flip every item + zero the badge before the server
    // round-trip so the click feels instant. Reconcile after.
    setItems((prev) => prev.map((n) => ({ ...n, read: true })));
    setUnread(0);
    prevUnreadRef.current = 0;
    try {
      await api.post("/admin/status/notifications/mark-read");
      await fetchNow();
    } finally {
      setLoading(false);
    }
  };

  const markOneRead = async (notifId) => {
    if (!notifId) return;
    // Optimistic — flip this one row + decrement the badge immediately
    // (only if it was actually unread — clicking an already-read row
    // must not underflow the counter).
    let wasUnread = false;
    setItems((prev) => prev.map((n) => {
      if (n.notif_id === notifId && !n.read) { wasUnread = true; return { ...n, read: true }; }
      return n;
    }));
    if (wasUnread) {
      setUnread((u) => Math.max(0, u - 1));
      prevUnreadRef.current = Math.max(0, (prevUnreadRef.current ?? 0) - 1);
    }
    try {
      const r = await api.post(
        `/admin/status/notifications/${encodeURIComponent(notifId)}/mark-read`
      );
      // Reconcile with authoritative count from the server so we never
      // drift from Mongo (covers the edge case of a concurrent bell
      // poll racing this write).
      const auth = Number(r?.data?.unread_count);
      if (Number.isFinite(auth)) {
        setUnread(auth);
        prevUnreadRef.current = auth;
      }
    } catch {
      // If the POST failed, fetchNow will re-sync from server on next
      // poll — the optimistic flip will get corrected then.
    }
  };

  const toggleSound = () => {
    const next = !soundEnabled;
    setSoundEnabled(next);
    try { localStorage.setItem(SOUND_ENABLED_KEY, next ? "1" : "0"); }
    catch { /* private-mode / storage-full — ignore */ }
    // Play a test beep on ENABLE — this doubles as the mandatory
    // user-gesture that unlocks the AudioContext for future auto-plays.
    if (next) playBellBeep();
  };

  return (
    <div style={{ position: "relative", display: "inline-block" }} ref={dropdownRef}>
      <button
        data-testid="notification-bell-btn"
        onClick={() => setOpen(!open)}
        title={unread > 0 ? `${unread} unread` : "Notifications"}
        style={{
          background: "transparent",
          border: `1px solid ${C.border}`,
          borderRadius: 8,
          padding: "6px 10px",
          color: C.text,
          cursor: "pointer",
          position: "relative",
          fontFamily: C.mono,
          fontSize: 13,
        }}
      >
        <span aria-hidden>🔔</span>
        {unread > 0 && (
          <span
            data-testid="notification-bell-badge"
            style={{
              position: "absolute",
              top: -4, right: -4,
              background: C.red,
              color: "white",
              borderRadius: 10,
              padding: "1px 6px",
              fontSize: 10,
              fontWeight: 700,
              minWidth: 16,
              textAlign: "center",
            }}
          >
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          data-testid="notification-bell-dropdown"
          style={{
            position: "absolute",
            right: 0, top: "calc(100% + 6px)",
            width: 380,
            background: C.bg,
            border: `1px solid ${C.border}`,
            borderRadius: 10,
            boxShadow: "0 12px 32px rgba(0,0,0,0.6)",
            zIndex: 1000,
            maxHeight: 480,
            overflowY: "auto",
          }}
        >
          <div style={{
            display: "flex", justifyContent: "space-between",
            alignItems: "center", padding: "10px 14px",
            borderBottom: `1px solid ${C.border}`,
          }}>
            <div style={{
              fontFamily: C.mono, fontSize: 11, letterSpacing: "0.14em",
              color: C.faint,
            }}>
              NOTIFICATIONS · {items.length}
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <button
                data-testid="notification-sound-toggle"
                onClick={toggleSound}
                title={soundEnabled ? "Sound on — click to mute" : "Sound off — click to enable & test"}
                style={{
                  background: "transparent",
                  border: `1px solid ${C.border}`,
                  color: soundEnabled ? C.green : C.faint,
                  fontFamily: C.mono, fontSize: 11,
                  padding: "3px 8px", borderRadius: 5, cursor: "pointer",
                }}
              >{soundEnabled ? "🔊 on" : "🔈 off"}</button>
              {unread > 0 && (
                <button
                  data-testid="mark-all-read-btn"
                  onClick={markAllRead}
                  disabled={loading}
                  style={{
                    background: "transparent",
                    border: `1px solid ${C.border}`,
                    color: C.dim, fontFamily: C.mono, fontSize: 11,
                    padding: "3px 8px", borderRadius: 5, cursor: "pointer",
                  }}
                >Mark all read</button>
              )}
            </div>
          </div>

          {items.length === 0 && (
            <div style={{ padding: "20px 14px", textAlign: "center", color: C.faint, fontSize: 12 }}>
              No notifications yet — everything is quiet.
            </div>
          )}

          {items.map((n, i) => {
            const dot = n.to_state === "red" ? "🔴" : n.to_state === "green" ? "🟢" : "⚪";
            const ageMin = n.created_at ? Math.max(0, Math.round((Date.now() - new Date(n.created_at)) / 60000)) : 0;
            const rowKey = n.notif_id || `${n.check_id}|${n.created_at}|${i}`;
            const clickable = !n.read;
            return (
              <div
                key={rowKey}
                data-testid={`notification-row-${n.check_id}`}
                onClick={clickable ? () => markOneRead(n.notif_id) : undefined}
                title={clickable ? "Click to mark as read" : "Already read"}
                style={{
                  padding: "10px 14px",
                  borderBottom: `1px solid ${C.border}`,
                  background: n.read ? "transparent" : "rgba(239,68,68,0.05)",
                  cursor: clickable ? "pointer" : "default",
                  transition: "background 0.15s ease",
                }}
              >
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 3 }}>
                  <span>{dot}</span>
                  <span style={{ color: C.text, fontSize: 13, fontWeight: 500 }}>
                    {n.name}
                  </span>
                  <span style={{ marginLeft: "auto", color: C.faint, fontSize: 10, fontFamily: C.mono }}>
                    {ageMin < 1 ? "just now" : ageMin < 60 ? `${ageMin}m` : `${Math.round(ageMin/60)}h`}
                  </span>
                </div>
                <div style={{ color: C.dim, fontSize: 11 }}>
                  <span style={{ fontFamily: C.mono, color: C.faint }}>
                    {n.from_state} → {n.to_state}
                  </span>
                  {" · "}
                  <span>{n.detail}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
