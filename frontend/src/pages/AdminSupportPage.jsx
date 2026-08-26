/**
 * pages/AdminSupportPage.jsx — Admin "Support" tab.
 *
 * 2026-08-27 · Admin Compact M6 — extracted verbatim from Admin.jsx's
 * inline SupportPage() so this tab code-splits into its own chunk
 * instead of shipping inside Admin.jsx's main bundle. Behavior is
 * unchanged; only the module boundary moved.
 */
import React, { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";
import { Card, Badge, ago, STATUS_COLOR } from "./Admin";

export default function AdminSupportPage() {
  const [tickets, setTickets] = useState([]);
  const [selected, setSelected] = useState(null);
  const [reply, setReply] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/admin/support");
      setTickets(r.data.tickets || []);
      if (r.data.tickets?.length && !selected) setSelected(r.data.tickets[0]);
    } finally { setLoading(false); }
  }, [selected]);

  useEffect(() => { load(); }, []); // eslint-disable-line

  async function sendReply() {
    if (!reply.trim() || !selected) return;
    try {
      await api.post(`/admin/support/${selected.ticket_id}/reply`, { message: reply });
      setReply("");
      toast({ message: "Reply sent", kind: "success" });
      await load();
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Send failed", kind: "error" });
    }
  }

  async function resolve(tid) {
    if (!window.confirm("Mark as resolved?")) return;
    try {
      await api.post(`/admin/support/${tid}/resolve`);
      toast({ message: "Resolved", kind: "success" });
      await load();
    } catch (e) {
      toast({ message: "Failed", kind: "error" });
    }
  }

  if (loading) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;

  return (
    <div style={{ padding: 24 }}>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 12px" }}>
        Support inbox ({tickets.filter(t => t.status === "open").length} open)
      </h3>
      {tickets.length === 0 ? (
        <Card style={{ padding: 24, textAlign: "center", color: "var(--text-faint)" }}>
          No tickets yet.
        </Card>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "280px 1fr",
                       gap: 12, height: 520 }}>
          <Card style={{ overflowY: "auto" }}>
            {tickets.map((t) => (
              <div
                key={t.ticket_id}
                data-testid={`admin-support-ticket-${t.ticket_id}`}
                onClick={() => setSelected(t)}
                style={{
                  padding: "10px 12px", borderBottom: "1px solid var(--border)",
                  cursor: "pointer",
                  background: selected?.ticket_id === t.ticket_id ? "var(--bg-elev)" : "",
                }}>
                <div style={{ fontSize: 12, fontWeight: 600 }}>{t.subject}</div>
                <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 2 }}>
                  {t.user_email} · {ago(t.created_at)}
                </div>
                <div style={{ marginTop: 4, display: "flex", gap: 6, alignItems: "center" }}>
                  <Badge color={STATUS_COLOR[t.status === "resolved" ? "done" : (t.status === "open" ? "failed" : "running")]}>
                    {t.status}
                  </Badge>
                  {t.source && (
                    <span
                      data-testid={`admin-support-ticket-source-${t.ticket_id}`}
                      style={{
                        fontSize: 10, color: "var(--text-faint)",
                        padding: "1px 6px", border: "1px solid var(--border)",
                        borderRadius: 3, fontFamily: "monospace",
                      }}>
                      {t.source}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </Card>
          {selected && (
            <Card style={{ display: "flex", flexDirection: "column" }}>
              <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--border)",
                             display: "flex", justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{selected.subject}</div>
                  <div style={{ fontSize: 11, color: "var(--text-faint)" }}>
                    {selected.user_email} · {ago(selected.created_at)}
                  </div>
                </div>
                {selected.status !== "resolved" && (
                  <button
                    data-testid={`admin-support-resolve-${selected.ticket_id}`}
                    className="btn-ghost" style={{ padding: "4px 10px", fontSize: 11 }}
                    onClick={() => resolve(selected.ticket_id)}>
                    resolve
                  </button>
                )}
              </div>
              <div style={{ flex: 1, overflowY: "auto", padding: 12,
                             display: "flex", flexDirection: "column", gap: 8 }}>
                {(selected.messages || []).map((m, i) => (
                  <div key={i} style={{
                    alignSelf: m.sender === "admin" ? "flex-end" : "flex-start",
                    maxWidth: "80%",
                    padding: "8px 12px", borderRadius: 4, fontSize: 12,
                    background: m.sender === "admin"
                      ? "rgba(255,138,42,0.1)" : "var(--bg-elev)",
                    border: "1px solid var(--border)",
                  }}>
                    <div style={{ fontSize: 10, color: "var(--text-faint)", marginBottom: 4 }}>
                      {m.sender} · {ago(m.ts)}
                    </div>
                    {m.message}
                  </div>
                ))}
              </div>
              {selected.status !== "resolved" && (
                <div style={{ padding: 10, borderTop: "1px solid var(--border)",
                               display: "flex", gap: 8 }}>
                  <input
                    data-testid="admin-support-reply-input"
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                    placeholder="Type reply…"
                    className="input"
                    onKeyDown={(e) => e.key === "Enter" && sendReply()}
                    style={{ flex: 1 }}
                  />
                  <button
                    data-testid="admin-support-reply-send"
                    className="btn-primary" onClick={sendReply}>
                    send
                  </button>
                </div>
              )}
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
