/**
 * DeleteChatConfirmModal.jsx — Round-2 PR · P0-2 (2026-08).
 *
 * Themed in-app confirm dialog for SessionSwitcher's per-chat delete
 * button. Replaces the previous "no confirm, no undo" one-click
 * DELETE (Shell.jsx::deleteSession) — a real data-loss risk found in
 * the naming/UX audit (a misclick on the trash icon permanently
 * deleted a chat's entire history).
 *
 * Scope is CONFIRM ONLY (founder-locked) — no undo/snapshot in this
 * PR; undo needs backend tombstone infra and is a future ROADMAP item.
 *
 * Same visual language as RollbackConfirmModal.jsx (dark theme,
 * non-blocking, focus-trapped) — NOT window.confirm().
 */
import React, { useRef } from "react";
import { AlertTriangle, Trash2, X } from "lucide-react";
import useModalA11y from "../hooks/useModalA11y";

export default function DeleteChatConfirmModal({ open, onConfirm, onCancel }) {
  const confirmBtnRef = useRef(null);
  const modalRef = useRef(null);

  useModalA11y({
    ref: modalRef,
    isOpen: open,
    onClose: onCancel,
    initialFocus: confirmBtnRef,
  });

  if (!open) return null;

  return (
    <div
      data-testid="delete-chat-confirm-modal"
      ref={modalRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-chat-confirm-title"
      tabIndex={-1}
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel && onCancel();
      }}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.72)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 10_000,
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(420px, 92vw)",
          background: "linear-gradient(135deg, #0f1419 0%, #131a24 100%)",
          border: "1px solid rgba(239, 68, 68, 0.35)",
          borderRadius: 14,
          padding: 22,
          display: "flex",
          flexDirection: "column",
          gap: 14,
          boxShadow: "0 24px 60px -12px rgba(239, 68, 68, 0.35), "
                   + "0 0 0 1px rgba(239, 68, 68, 0.1)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            display: "inline-flex", alignItems: "center",
            justifyContent: "center",
            width: 32, height: 32, borderRadius: 8,
            background: "rgba(239, 68, 68, 0.12)",
            border: "1px solid rgba(239, 68, 68, 0.35)",
          }}>
            <AlertTriangle size={16} color="#f87171" strokeWidth={2.5} />
          </div>
          <strong
            id="delete-chat-confirm-title"
            style={{
              fontSize: 13.5, color: "#f87171", letterSpacing: 0.4,
              textTransform: "uppercase",
            }}
          >
            Delete this chat?
          </strong>
        </div>

        <div style={{ fontSize: 12, color: "#c2c9d6", lineHeight: 1.6 }}>
          This can&apos;t be undone.
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
          <button
            ref={confirmBtnRef}
            type="button"
            data-testid="delete-chat-confirm-approve"
            onClick={onConfirm}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "8px 16px",
              background: "linear-gradient(135deg, #ef4444, #b91c1c)",
              color: "#fff", border: "none", borderRadius: 8,
              fontSize: 11.5, fontWeight: 700,
              cursor: "pointer",
              textTransform: "uppercase", letterSpacing: 0.04,
              boxShadow: "0 6px 18px -8px rgba(239, 68, 68, 0.7)",
            }}
          >
            <Trash2 size={12} strokeWidth={2.5} />
            Delete
          </button>
          <button
            type="button"
            data-testid="delete-chat-confirm-cancel"
            onClick={onCancel}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "8px 16px",
              background: "transparent",
              color: "#c2c9d6",
              border: "1px solid rgba(255, 255, 255, 0.15)",
              borderRadius: 8,
              fontSize: 11.5, fontWeight: 600,
              cursor: "pointer",
              textTransform: "uppercase", letterSpacing: 0.04,
            }}
          >
            <X size={12} strokeWidth={2.5} />
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
