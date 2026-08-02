/**
 * NotificationBell.jsx — Cockpit-Bell UI (Feb 2026)
 *
 * Short-polls /admin/status/notifications every 12s (env-tunable via
 * REACT_APP_BELL_POLL_MS). Renders a badge with the unread count and
 * a dropdown listing newest-first transitions. RED-only badge — gray
 * transitions never touch this UI per the founder's 3-state discipline.
 *
 * Zero mocks — everything from the real /admin/status/* endpoints.
 */
import React, { useEffect, useRef, useState, useCallback } from "react";
import { api } from "../lib/api";

const POLL_MS = Number(import.meta?.env?.REACT_APP_BELL_POLL_MS || 12000);

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

export default function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(false);
  const dropdownRef = useRef(null);

  const fetchNow = useCallback(async () => {
    try {
      const r = await api.get("/admin/status/notifications?limit=30");
      const d = r.data || {};
      setItems(Array.isArray(d.notifications) ? d.notifications : []);
      setUnread(Number(d.unread_count || 0));
    } catch {
      // silent — bell must never crash the admin surface
    }
  }, []);

  useEffect(() => {
    fetchNow();
    const t = setInterval(fetchNow, POLL_MS);
    return () => clearInterval(t);
  }, [fetchNow]);

  // Close dropdown on outside click.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const markAllRead = async () => {
    setLoading(true);
    try {
      await api.post("/admin/status/notifications/mark-read");
      await fetchNow();
    } finally {
      setLoading(false);
    }
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

          {items.length === 0 && (
            <div style={{ padding: "20px 14px", textAlign: "center", color: C.faint, fontSize: 12 }}>
              No notifications yet — everything is quiet.
            </div>
          )}

          {items.map((n, i) => {
            const dot = n.to_state === "red" ? "🔴" : n.to_state === "green" ? "🟢" : "⚪";
            const ageMin = n.created_at ? Math.max(0, Math.round((Date.now() - new Date(n.created_at)) / 60000)) : 0;
            return (
              <div
                key={i}
                data-testid={`notification-row-${n.check_id}`}
                style={{
                  padding: "10px 14px",
                  borderBottom: `1px solid ${C.border}`,
                  background: n.read ? "transparent" : "rgba(239,68,68,0.05)",
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
