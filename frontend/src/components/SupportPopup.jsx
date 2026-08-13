/**
 * SupportPopup.jsx — In-app "Need help?" modal.
 *
 * Small, subject-less textarea + submit that posts to POST /support/tickets
 * (logged-in user). Writes to the same cto_support collection the admin
 * Support panel reads, so a message here shows up in the admin inbox
 * within one refresh — no parallel system.
 *
 * Usage:
 *   <SupportButton source="in_app_dashboard" />
 *
 * The button renders a compact "Need help?" pill. Clicking opens the
 * modal. Use different `source` props on different screens so the
 * founder can tell where a ticket came from (badge shows in admin).
 */
import React, { useRef, useState } from "react";
import { api } from "../lib/api";
import useModalA11y from "../hooks/useModalA11y";
import { toast } from "./Toast";

const PAL = {
  bg:        "#0b0b0b",
  card:      "#141414",
  accent:    "#eab308",
  text:      "#e8e8e8",
  muted:     "#a0a0a0",
  border:    "#2a2a2a",
  errBg:     "rgba(239, 68, 68, 0.08)",
  errBorder: "rgba(239, 68, 68, 0.32)",
  okBg:      "rgba(234, 179, 8, 0.08)",
  okBorder:  "rgba(234, 179, 8, 0.28)",
};

export function SupportButton({ source = "in_app", style = {},
                                 label = "Need help?" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        data-testid={`support-open-${source}`}
        onClick={() => setOpen(true)}
        style={{
          background: "transparent",
          color: PAL.muted,
          border: `1px solid ${PAL.border}`,
          borderRadius: 999,
          padding: "6px 14px",
          fontSize: 12,
          fontWeight: 500,
          cursor: "pointer",
          fontFamily: "inherit",
          transition: "color 120ms, border-color 120ms",
          ...style,
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.color = PAL.accent;
          e.currentTarget.style.borderColor = PAL.accent;
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.color = PAL.muted;
          e.currentTarget.style.borderColor = PAL.border;
        }}>
        {label}
      </button>
      {open && <SupportPopup source={source} onClose={() => setOpen(false)} />}
    </>
  );
}

export function SupportPopup({ source = "in_app", onClose }) {
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null); // "ok" | "err" | null
  const [err, setErr] = useState("");
  const [ticketId, setTicketId] = useState("");
  const modalRef = useRef(null);

  // Iter 388t · Bug 27 · Escape close + focus trap for the popup.
  // Was missing entirely — a keyboard-only user had no way to
  // dismiss the popup once opened.
  useModalA11y({ ref: modalRef, isOpen: true, onClose });

  async function submit(e) {
    e.preventDefault();
    if (!body.trim() || busy) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.post("/support/tickets", { body, source });
      const tid = r.data?.ticket_id || "";
      setTicketId(tid);
      setStatus("ok");
      // Iter 388t · UX polish — fire a top-level toast in addition to
      // the in-popup success block so the founder gets an obvious
      // confirmation even if they close the popup right away.  The
      // in-popup block shows the ticket ref + longer copy; this
      // toast surfaces the "sent!" signal at the app chrome level.
      try {
        toast({
          message: tid
            ? `Support ticket received — ref ${tid.slice(0, 8)}. My reply will land in your email inbox.`
            : "Support ticket received. My reply will land in your email inbox.",
          kind: "success",
          duration: 4500,
        });
      } catch { /* toast optional — never block submit success */ }
    } catch (ex) {
      setStatus("err");
      setErr(ex?.response?.data?.detail || ex?.message ||
             "Send failed. Please try again.");
    } finally { setBusy(false); }
  }

  return (
    <div
      data-testid="support-popup-backdrop"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 9999, padding: 20,
      }}>
      <div
        data-testid="support-popup"
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="support-popup-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        style={{
          background: PAL.card, border: `1px solid ${PAL.border}`,
          borderRadius: 12, padding: "24px 24px 20px",
          width: "100%", maxWidth: 480,
          boxShadow: "0 20px 60px rgba(0,0,0,0.6)",
          color: PAL.text,
          fontFamily: "'Helvetica Neue', Arial, sans-serif",
        }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                      alignItems: "center", marginBottom: 12 }}>
          <h3 id="support-popup-title" style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>
            Need help?
          </h3>
          <button
            data-testid="support-popup-close"
            onClick={onClose}
            style={{
              background: "transparent", border: "none",
              color: PAL.muted, cursor: "pointer", fontSize: 20,
              lineHeight: 1, padding: 0,
            }}>×</button>
        </div>

        {status === "ok" ? (
          <div
            data-testid="support-popup-success"
            style={{
              background: PAL.okBg, border: `1px solid ${PAL.okBorder}`,
              padding: "16px 18px", borderRadius: 8,
            }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: PAL.accent,
                          marginBottom: 4 }}>
              ✓ Message received
            </div>
            <div style={{ fontSize: 12, color: PAL.muted, lineHeight: 1.5 }}>
              Thanks — I'll get back to you personally. My reply lands
              in your email inbox with a signed link to view the full
              thread and reply back (no login needed).
              {ticketId && (
                <div style={{ marginTop: 6, fontSize: 10,
                              fontFamily: "monospace" }}>
                  Ref: {ticketId}
                </div>
              )}
            </div>
            <button
              data-testid="support-popup-done"
              onClick={onClose}
              style={{
                marginTop: 12, background: "transparent",
                color: PAL.text, border: `1px solid ${PAL.border}`,
                borderRadius: 6, padding: "6px 14px",
                fontSize: 12, cursor: "pointer",
              }}>
              Close
            </button>
          </div>
        ) : (
          <form onSubmit={submit}>
            <p style={{ fontSize: 12, color: PAL.muted,
                        margin: "0 0 10px", lineHeight: 1.5 }}>
              Drop a message below and I'll get back to you personally.
            </p>
            <textarea
              autoFocus
              data-testid="support-popup-body"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="What do you need help with?"
              rows={5}
              disabled={busy}
              style={{
                width: "100%", boxSizing: "border-box",
                background: PAL.bg, color: PAL.text,
                border: `1px solid ${PAL.border}`,
                borderRadius: 8, padding: 10,
                fontSize: 13, lineHeight: 1.5,
                fontFamily: "inherit", resize: "vertical",
                outline: "none",
              }}
            />
            {status === "err" && (
              <div style={{ color: "#f87171", fontSize: 11,
                            marginTop: 6 }}>
                {err}
              </div>
            )}
            <button
              type="submit"
              data-testid="support-popup-submit"
              disabled={!body.trim() || busy}
              style={{
                marginTop: 12, width: "100%",
                background: PAL.accent, color: "#000",
                border: "none", borderRadius: 8,
                padding: "10px 18px", fontSize: 13, fontWeight: 600,
                cursor: (!body.trim() || busy) ? "not-allowed" : "pointer",
                opacity: (!body.trim() || busy) ? 0.5 : 1,
              }}>
              {busy ? "Sending…" : "Send message"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

export default SupportButton;
