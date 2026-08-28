/**
 * ConfirmModal.jsx — Overnight T6/P1e (2026-08-28).
 *
 * Generic themed confirm dialog, same visual language as
 * DeleteChatConfirmModal.jsx / RollbackConfirmModal.jsx — replaces
 * native window.confirm() on user-facing (non-ship-flow) paths.
 * Ship/rollback confirms are explicitly OUT of scope here (parked
 * under ROADMAP F17, pending the Phase-7 ship-UI unification).
 */
import React, { useRef } from "react";
import { AlertTriangle, Check, X } from "lucide-react";
import useModalA11y from "../hooks/useModalA11y";

export default function ConfirmModal({
  open, title, body, confirmLabel = "Confirm", cancelLabel = "Cancel",
  danger = true, onConfirm, onCancel, testidPrefix = "confirm-modal",
}) {
  const confirmBtnRef = useRef(null);
  const modalRef = useRef(null);

  useModalA11y({
    ref: modalRef,
    isOpen: open,
    onClose: onCancel,
    initialFocus: confirmBtnRef,
  });

  if (!open) return null;
  const accent = danger ? "#f87171" : "#7dd3fc";
  const accentSoft = danger ? "rgba(239, 68, 68, 0.35)" : "rgba(56, 189, 248, 0.35)";

  return (
    <div
      data-testid={`${testidPrefix}-overlay`}
      ref={modalRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby={`${testidPrefix}-title`}
      tabIndex={-1}
      onClick={(e) => { if (e.target === e.currentTarget) onCancel?.(); }}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0, 0, 0, 0.72)", backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 10_000,
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(440px, 92vw)",
          background: "linear-gradient(135deg, #0f1419 0%, #131a24 100%)",
          border: `1px solid ${accentSoft}`,
          borderRadius: 14, padding: 22,
          display: "flex", flexDirection: "column", gap: 14,
          boxShadow: `0 24px 60px -12px ${accentSoft}, 0 0 0 1px rgba(255,255,255,0.04)`,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            width: 32, height: 32, borderRadius: 8,
            background: danger ? "rgba(239, 68, 68, 0.12)" : "rgba(56, 189, 248, 0.12)",
            border: `1px solid ${accentSoft}`,
          }}>
            <AlertTriangle size={16} color={accent} strokeWidth={2.5} />
          </div>
          <strong id={`${testidPrefix}-title`} style={{
            fontSize: 13.5, color: accent, letterSpacing: 0.4, textTransform: "uppercase",
          }}>{title}</strong>
        </div>

        {body && (
          <div style={{ fontSize: 12, color: "#c2c9d6", lineHeight: 1.6 }}>{body}</div>
        )}

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
          <button
            ref={confirmBtnRef} type="button"
            data-testid={`${testidPrefix}-confirm`}
            onClick={onConfirm}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "8px 16px",
              background: danger
                ? "linear-gradient(135deg, #ef4444, #b91c1c)"
                : "linear-gradient(135deg, #38bdf8, #0284c7)",
              color: "#fff", border: "none", borderRadius: 8,
              fontSize: 11.5, fontWeight: 700, cursor: "pointer",
              textTransform: "uppercase", letterSpacing: 0.04,
              boxShadow: `0 6px 18px -8px ${accentSoft}`,
            }}
          >
            <Check size={12} strokeWidth={2.5} />
            {confirmLabel}
          </button>
          <button
            type="button" data-testid={`${testidPrefix}-cancel`}
            onClick={onCancel}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "8px 16px", background: "transparent",
              color: "#c2c9d6", border: "1px solid rgba(255, 255, 255, 0.15)",
              borderRadius: 8, fontSize: 11.5, fontWeight: 600, cursor: "pointer",
              textTransform: "uppercase", letterSpacing: 0.04,
            }}
          >
            <X size={12} strokeWidth={2.5} />
            {cancelLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
