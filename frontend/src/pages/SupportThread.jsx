/**
 * pages/SupportThread.jsx — Public HMAC-verified support thread view.
 *
 * Landing page for the "View thread & reply" link inside admin-reply
 * notification emails (services/support_email.py). Identity verified
 * via signed HMAC token (?t=…&e=…) — no login required. Same signature
 * pattern as pages/Support.jsx.
 *
 * Flow:
 *   1. Parse ?t (token), ?e (email) from URL, :ticketId from path
 *   2. GET /support/tickets/{id}/thread?t=…&e=…
 *   3. Render full conversation (user + admin messages, oldest first)
 *   4. Textarea + submit → POST /support/tickets/{id}/reply/token
 *   5. On success, refetch thread to show the just-sent user reply
 */
import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useParams, useLocation, Link } from "react-router-dom";
import { API_BASE } from "../lib/api";
import axios from "axios";

const PAL = {
  bg:        "#0b0b0b",
  card:      "#141414",
  accent:    "#eab308",
  text:      "#e8e8e8",
  muted:     "#a0a0a0",
  border:    "#2a2a2a",
  userBg:    "#1a1a1a",
  adminBg:   "rgba(234, 179, 8, 0.06)",
  adminBorder: "rgba(234, 179, 8, 0.25)",
  errBg:     "rgba(239, 68, 68, 0.08)",
  errBorder: "rgba(239, 68, 68, 0.32)",
};

function fmtTs(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleString("en-US", {
      month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit",
    });
  } catch { return ""; }
}

export default function SupportThread() {
  const { ticketId } = useParams();
  const loc = useLocation();
  const params = useMemo(() => new URLSearchParams(loc.search), [loc.search]);
  const token = params.get("t") || "";
  const email = params.get("e") || "";

  const [ticket, setTicket] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState("");

  const [replyBody, setReplyBody] = useState("");
  const [sending, setSending] = useState(false);
  const [sendErr, setSendErr] = useState("");

  const hasCreds = !!(token && email && ticketId);

  const fetchThread = useCallback(async () => {
    if (!hasCreds) {
      setLoadErr("Missing token or email in URL.");
      setLoading(false);
      return;
    }
    setLoadErr("");
    try {
      const r = await axios.get(
        `${API_BASE}/support/tickets/${ticketId}/thread`,
        { params: { t: token, e: email }, timeout: 15000 },
      );
      setTicket(r.data);
    } catch (ex) {
      const s = ex?.response?.status;
      if (s === 403) setLoadErr("This link is invalid or has expired.");
      else if (s === 404) setLoadErr("Ticket not found.");
      else setLoadErr(ex?.response?.data?.detail || ex?.message ||
                      "Could not load thread.");
    } finally { setLoading(false); }
  }, [hasCreds, ticketId, token, email]);

  useEffect(() => { fetchThread(); }, [fetchThread]);

  async function sendReply(e) {
    e.preventDefault();
    if (!replyBody.trim() || sending) return;
    setSending(true);
    setSendErr("");
    try {
      await axios.post(
        `${API_BASE}/support/tickets/${ticketId}/reply/token`,
        { t: token, e: email, body: replyBody },
        { timeout: 15000 },
      );
      setReplyBody("");
      await fetchThread();
    } catch (ex) {
      setSendErr(ex?.response?.data?.detail || ex?.message ||
                 "Could not send reply. Please try again.");
    } finally { setSending(false); }
  }

  return (
    <div
      data-testid="support-thread-page"
      style={{
        minHeight: "100vh", background: PAL.bg, color: PAL.text,
        padding: "48px 20px",
        fontFamily: "'Helvetica Neue', Arial, sans-serif",
      }}>
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        <div style={{ marginBottom: 20 }}>
          <Link
            data-testid="support-thread-home-link"
            to="/"
            style={{ color: PAL.muted, fontSize: 12,
                     textDecoration: "none" }}>
            ← Back to home
          </Link>
        </div>

        <div style={{
          background: PAL.card, border: `1px solid ${PAL.border}`,
          borderRadius: 14, padding: "32px 28px",
          boxShadow: "0 12px 48px rgba(0,0,0,0.4)",
        }}>
          <h1 style={{ fontSize: 22, margin: "0 0 6px", fontWeight: 600 }}>
            Support conversation
          </h1>
          <p style={{ color: PAL.muted, fontSize: 12,
                      margin: "0 0 24px" }}>
            {email && <>Signed in as <b style={{ color: PAL.text }}>{email}</b> · </>}
            <code style={{ fontFamily: "monospace" }}>{ticketId}</code>
          </p>

          {loading && (
            <div
              data-testid="support-thread-loading"
              style={{ color: PAL.muted, fontSize: 13,
                       padding: "24px 0", textAlign: "center" }}>
              Loading conversation…
            </div>
          )}

          {!loading && loadErr && (
            <div
              data-testid="support-thread-error"
              style={{
                background: PAL.errBg, border: `1px solid ${PAL.errBorder}`,
                color: "#f87171", padding: "16px 18px",
                borderRadius: 8, fontSize: 13, lineHeight: 1.5,
              }}>
              {loadErr}
            </div>
          )}

          {!loading && ticket && (
            <>
              {ticket.subject && (
                <div style={{
                  fontSize: 15, fontWeight: 600, marginBottom: 20,
                  paddingBottom: 12, borderBottom: `1px solid ${PAL.border}`,
                }}>
                  {ticket.subject}
                </div>
              )}

              <div
                data-testid="support-thread-messages"
                style={{ display: "flex", flexDirection: "column",
                         gap: 12, marginBottom: 24 }}>
                {(ticket.messages || []).map((m, i) => {
                  const isAdmin = m.sender === "admin";
                  return (
                    <div
                      key={i}
                      data-testid={`support-thread-msg-${m.sender}-${i}`}
                      style={{
                        background: isAdmin ? PAL.adminBg : PAL.userBg,
                        border: `1px solid ${isAdmin ? PAL.adminBorder : PAL.border}`,
                        borderRadius: 10, padding: "14px 16px",
                      }}>
                      <div style={{
                        fontSize: 11, fontWeight: 600,
                        color: isAdmin ? PAL.accent : PAL.muted,
                        marginBottom: 6,
                        textTransform: "uppercase", letterSpacing: 0.5,
                      }}>
                        {isAdmin ? "Aurem" : "You"}
                        <span style={{
                          marginLeft: 8, fontWeight: 400,
                          textTransform: "none", letterSpacing: 0,
                          color: PAL.muted,
                        }}>
                          {fmtTs(m.ts)}
                        </span>
                      </div>
                      <div style={{
                        fontSize: 13, lineHeight: 1.6, color: PAL.text,
                        whiteSpace: "pre-wrap", wordBreak: "break-word",
                      }}>
                        {m.message}
                      </div>
                    </div>
                  );
                })}
                {(!ticket.messages || ticket.messages.length === 0) && (
                  <div style={{ color: PAL.muted, fontSize: 13,
                                fontStyle: "italic" }}>
                    No messages yet.
                  </div>
                )}
              </div>

              <form onSubmit={sendReply}>
                <div style={{ fontSize: 12, color: PAL.muted,
                              marginBottom: 8 }}>
                  Reply
                </div>
                <textarea
                  data-testid="support-thread-reply-body"
                  value={replyBody}
                  onChange={(e) => setReplyBody(e.target.value)}
                  placeholder="Write a reply…"
                  rows={5}
                  disabled={sending}
                  style={{
                    width: "100%", boxSizing: "border-box",
                    background: PAL.bg, color: PAL.text,
                    border: `1px solid ${PAL.border}`,
                    borderRadius: 8, padding: 12,
                    fontSize: 13, lineHeight: 1.5,
                    fontFamily: "inherit", resize: "vertical",
                    outline: "none",
                  }}
                />
                {sendErr && (
                  <div style={{ color: "#f87171", fontSize: 12,
                                marginTop: 6 }}>
                    {sendErr}
                  </div>
                )}
                <button
                  type="submit"
                  data-testid="support-thread-reply-submit"
                  disabled={!replyBody.trim() || sending}
                  style={{
                    marginTop: 12, width: "100%",
                    background: PAL.accent, color: "#000",
                    border: "none", borderRadius: 8,
                    padding: "10px 20px", fontSize: 13, fontWeight: 600,
                    cursor: (!replyBody.trim() || sending)
                      ? "not-allowed" : "pointer",
                    opacity: (!replyBody.trim() || sending) ? 0.5 : 1,
                  }}>
                  {sending ? "Sending…" : "Send reply"}
                </button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
