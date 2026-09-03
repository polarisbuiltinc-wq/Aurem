/**
 * UserNotificationBell.jsx — P2-A (2026-08-28).
 *
 * User-facing notification bell for the dashboard TopBar (distinct
 * from the admin-only NotificationBell.jsx). Polls
 * GET /notifications every 30s + on open. PERSISTENT events
 * (payment_failed, ship_failed, repo_revoked) stay visually flagged
 * with an amber dot in the list until the user marks them read or
 * clicks "Mark all read" — they never auto-clear on their own.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Bell, AlertTriangle, CheckCheck } from "lucide-react";
import { api } from "../../../lib/api";

const TYPE_LABEL = {
  scan_done:      "Scan",
  ship_done:      "Shipped",
  ship_failed:    "Ship failed",
  offer_claimed:  "Offer claimed",
  payment_failed: "Payment failed",
  repo_revoked:   "Repo access",
  kit_live:       "Kit",
  upgrade_eligible: "Upgrade",
};

function timeAgo(ts) {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function UserNotificationBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [unread, setUnread] = useState(0);
  const [coords, setCoords] = useState({ top: 0, left: 0 });
  const rootRef = useRef(null);
  const btnRef = useRef(null);
  const panelRef = useRef(null);

  const refresh = useCallback(() => {
    api.get("/notifications")
      .then((r) => {
        setItems(r.data?.items || []);
        setUnread(r.data?.unread_count || 0);
      })
      .catch(() => { /* keep last-known state on a transient blip */ });
  }, []);

  useEffect(() => {
    refresh();
    // P2-F follow-up (2026-08-28) — testing_agent review note: 30s was
    // too slow for persistent alerts (payment_failed/ship_failed/
    // repo_revoked). 10s keeps this a plain poll (no new SSE/websocket
    // infra) while cutting worst-case bell latency to a third.
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, 10000);
    return () => clearInterval(timer);
  }, [refresh]);

  const toggle = useCallback(() => {
    setOpen((o) => {
      const next = !o;
      if (next) {
        refresh();
        const rect = btnRef.current?.getBoundingClientRect();
        if (rect) setCoords({ top: rect.bottom + 8, left: Math.max(8, rect.right - 320) });
      }
      return next;
    });
  }, [refresh]);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (
        rootRef.current && !rootRef.current.contains(e.target) &&
        panelRef.current && !panelRef.current.contains(e.target)
      ) setOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const markOne = useCallback((notifId) => {
    api.post(`/notifications/${notifId}/read`).then(refresh).catch(() => {});
  }, [refresh]);

  const markAll = useCallback(() => {
    api.post("/notifications/read-all").then(refresh).catch(() => {});
  }, [refresh]);

  return (
    <div ref={rootRef} style={{ position: "relative", display: "inline-flex" }}>
      <button
        ref={btnRef}
        onClick={toggle}
        data-testid="user-notification-bell-trigger"
        title="Notifications"
        className="relative flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-white/5 transition-colors"
        style={{ width: 32, height: 32 }}
      >
        <Bell className="size-4" strokeWidth={2} />
        {unread > 0 && (
          <span
            data-testid="user-notification-bell-count"
            className="absolute -top-0.5 -right-0.5 flex items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-semibold leading-none"
            style={{ minWidth: 16, height: 16, padding: "0 3px" }}
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && createPortal(
        <div
          ref={panelRef}
          data-testid="user-notification-bell-panel"
          style={{ position: "fixed", top: coords.top, left: coords.left }}
          className="z-[999] w-[320px] max-h-[420px] overflow-y-auto rounded-lg border border-border bg-[#0A0A0A] shadow-xl"
        >
          <div className="flex items-center justify-between px-3 py-2 border-b border-border">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Notifications
            </span>
            {unread > 0 && (
              <button
                data-testid="user-notification-bell-mark-all-read"
                onClick={markAll}
                className="flex items-center gap-1 text-[11px] text-primary hover:underline"
              >
                <CheckCheck className="size-3" strokeWidth={2.5} /> Mark all read
              </button>
            )}
          </div>
          {items.length === 0 ? (
            <p data-testid="user-notification-bell-empty" className="px-3 py-6 text-center text-[12px] text-muted-foreground/70">
              No notifications yet.
            </p>
          ) : (
            items.map((n) => (
              <div
                key={n.notif_id}
                data-testid={`user-notification-item-${n.notif_id}`}
                data-persistent={n.persistent ? "true" : "false"}
                data-unread={!n.read_at ? "true" : "false"}
                onClick={() => !n.read_at && markOne(n.notif_id)}
                className={
                  "flex items-start gap-2 px-3 py-2.5 border-b border-border/50 cursor-pointer transition-colors " +
                  (!n.read_at ? "bg-white/[0.03] hover:bg-white/[0.05]" : "hover:bg-white/[0.02] opacity-60")
                }
              >
                {n.persistent && !n.read_at && (
                  <AlertTriangle className="size-3.5 shrink-0 mt-[2px] text-amber-500" strokeWidth={2.5} />
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] font-mono uppercase tracking-wide text-muted-foreground/70">
                      {TYPE_LABEL[n.type] || n.type}
                    </span>
                    {!n.read_at && (
                      <span className="size-1.5 rounded-full bg-primary shrink-0" />
                    )}
                  </div>
                  <div className="text-[12px] text-foreground mt-0.5">{n.text}</div>
                  <div className="text-[10px] text-muted-foreground/60 mt-0.5">{timeAgo(n.created_at)}</div>
                </div>
              </div>
            ))
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
