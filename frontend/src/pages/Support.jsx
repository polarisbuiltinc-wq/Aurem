/**
 * pages/Support.jsx — Public support-message page.
 *
 * Two entry paths:
 *   1. Token link inside campaign emails (?t=…&e=…&src=…) — identity
 *      pre-verified via signed HMAC, email locked, posts to
 *      /support/tickets/token.
 *   2. 2026-08-19 fix: anyone else (e.g. the plain footer "Support"
 *      link on Landing, or a pre-signup visitor with no email link at
 *      all) — a normal name+email+message form, posts to the new
 *      public endpoint /support/tickets/public. Before this fix the
 *      page was permanently disabled without a token, so the site's
 *      own footer link led to a form nobody could actually submit.
 *
 * Backend verifies the HMAC for path 1, rate-limits path 2 by IP;
 * both write to the same `cto_support` collection the admin panel reads.
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

const EMAIL_RX = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export default function Support() {
  const loc = useLocation();
  const params = useMemo(() => new URLSearchParams(loc.search), [loc.search]);
  const token  = params.get("t")   || "";
  const email  = params.get("e")   || "";
  const source = params.get("src") || "landing";

  const [body, setBody] = useState("");
  const [name, setName] = useState("");
  const [emailInput, setEmailInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null); // "ok" | "err" | null
  const [err, setErr] = useState("");
  const [ticketId, setTicketId] = useState("");

  const hasToken = !!(token && email);
  const effectiveEmail = hasToken ? email : emailInput.trim().toLowerCase();
  const emailValid = hasToken || EMAIL_RX.test(effectiveEmail);
  const canSubmit = !!body.trim() && emailValid && !busy;

  async function submit(e) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setErr("");
    try {
      const r = hasToken
        ? await axios.post(`${API_BASE}/support/tickets/token`, {
            t: token, e: email, source, body,
          }, { timeout: 15000 })
        : await axios.post(`${API_BASE}/support/tickets/public`, {
            name: name.trim() || undefined,
            email: effectiveEmail,
            source,
            body,
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
              <b style={{ color: PAL.text }}>{effectiveEmail}</b>.
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
              <>
                <input
                  data-testid="support-page-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Your name (optional)"
                  style={{
                    width: "100%", boxSizing: "border-box",
                    background: "#0b0b0b", color: PAL.text,
                    border: `1px solid ${PAL.border}`,
                    borderRadius: 8, padding: 12,
                    fontSize: 14, fontFamily: "inherit",
                    outline: "none", marginBottom: 10,
                  }}
                />
                <input
                  data-testid="support-page-email"
                  type="email"
                  required
                  value={emailInput}
                  onChange={(e) => setEmailInput(e.target.value)}
                  placeholder="Your email — so I can reply"
                  style={{
                    width: "100%", boxSizing: "border-box",
                    background: "#0b0b0b", color: PAL.text,
                    border: `1px solid ${PAL.border}`,
                    borderRadius: 8, padding: 12,
                    fontSize: 14, fontFamily: "inherit",
                    outline: "none", marginBottom: 16,
                  }}
                />
              </>
            )}
            <textarea
              data-testid="support-page-body"
              autoFocus={hasToken}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="What do you need help with?"
              rows={7}
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
              disabled={!canSubmit}
              style={{
                marginTop: 16, width: "100%",
                background: PAL.accent, color: "#000",
                border: "none", borderRadius: 8,
                padding: "12px 24px", fontSize: 14, fontWeight: 600,
                cursor: !canSubmit ? "not-allowed" : "pointer",
                opacity: !canSubmit ? 0.5 : 1,
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
