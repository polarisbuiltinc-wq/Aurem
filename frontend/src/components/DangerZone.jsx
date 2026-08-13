/**
 * DangerZone.jsx — Iter 388t · GDPR/DSAR self-serve account delete.
 *
 * Rendered at the bottom of Settings → Profile tab.  Wraps a multi-step
 * confirmation modal so a stray click can't nuke a founder's account.
 *
 * Flow:
 *   1. User clicks "Delete my account" → modal opens on Step 1 (warning).
 *   2. Step 2 asks them to type their OWN email verbatim; button
 *      stays disabled until the input matches.
 *   3. Submit calls POST /api/aurem-dev/auth/delete-me — on success we
 *      clear the token and hard-redirect to /login?deleted=1.
 *
 * Failure modes:
 *   • 403 (founder) → surfaces the "contact support" refusal inline.
 *   • 422 (wrong email) → shouldn't happen since we gate client-side
 *     but the inline error banner catches it if a race sneaks through.
 *   • Network / 5xx → banner with a Retry hint.
 */
import React, { useRef, useState, useMemo } from "react";
import { AlertTriangle, Trash2 } from "lucide-react";
import { api, logout as apiLogout } from "../lib/api";
import useModalA11y from "../hooks/useModalA11y";

export default function DangerZone({ email }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const modalRef = useRef(null);

  const emailLower = (email || "").trim().toLowerCase();
  const typedLower = typed.trim().toLowerCase();
  const canSubmit = !!emailLower && typedLower === emailLower && !submitting;

  // Iter 388v · P0 Security Fix — MASK the confirm-email display so a
  // shoulder-surfer / screen-share viewer cannot copy-paste it back
  // into the input. Confirmation only works if the user KNOWS their
  // own email; the masked version won't match on paste.
  //   Rule: hide the local part entirely except the last 2 chars.
  //   Example: teji.ss1986@gmail.com → *********86@gmail.com
  // Validation stays against the real, unmasked email (line 34).
  const emailMasked = useMemo(() => {
    if (!emailLower) return "";
    const at = emailLower.lastIndexOf("@");
    if (at < 0) return emailLower;  // malformed — nothing to mask meaningfully
    const local = emailLower.slice(0, at);
    const domain = emailLower.slice(at);
    if (local.length <= 2) {
      // Local part too short — mask everything with 4 stars so we
      // still never reveal it verbatim on the screen.
      return "****" + domain;
    }
    const last2 = local.slice(-2);
    const mask = "*".repeat(local.length - 2);
    return mask + last2 + domain;
  }, [emailLower]);

  const reset = () => {
    setOpen(false);
    setTyped("");
    setError(null);
    setSubmitting(false);
  };

  // Iter 388t · Bug 27 · focus trap + Escape close + focus restore.
  // (The old inline `onKeyDown={(e) => if Escape` handler is now
  // handled centrally by the hook; keeping the same close semantics.)
  useModalA11y({ ref: modalRef, isOpen: open, onClose: reset });

  const onDelete = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.post("/auth/delete-me", { email_confirmation: typed });
      // Success — clear token/user cache + hard redirect with toast flag.
      try { apiLogout(); } catch { /* ignore */ }
      window.location.replace("/login?deleted=1");
    } catch (e) {
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail || e?.message || "delete failed";
      setError(`HTTP ${status || "network"} — ${detail}`);
      setSubmitting(false);
    }
  };

  return (
    <>
      <section
        className="card"
        data-testid="settings-danger-zone"
        style={{
          border: "1px solid rgba(239, 68, 68, 0.35)",
          background: "rgba(239, 68, 68, 0.04)",
        }}
      >
        <h3 style={{
          fontSize: 14, color: "#ef4444", margin: 0, marginBottom: 8,
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <AlertTriangle size={14} /> Danger zone
        </h3>
        <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "0 0 12px", lineHeight: 1.55 }}>
          Permanently delete your AUREM account, cancel any active
          subscription, revoke every GitHub App installation, and purge
          all associated data across our systems.  This is irreversible.
        </p>
        <button
          type="button"
          data-testid="danger-zone-delete-btn"
          onClick={() => setOpen(true)}
          style={{
            appearance: "none",
            background: "transparent",
            color: "#ef4444",
            border: "1px solid rgba(239, 68, 68, 0.5)",
            borderRadius: 6,
            padding: "8px 14px",
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <Trash2 size={13} /> Delete my account
        </button>
      </section>

      {open && (
        <div
          data-testid="danger-zone-modal"
          ref={modalRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="danger-zone-modal-title"
          tabIndex={-1}
          style={{
            position: "fixed", inset: 0, zIndex: 10001,
            background: "rgba(0,0,0,0.6)", display: "flex",
            alignItems: "center", justifyContent: "center", padding: 16,
          }}
        >
          <div style={{
            background: "var(--panel)",
            border: "1px solid rgba(239, 68, 68, 0.35)",
            borderRadius: 10,
            width: "100%", maxWidth: 520,
            padding: 22,
          }}>
            <h4
              id="danger-zone-modal-title"
              style={{
                fontSize: 16, color: "#ef4444", margin: 0, marginBottom: 8,
                display: "flex", alignItems: "center", gap: 8,
              }}
            >
              <AlertTriangle size={16} /> Delete your account permanently?
            </h4>
            <p style={{ fontSize: 12.5, color: "var(--text)", margin: "0 0 12px", lineHeight: 1.6 }}>
              This will immediately:
            </p>
            <ul style={{ fontSize: 12, color: "var(--text-faint)", margin: "0 0 14px 18px", lineHeight: 1.7 }}>
              <li>Cancel any active Stripe subscription</li>
              <li>Revoke every GitHub App installation you own</li>
              <li>Delete all projects, tasks, chats, tokens, and API keys</li>
              <li>Log you out of every device</li>
            </ul>
            <p style={{ fontSize: 12.5, color: "var(--text)", margin: "0 0 8px" }}>
              To confirm, type your full account email exactly — from
              memory, not copied from here:
            </p>
            <div
              data-testid="danger-zone-email-masked"
              aria-label="masked account email (hint only — type the full email from memory)"
              style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 12, color: "var(--accent-2)",
                marginBottom: 8, letterSpacing: "0.02em",
                userSelect: "none",
              }}>
              {emailMasked}
            </div>
            <input
              className="input"
              type="email"
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="type your email to confirm"
              data-testid="danger-zone-email-input"
              disabled={submitting}
              style={{
                width: "100%", padding: "10px 12px",
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 13, marginBottom: 10,
              }}
            />
            {error && (
              <div
                data-testid="danger-zone-error"
                style={{
                  fontSize: 12, color: "#ef4444",
                  padding: "8px 10px", marginBottom: 10,
                  background: "rgba(239,68,68,0.08)",
                  border: "1px solid rgba(239,68,68,0.3)",
                  borderRadius: 5,
                }}
              >
                {error}
              </div>
            )}
            <div style={{
              display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 6,
            }}>
              <button
                type="button"
                data-testid="danger-zone-cancel-btn"
                onClick={reset}
                disabled={submitting}
                style={{
                  appearance: "none",
                  background: "transparent",
                  color: "var(--text)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  padding: "8px 14px",
                  fontSize: 12,
                  cursor: submitting ? "not-allowed" : "pointer",
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="danger-zone-confirm-btn"
                disabled={!canSubmit}
                onClick={onDelete}
                style={{
                  appearance: "none",
                  background: canSubmit ? "#ef4444" : "rgba(239,68,68,0.3)",
                  color: "#fff",
                  border: "none",
                  borderRadius: 6,
                  padding: "8px 14px",
                  fontSize: 12,
                  fontWeight: 700,
                  cursor: canSubmit ? "pointer" : "not-allowed",
                  display: "inline-flex", alignItems: "center", gap: 6,
                }}
              >
                <Trash2 size={13} />
                {submitting ? "Deleting…" : "Yes, delete permanently"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
