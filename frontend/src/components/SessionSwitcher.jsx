/**
 * SessionSwitcher.jsx — 2026-08-23
 *
 * Closes a real gap found while investigating the "chat history
 * vanished" report: multiple real past sessions can exist for the
 * same project, and the current dashboard (chromeless Shell) had NO
 * way to see or switch between them — a session could silently swap
 * under the user (see Shell.jsx sessionStorage-stickiness fix) with
 * zero way to notice or recover. This gives every user a visible,
 * always-available list of their own past chats for the active
 * project, so "which conversation am I looking at" is never a
 * mystery again.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { History, Plus, Trash2, MessageSquare } from "lucide-react";
import { useChatSession } from "./Shell";
import DeleteChatConfirmModal from "./DeleteChatConfirmModal";

function timeAgo(ts) {
  if (!ts) return "";
  const diffMs = Date.now() - ts * 1000;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export default function SessionSwitcher() {
  const { sessionId, sessions, refreshSessions, openSession, deleteSession, startNewSession } =
    useChatSession();
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState({ top: 0, right: 0 });
  // Round-2 PR (P0-2) — trash icon opens a themed confirm dialog
  // instead of deleting immediately (was: zero confirm, zero undo).
  const [pendingDelete, setPendingDelete] = useState(null);
  const rootRef = useRef(null);
  const btnRef = useRef(null);
  const panelRef = useRef(null);

  const toggle = useCallback(() => {
    setOpen((o) => {
      const next = !o;
      if (next) {
        refreshSessions(true);
        const rect = btnRef.current?.getBoundingClientRect();
        if (rect) setCoords({ top: rect.bottom + 6, right: window.innerWidth - rect.right });
      }
      return next;
    });
  }, [refreshSessions]);

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

  return (
    <div ref={rootRef} style={{ position: "relative" }}>
      <button
        ref={btnRef}
        onClick={toggle}
        data-testid="session-switcher-btn"
        title="Recent chats for this project"
        className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-[6px] text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground hover:border-foreground/30"
      >
        <History className="size-3 shrink-0" strokeWidth={2} />
        Chats
        {sessions.length > 0 && (
          <span className="text-[10px] text-muted-foreground/70">({sessions.length})</span>
        )}
      </button>

      {open && createPortal(
        <div
          ref={panelRef}
          data-testid="session-switcher-panel"
          style={{ position: "fixed", top: coords.top, right: coords.right }}
          className="z-[999] w-[300px] max-h-[360px] overflow-y-auto rounded-lg border border-border bg-[#0A0A0A] shadow-xl"
        >
          <div className="flex items-center justify-between px-3 py-2 border-b border-border">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Recent chats
            </span>
            <button
              data-testid="session-switcher-new"
              onClick={() => { startNewSession(); setOpen(false); }}
              title="Start a new chat"
              className="flex items-center gap-1 rounded-md border border-border px-1.5 py-1 text-[11px] text-muted-foreground hover:text-foreground"
            >
              <Plus className="size-3" strokeWidth={2.5} /> New
            </button>
          </div>

          {sessions.length === 0 ? (
            <p data-testid="session-switcher-empty" className="px-3 py-4 text-[12px] text-muted-foreground/70">
              No saved chats for this project yet.
            </p>
          ) : (
            sessions.map((s) => {
              const active = s.session_id === sessionId;
              const label = (s.title && s.title.trim()) || s.last_message || "Untitled";
              const display = label.length > 46 ? label.slice(0, 46) + "…" : label;
              return (
                <div
                  key={s.session_id}
                  data-testid={`session-switcher-row-${s.session_id}`}
                  role="button"
                  onClick={() => { openSession(s.session_id); setOpen(false); }}
                  className={
                    "flex items-center gap-2 px-3 py-2 cursor-pointer border-l-2 " +
                    (active
                      ? "bg-primary/10 border-primary text-foreground"
                      : "border-transparent text-muted-foreground hover:bg-white/[0.03] hover:text-foreground")
                  }
                >
                  <MessageSquare className="size-3 shrink-0" strokeWidth={2} />
                  <div className="flex-1 min-w-0">
                    <div className="truncate text-[12px]" title={label}>{display}</div>
                    <div className="text-[10px] text-muted-foreground/60">
                      {timeAgo(s.updated_at)}{active ? " · viewing now" : ""}
                    </div>
                  </div>
                  <button
                    data-testid={`session-switcher-delete-${s.session_id}`}
                    onClick={(e) => { e.stopPropagation(); setPendingDelete({ id: s.session_id, label: display }); }}
                    title="Delete chat"
                    className="opacity-40 hover:opacity-100 hover:text-red-400 shrink-0"
                  >
                    <Trash2 className="size-3" strokeWidth={2} />
                  </button>
                </div>
              );
            })
          )}
        </div>,
        document.body
      )}

      <DeleteChatConfirmModal
        open={!!pendingDelete}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete) deleteSession(undefined, pendingDelete.id);
          setPendingDelete(null);
        }}
      />
    </div>
  );
}
