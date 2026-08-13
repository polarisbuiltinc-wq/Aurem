/**
 * pages/Support.jsx — Public support-message page.
 *
 * Landing page for the "Need help?" link inside every campaign email
 * (Stage 0/3/7) and other transactional emails. Identity is verified
 * via a signed HMAC token in the URL (?t=…&e=…&src=…) so the user
 * doesn't need to log in to file a ticket.
 *
 * Flow:
 *   1. Parse ?t (token), ?e (email), ?src (source label) from URL
 *   2. Show a subject-less textarea + submit button
 *   3. POST /support/tickets/token with those exact fields
 *   4. Backend verifies HMAC(support:<email>) == t, writes to
 *      cto_support (same collection admin Support panel reads)
 *
 * If the token is invalid/expired the user still sees the form; the
 * backend rejects with 403 and we show a friendly error.
 */
import React, { useState, useMemo } from "react";
import { useLocation, Link } from "react-router-dom";
import { API_BASE } from "../lib/api";
import axios from "axios";

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

export default function Support() {
  const loc = useLocation();
  const params = useMemo(() => new URLSearchParams(loc.search), [loc.search]);
  const token  = params.get("t")   || "";
  const email  = params.get("e")   || "";
  const source = params.get("src") || "email_other";

  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null); // "ok" | "err" | null
  const [err, setErr] = useState("");
  const [ticketId, setTicketId] = useState("");

  const hasToken = !!(token && email);

  async function submit(e) {
    e.preventDefault();
    if (!body.trim() || busy) return;
    setBusy(true);
    setErr("");
    try {
      const r = await axios.post(`${API_BASE}/support/tickets/token`, {
        t: token, e: email, source, body,
      }, { timeout: 15000 });
      setTicketId(r.data?.ticket_id || "");
      setStatus("ok");
    } catch (ex) {
      setStatus("err");
      setErr(
        ex?.response?.data?.detail ||
        ex?.message ||
        "Something went wrong. Please try again.",
      );
    } finally { setBusy(false); }
  }

  return (
    <div
      data-testid="support-page"
      style={{
        minHeight: "100vh", background: PAL.bg, color: PAL.text,
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: "48px 20px",
        fontFamily: "'Helvetica Neue', Arial, sans-serif",
      }}>
      <div style={{
        maxWidth: 560, width: "100%",
        background: PAL.card, border: `1px solid ${PAL.border}`,
        borderRadius: 14, padding: "36px 32px",
        boxShadow: "0 12px 48px rgba(0,0,0,0.4)",
      }}>
        <div style={{ marginBottom: 20 }}>
          <Link to="/" style={{ color: PAL.muted, fontSize: 12,
                                textDecoration: "none" }}>← Back to home</Link>
        </div>

        <h1 style={{ fontSize: 24, margin: "0 0 8px", fontWeight: 600 }}>
          Need help?
        </h1>
        <p style={{ color: PAL.muted, fontSize: 14, margin: "0 0 24px",
                    lineHeight: 1.5 }}>
          Drop a message below and I'll get back to you personally.
          {hasToken && email && (
            <> Messaging as <b style={{ color: PAL.text }}>{email}</b>.</>
          )}
        </p>

        {status === "ok" ? (
          <div
            data-testid="support-page-success"
            style={{
              background: PAL.okBg, border: `1px solid ${PAL.okBorder}`,
              padding: "18px 20px", borderRadius: 10,
            }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: PAL.accent,
                          marginBottom: 6 }}>
              ✓ Message received
            </div>
            <div style={{ fontSize: 13, color: PAL.muted, lineHeight: 1.5 }}>
              Thanks — I read every one. You'll hear back at{" "}
              <b style={{ color: PAL.text }}>{email}</b>.
              {ticketId && (
                <div style={{ marginTop: 6, fontSize: 11,
                              fontFamily: "monospace" }}>
                  Ref: {ticketId}
                </div>
              )}
            </div>
          </div>
        ) : (
          <form onSubmit={submit}>
            {!hasToken && (
              <div style={{
                background: PAL.errBg, border: `1px solid ${PAL.errBorder}`,
                padding: "10px 12px", borderRadius: 8, fontSize: 12,
                color: "#f87171", marginBottom: 16,
              }}>
                This link is missing its identity token — please open the
                link from your original email, or use the in-app help
                button once you're signed in.
              </div>
            )}
            <textarea
              data-testid="support-page-body"
              autoFocus
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="What do you need help with?"
              rows={7}
              disabled={!hasToken || busy}
              style={{
                width: "100%", boxSizing: "border-box",
                background: "#0b0b0b", color: PAL.text,
                border: `1px solid ${PAL.border}`,
                borderRadius: 8, padding: 12,
                fontSize: 14, lineHeight: 1.5,
                fontFamily: "inherit", resize: "vertical",
                outline: "none",
              }}
            />
            {status === "err" && (
              <div style={{ color: "#f87171", fontSize: 12, marginTop: 8 }}>
                {err}
              </div>
            )}
            <button
              type="submit"
              data-testid="support-page-submit"
              disabled={!hasToken || !body.trim() || busy}
              style={{
                marginTop: 16, width: "100%",
                background: PAL.accent, color: "#000",
                border: "none", borderRadius: 8,
                padding: "12px 24px", fontSize: 14, fontWeight: 600,
                cursor: (!hasToken || !body.trim() || busy)
                  ? "not-allowed" : "pointer",
                opacity: (!hasToken || !body.trim() || busy) ? 0.5 : 1,
              }}>
              {busy ? "Sending…" : "Send message"}
            </button>
            <p style={{ fontSize: 11, color: PAL.muted, marginTop: 14,
                        textAlign: "center", lineHeight: 1.5 }}>
              Source: <code style={{ fontFamily: "monospace" }}>{source}</code>
              {" · "}No login required.
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
