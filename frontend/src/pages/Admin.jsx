/**
 * pages/Admin.jsx — AuremCTO Admin Panel
 * Guarded route: only users with is_admin in localStorage 'aurem_user'.
 * All data lives under /api/aurem-dev/admin/*.
 */
import React, { useState, useEffect, useCallback, useMemo } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import {
  LayoutDashboard, Users, MessageCircle, Folder, ListChecks,
  Cpu, CreditCard, Network as SitemapIcon, Settings as SettingsIcon,
  LogOut, ExternalLink, ArrowLeft, Loader2, Brain, Eye, Terminal,
  Mail, Activity, Plug, GitBranch, Zap, ShieldAlert, DollarSign, ShieldCheck,
  Menu, X, Wrench, KeyRound,
} from "lucide-react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";
import { buildGroupedAdminNav } from "../lib/adminNav";
import AuremAdminPanel from "../components/AuremAdminPanel";
import OraChatDrawer from "../components/OraChatDrawer";        // Iter 212m-238
import NotificationBell from "../components/NotificationBell";   // Feb 2026 · cockpit bell
import AdminOverview from "./AdminOverview";
import AdminCockpit from "./AdminCockpit";                        // Feb 2026 · sidebar-in-cockpit
import AdminSuggestions from "./AdminSuggestions";              // Iter 212m-193
import AgentTokenPanel from "../components/AgentTokenPanel";
import AdminThinkingHints from "../components/AdminThinkingHints";
import TwoFactorCard from "../components/TwoFactorCard";  // Iter 212m-20
import AdminHouseRules from "../components/AdminHouseRules";  // Iter 212m-24
import AdminRobotGuide from "../components/AdminRobotGuide";  // Iter 212m-187
import AdminBINTracker from "./AdminBINTracker";               // Iter 212m-171
import AdminGithubBulkRevoke from "./AdminGithubBulkRevoke";    // 2026-08-30
import AdminFeatureFlags from "./AdminFeatureFlags";           // Iter 212m-171
import { LLMCreditMonitor } from "./AdminLLMCredits";          // Iter 212m-171
import { ParliamentLivePanel } from "./AdminParliamentLive";   // Iter 212m-171

// 2026-08-27 · Admin Compact M6 — these 5 tabs used to be defined
// inline in this file (~1900 lines total: Support, Architecture,
// Settings + its 4 config sub-cards, Audit). Extracted verbatim into
// their own modules and lazy-loaded so a tab's code only downloads
// when that tab is actually opened. M2 also merged the old inline
// "Payments" tab into AdminFinancials (see the "payments" case below).
const AdminSupportPageLazy = React.lazy(() => import("./AdminSupportPage"));
const AdminArchitecturePageLazy = React.lazy(() => import("./AdminArchitecturePage"));
const AdminSettingsPageLazy = React.lazy(() => import("./AdminSettingsPage"));
const AdminAuditPageLazy = React.lazy(() => import("./AdminAuditPage"));
const AdminFinancialsLazy = React.lazy(() => import("./AdminFinancials"));

// ── Helpers ────────────────────────────────────────────────────────────
const fmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n ?? 0));
export const fmtMoney = (n) => `$${(n || 0).toFixed(2)}`;
// Iter 212m-96 — accept BOTH numeric epoch seconds AND ISO date strings.
// Backend returns ISO strings for newer users (Iter 211+) and Unix epoch
// numbers for legacy rows. Either way we normalize to epoch seconds.
export const ago = (v) => {
  if (v == null || v === "") return "—";
  let sec;
  if (typeof v === "number") {
    sec = v;
  } else {
    const t = new Date(v).getTime();
    if (isNaN(t)) return "—";
    sec = Math.floor(t / 1000);
  }
  const s = Math.floor(Date.now() / 1000 - sec);
  if (s < 0) return "—";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};

export const STATUS_COLOR = {
  done: "var(--ok)",
  failed: "var(--danger)",
  running: "var(--accent-2)",
  queued: "var(--text-faint)",
  active: "var(--ok)",
  suspended: "var(--danger)",
};

export function Badge({ children, color }) {
  return (
    <span style={{
      display: "inline-block", padding: "2px 8px",
      fontSize: 10, letterSpacing: "0.05em",
      fontFamily: "'JetBrains Mono', monospace",
      background: "var(--bg-elev)",
      border: `1px solid ${color || "var(--border)"}`,
      borderRadius: 4, color: color || "var(--text-dim)",
    }}>
      {children}
    </span>
  );
}

export function Card({ children, style }) {
  return (
    <div style={{
      background: "var(--panel-2)",
      border: "1px solid var(--border)",
      borderRadius: 4,
      ...style,
    }}>
      {children}
    </div>
  );
}

export function MCard({ label, value, sub, accent }) {
  return (
    <Card style={{ padding: "14px 16px" }}>
      <div style={{ fontSize: 10, letterSpacing: "0.1em",
                     color: "var(--text-faint)", textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ fontSize: 26, fontWeight: 600, marginTop: 6,
                     color: accent || "var(--text)",
                     fontFamily: "'JetBrains Mono', monospace" }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, marginTop: 4, color: "var(--text-faint)" }}>{sub}</div>}
    </Card>
  );
}

export function Table({ cols, rows, onRowClick }) {
  if (!rows || rows.length === 0) {
    return <div style={{ padding: 24, textAlign: "center", fontSize: 12, color: "var(--text-faint)" }}>
      No data yet.
    </div>;
  }
  return (
    <div className="aurem-table-wrap">
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 560 }}>
      <thead>
        <tr>{cols.map((c, i) => (
          <th key={i} style={{
            textAlign: "left", padding: "8px 12px", fontSize: 10,
            color: "var(--text-faint)", textTransform: "uppercase",
            letterSpacing: "0.1em", borderBottom: "1px solid var(--border)",
            background: "var(--bg-elev)", whiteSpace: "nowrap",
          }}>{c}</th>
        ))}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}
              onClick={onRowClick ? () => onRowClick(i) : undefined}
              style={{ cursor: onRowClick ? "pointer" : "default" }}
              onMouseEnter={(e) => onRowClick && (e.currentTarget.style.background = "var(--bg-elev)")}
              onMouseLeave={(e) => onRowClick && (e.currentTarget.style.background = "")}>
            {r.map((cell, j) => (
              <td key={j} style={{ padding: "10px 12px",
                                    borderBottom: "1px solid var(--border)",
                                    verticalAlign: "middle" }}>
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

// ── Pages ──────────────────────────────────────────────────────────────
function Dashboard() {
  const [data, setData] = useState(null);
  useEffect(() => {
    api.get("/admin/dashboard").then((r) => setData(r.data)).catch(() => {});
  }, []);
  if (!data) return <div style={{ padding: 24, color: "var(--text-faint)" }}><Loader2 size={14} className="spin" /> Loading…</div>;

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 18 }}>
        <MCard label="Users" value={data.total_users} sub={`${data.total_projects} projects`} />
        <MCard label="Tasks today" value={data.tasks_today} sub={`${data.success_rate}% success`} />
        <MCard label="Done / Failed" value={`${data.done_tasks}/${data.failed_tasks}`} accent="var(--ok)" />
        <MCard label="Chat sessions" value={data.total_sessions} sub="lifetime" />
      </div>

      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        Recent tasks
      </h3>
      <Card>
        <Table
          cols={["Task", "User", "Status", "Commit", "Time"]}
          rows={(data.recent_tasks || []).map((t) => [
            <span key="task" style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis",
                            whiteSpace: "nowrap", display: "block" }}>{t.task}</span>,
            <span key="user" style={{ color: "var(--text-faint)",
                            fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              {(t.user_id || "").slice(0, 10)}
            </span>,
            <Badge key="status" color={STATUS_COLOR[t.status]}>{t.status}</Badge>,
            t.commit_sha ? <span key="commit" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{t.commit_sha}</span> : "—",
            <span key="time" style={{ color: "var(--text-faint)" }}>{ago(t.created_at)}</span>,
          ])}
        />
      </Card>

      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "20px 0 8px" }}>
        Recent users
      </h3>
      <Card>
        <Table
          cols={["Email", "Name", "Tier", "Joined"]}
          rows={(data.recent_users || []).map((u) => [
            u.email,
            u.name || "—",
            <Badge key="tier">{u.tier || "free"}</Badge>,
            <span key="time" style={{ color: "var(--text-faint)" }}>{ago(u.created_at)}</span>,
          ])}
        />
      </Card>
    </div>
  );
}

function UsersList({ onSelect }) {
  const [users, setUsers] = useState([]);
  const [buckets, setBuckets] = useState({ "24h": 0, "7d": 0, "30d": 0, "all": 0 });
  const [search, setSearch] = useState("");
  // Iter 194 — signup-window filter. Default "all"; pills above the
  // table let admin scope the list to "joined in 24 h", "7 d", "30 d".
  const [windowSel, setWindowSel] = useState("all");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(() => new Set());
  const [composerOpen, setComposerOpen] = useState(false);
  const [deletingId, setDeletingId] = useState(null);

  const load = useCallback(async (s, w) => {
    setLoading(true);
    try {
      const r = await api.get("/admin/users", { params: { search: s, window: w } });
      setUsers(r.data.users || []);
      if (r.data.bucket_counts) setBuckets(r.data.bucket_counts);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => load(search, windowSel), 250);
    return () => clearTimeout(t);
  }, [search, windowSel, load]);

  const toggleOne = (uid) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(uid)) next.delete(uid); else next.add(uid);
      return next;
    });
  };
  const toggleAll = () => {
    setSelected((prev) => {
      if (prev.size === users.length) return new Set();
      return new Set(users.map((u) => u.user_id));
    });
  };

  async function deleteUser(u) {
    if (!window.confirm(`Delete ${u.email}? This cascades to projects, tasks, payments, API keys. No undo.`)) {
      return;
    }
    setDeletingId(u.user_id);
    try {
      const r = await api.delete(`/admin/users/${u.user_id}`);
      const dels = r.data?.deletions || {};
      toast({
        message: `Deleted ${u.email} · users:${dels.users ?? "?"} projects:${dels.cto_projects ?? "?"} tasks:${dels.cto_tasks ?? "?"}`,
        kind: "success",
      });
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(u.user_id);
        return next;
      });
      load(search, windowSel);
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Delete failed",
        kind: "error",
      });
    } finally { setDeletingId(null); }
  }

  return (
    <div style={{ padding: 24 }}>
      {/* Iter 65 — Agent token P&L widget, top of Users tab. */}
      <AgentTokenPanel />
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "center", marginBottom: 14, gap: 12, flexWrap: "wrap" }}>
        <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                      color: "var(--text-faint)", margin: 0 }}>
          Users ({users.length}){selected.size > 0 && ` · ${selected.size} selected`}
        </h3>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {selected.size > 0 && (
            <button
              data-testid="admin-users-send-offer"
              className="btn-primary"
              style={{ fontSize: 12, padding: "6px 14px" }}
              onClick={() => setComposerOpen(true)}
            >
              <Mail size={12} style={{ verticalAlign: "middle", marginRight: 6 }} />
              Send offer email ({selected.size})
            </button>
          )}
          <input
            data-testid="admin-users-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search email / name…"
            className="input"
            style={{ width: 260, maxWidth: "100%" }}
          />
        </div>
      </div>

      {/* Iter 194 — signup-window filter pills. Clicking a pill scopes
          the table to users that joined in that bucket; the count next
          to each pill is the raw bucket_count from /admin/users so the
          admin sees "how many today" before scoping in. */}
      <div data-testid="admin-users-window-pills"
           style={{ display: "flex", gap: 6, flexWrap: "wrap",
                    marginBottom: 14, alignItems: "center" }}>
        {[
          { id: "24h", label: "Last 24 h" },
          { id: "7d",  label: "Last 7 days" },
          { id: "30d", label: "Last 30 days" },
          { id: "all", label: "All time" },
        ].map((p) => {
          const active = windowSel === p.id;
          return (
            <button
              key={p.id}
              data-testid={`admin-users-window-${p.id}`}
              onClick={() => setWindowSel(p.id)}
              style={{
                padding: "5px 12px",
                fontSize: 11,
                fontFamily: "'JetBrains Mono', monospace",
                fontWeight: 600,
                letterSpacing: "0.04em",
                background: active ? "var(--accent, #ff8a2a)" : "transparent",
                color: active ? "#0a0e1a" : "var(--text-faint)",
                border: `1px solid ${active ? "var(--accent, #ff8a2a)" : "var(--line, rgba(255,255,255,0.08))"}`,
                borderRadius: 999,
                cursor: "pointer",
                transition: "all 0.15s",
              }}
            >
              {p.label} · {buckets[p.id] ?? 0}
            </button>
          );
        })}
      </div>
      <Card>
        {loading ? <div style={{ padding: 24, color: "var(--text-faint)" }}><Loader2 size={14} className="spin" /> Loading…</div> : (
          <Table
            cols={[
              <input
                key="all"
                data-testid="admin-users-select-all"
                type="checkbox"
                checked={users.length > 0 && selected.size === users.length}
                onChange={toggleAll}
                style={{ cursor: "pointer" }}
              />,
              "Email", "Name", "Tier", "Projects", "Tasks", "Status", "Joined", "",
            ]}
            rows={users.map((u) => [
              <input
                key={`cb-${u.user_id}`}
                data-testid={`admin-user-cb-${u.user_id}`}
                type="checkbox"
                checked={selected.has(u.user_id)}
                onChange={() => toggleOne(u.user_id)}
                onClick={(e) => e.stopPropagation()}
                style={{ cursor: "pointer" }}
              />,
              u.email,
              u.name || "—",
              <Badge key="tier">{u.tier || "free"}</Badge>,
              u.project_count ?? 0,
              u.task_count ?? 0,
              <Badge key="status" color={STATUS_COLOR[u.status || "active"]}>{u.status || "active"}</Badge>,
              // Iter 194 — Joined column. Shows "x ago" + absolute
              // date as a tooltip so admin can scan recency and
              // hover for the exact timestamp.
              // Iter 212m-96 — defensive date parse: backend returns
              // either a Unix epoch (number) or an ISO string depending
              // on user vintage. Either form crashed `new Date(undefined*1000)`
              // → "Invalid time value" which crashed the entire Users
              // tab. Now we handle both safely.
              <span
                key="joined"
                title={(() => {
                  try {
                    const v = u.created_at;
                    if (v == null) return "unknown";
                    const d = typeof v === "number"
                      ? new Date(v * 1000)
                      : new Date(v);
                    return isNaN(d.getTime()) ? "unknown" : d.toISOString();
                  } catch { return "unknown"; }
                })()}
                style={{
                  fontSize: 11,
                  color: "var(--text-faint)",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                {u.created_at ? ago(u.created_at) : "—"}
              </span>,
              <div key="act" style={{ display: "flex", gap: 6 }}>
                <button
                  data-testid={`admin-user-view-${u.user_id}`}
                  className="btn-ghost" style={{ padding: "4px 10px", fontSize: 11 }}
                  onClick={() => onSelect(u)}>
                  view →
                </button>
                <button
                  data-testid={`admin-user-delete-${u.user_id}`}
                  className="btn-ghost"
                  style={{ padding: "4px 10px", fontSize: 11, color: "#fca5a5" }}
                  disabled={deletingId === u.user_id}
                  onClick={() => deleteUser(u)}
                  title="Delete user (cascades to all owned data)"
                >
                  {deletingId === u.user_id ? "…" : "delete"}
                </button>
              </div>,
            ])}
          />
        )}
      </Card>

      {composerOpen && (
        <EmailOfferComposer
          userIds={Array.from(selected)}
          recipients={users.filter((u) => selected.has(u.user_id))
                          .map((u) => ({ email: u.email, name: u.name }))}
          onClose={() => setComposerOpen(false)}
          onSent={() => {
            setComposerOpen(false);
            setSelected(new Set());
          }}
        />
      )}
    </div>
  );
}

// Iter 193 — Modal composer for bulk offer emails. Subject + HTML body
// with `{{name}}` and `{{email}}` substitutions handled server-side.
// Shows recipient count up front and a confirm summary before sending
// so an admin can't fat-finger a 500-user blast.
function EmailOfferComposer({ userIds, recipients, onClose, onSent }) {
  const [subject, setSubject] = useState("");
  const [bodyHtml, setBodyHtml] = useState(
    "<p>Hi {{name}},</p>\n<p>We've got something special for you...</p>\n<p>— The ORA team</p>"
  );
  const [sending, setSending] = useState(false);

  async function send() {
    if (!subject.trim() || !bodyHtml.trim()) return;
    if (!window.confirm(`Send "${subject}" to ${userIds.length} user(s)? This cannot be undone.`)) {
      return;
    }
    setSending(true);
    try {
      const r = await api.post("/admin/users/email-offer", {
        user_ids:  userIds,
        subject:   subject.trim(),
        body_html: bodyHtml,
      });
      const dryRun = r.data?.dry_run;
      const sent = r.data?.sent ?? 0;
      const failed = r.data?.failed ?? 0;
      toast({
        message: dryRun
          ? `Dry-run (RESEND_API_KEY missing) · ${recipients.length} would receive`
          : `Sent ${sent} email(s)${failed ? ` · ${failed} failed` : ""}`,
        kind: failed === 0 ? "success" : "info",
      });
      onSent();
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Send failed",
        kind: "error",
      });
    } finally { setSending(false); }
  }

  return (
    <div
      data-testid="admin-email-composer"
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.65)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 9000,
        padding: 24,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg-1, #0a0e1a)",
          border: "1px solid var(--line, rgba(255,255,255,0.08))",
          borderRadius: 12,
          width: "min(720px, 100%)",
          maxHeight: "90vh",
          overflowY: "auto",
          padding: 22,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "center", marginBottom: 14 }}>
          <h3 style={{ fontSize: 14, margin: 0 }}>Send offer email</h3>
          <button onClick={onClose}
                  style={{ background: "none", border: "none",
                           color: "var(--text-faint)", cursor: "pointer", fontSize: 18 }}>
            ×
          </button>
        </div>

        <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 14,
                       fontFamily: "'JetBrains Mono', monospace" }}>
          Recipients: {recipients.length} · Templating: <code>{`{{name}}`}</code>, <code>{`{{email}}`}</code>
        </div>

        <div data-testid="admin-offer-replyto-hint"
             style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 14,
                       padding: "6px 10px",
                       background: "rgba(34,197,94,0.07)",
                       border: "1px solid rgba(34,197,94,0.18)",
                       borderRadius: 6 }}>
          ↩ Replies will land in <strong style={{ color: "#22c55e" }}>polarisbuiltinc@gmail.com</strong> (via <code>REPLY_TO_EMAIL</code>)
        </div>

        <label style={{ fontSize: 11, color: "var(--text-faint)", display: "block",
                         marginBottom: 4, fontFamily: "'JetBrains Mono', monospace",
                         textTransform: "uppercase", letterSpacing: "0.06em" }}>
          Subject
        </label>
        <input
          data-testid="admin-offer-subject"
          className="input"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder="Special offer just for you"
          style={{ width: "100%", marginBottom: 12 }}
        />

        <label style={{ fontSize: 11, color: "var(--text-faint)", display: "block",
                         marginBottom: 4, fontFamily: "'JetBrains Mono', monospace",
                         textTransform: "uppercase", letterSpacing: "0.06em" }}>
          Body (HTML)
        </label>
        <textarea
          data-testid="admin-offer-body"
          className="input"
          value={bodyHtml}
          onChange={(e) => setBodyHtml(e.target.value)}
          rows={10}
          style={{ width: "100%", fontFamily: "'JetBrains Mono', monospace",
                   fontSize: 12, lineHeight: 1.5, resize: "vertical" }}
        />

        <div style={{ marginTop: 14, padding: "8px 12px",
                       background: "rgba(255,138,42,0.08)",
                       border: "1px solid rgba(255,138,42,0.2)",
                       borderRadius: 6, fontSize: 11,
                       color: "var(--text-faint)" }}>
          Preview recipients: {recipients.slice(0, 5).map((r) => r.email).join(", ")}
          {recipients.length > 5 && ` …and ${recipients.length - 5} more`}
        </div>

        <div style={{ marginTop: 14, display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            onClick={onClose}
            className="btn-secondary"
            disabled={sending}
            style={{ fontSize: 12 }}>
            Cancel
          </button>
          <button
            data-testid="admin-offer-send"
            onClick={send}
            disabled={sending || !subject.trim() || !bodyHtml.trim()}
            className="btn-primary"
            style={{ fontSize: 12 }}>
            {sending ? "Sending…" : `Send to ${recipients.length}`}
          </button>
        </div>
      </div>
    </div>
  );
}

function UserDetail({ user, onBack }) {
  const [d, setD] = useState(user);
  const [busy, setBusy] = useState(false);
  const [grantOpen, setGrantOpen] = useState(false);
  const [grantTokens, setGrantTokens] = useState("");
  const [grantReason, setGrantReason] = useState("");
  const [granting, setGranting] = useState(false);

  useEffect(() => {
    api.get(`/admin/users/${user.user_id}`).then((r) => setD(r.data)).catch(() => {});
  }, [user.user_id]);

  async function toggleSuspend() {
    const wantSuspend = d.status !== "suspended";
    if (!window.confirm(`${wantSuspend ? "Suspend" : "Unsuspend"} ${d.email}?`)) return;
    setBusy(true);
    try {
      await api.post(`/admin/users/${user.user_id}/suspend`, { suspend: wantSuspend });
      setD((prev) => ({ ...prev, status: wantSuspend ? "suspended" : "active" }));
      toast({ message: wantSuspend ? "Suspended" : "Unsuspended", kind: "success" });
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Failed", kind: "error" });
    } finally { setBusy(false); }
  }

  async function submitGrant() {
    const n = parseInt(grantTokens, 10);
    if (!n || n <= 0) {
      toast({ message: "Enter a positive token amount", kind: "error" });
      return;
    }
    setGranting(true);
    try {
      const r = await api.post(`/admin/users/${user.user_id}/grant-tokens`, {
        tokens: n,
        reason: grantReason.trim(),
      });
      toast({ message: `Granted ${n.toLocaleString()} tokens ✓`, kind: "success" });
      setD((prev) => ({ ...prev, usage: r.data.usage, token_grants: [
        { tokens: n, reason: grantReason.trim(), granted_at: Date.now() / 1000, granted_by: "you" },
        ...(prev.token_grants || []),
      ] }));
      setGrantOpen(false);
      setGrantTokens("");
      setGrantReason("");
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Grant failed", kind: "error" });
    } finally { setGranting(false); }
  }

  // 2026-02-12 — Email User button.
  // Pre-fills mailto: with user's actual data pulled from `d` (server-
  // returned dev_users row + projects). Founder doesn't have to copy-
  // paste name/tier/project name every time. Keeping mailto: (not an
  // in-admin compose form) per founder's spec: "mailto: is fine for now".
  function emailUser() {
    const name       = d.name || (d.email || "").split("@")[0] || "there";
    const projects   = (d.projects || []).map((p) => p.name).filter(Boolean);
    const projectStr = projects.length === 0
      ? "your project"
      : projects.length === 1
        ? projects[0]
        : `${projects[0]} (and ${projects.length - 1} more)`;
    const tier       = d.tier || "free";
    const joined     = ago(d.created_at) || "recently";
    const subject    = `Quick check-in about ${projectStr}`;
    const body = [
      `Hey ${name},`,
      "",
      `Noticed ${projectStr} is set up on ORA — is there anything I can help you get running?`,
      "",
      "A few things I can do straight away if useful:",
      "  · Walk you through the first ORA task for this repo",
      "  · Grant extra tokens if you're testing more than the free tier allows",
      "  · Answer any question about the workflow",
      "",
      "Just hit reply — happy to help.",
      "",
      "— Founder, ORA by Aurem",
      "",
      "—",
      `(context: ${d.email} · tier=${tier} · joined ${joined}` +
        (projects.length > 0 ? ` · project(s): ${projects.join(", ")})` : ")"),
    ].join("\n");
    const url = `mailto:${encodeURIComponent(d.email)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    window.location.href = url;
  }

  // Timeline row formatter — keeps the JSX clean. Groups event `type`
  // into human labels + an icon-like glyph the founder can scan quickly.
  function formatTimelineRow(evt) {
    const T = evt.type || "";
    const D = evt.detail || {};
    if (T === "signup_completed")       return { icon: "◆", label: "Signup completed", tail: D.signup_ip ? `from ${D.signup_ip}` : "" };
    if (T === "login_success" || T === "login")
                                        return { icon: "→", label: "Login", tail: "" };
    if (T === "email_verified")         return { icon: "✓", label: "Email verified", tail: "" };
    if (T === "first_chat")             return { icon: "○", label: "First chat opened", tail: "" };
    if (T === "first_ship")             return { icon: "★", label: "First ship", tail: "" };
    if (T === "promo_first50_claimed")  return { icon: "🎁", label: "First-50 promo claimed → 30-day Pro", tail: "" };
    if (T === "token_grant")            return { icon: "＋", label: `+${(D.tokens || 0).toLocaleString()} tokens granted`, tail: D.reason ? `— ${D.reason}` : "" };
    if (T.startsWith("task_"))          return { icon: "▸", label: `Task ${T.slice(5)}`, tail: D.task ? `— ${D.task.slice(0, 60)}${D.task.length > 60 ? "…" : ""}` : "" };
    return { icon: "·", label: T || "event", tail: "" };
  }

  return (
    <div style={{ padding: 24 }}>
      <button className="btn-ghost" style={{ padding: "4px 10px", fontSize: 11, marginBottom: 14 }}
              onClick={onBack}>
        <ArrowLeft size={11} /> Users
      </button>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14 }}>
        <Card style={{ padding: 16 }}>
          <h3 style={{ fontSize: 13, margin: "0 0 12px" }}>{d.email}</h3>
          <div style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1.8 }}>
            <div><b>Name:</b> {d.name || "—"}</div>
            <div data-testid="admin-user-phone"><b>Phone:</b> {d.phone || "—"}</div>
            <div><b>User ID:</b> <code style={{ fontSize: 11 }}>{d.user_id}</code></div>
            <div><b>Tier:</b> {d.tier || "free"}</div>
            <div><b>Status:</b> <Badge color={STATUS_COLOR[d.status || "active"]}>{d.status || "active"}</Badge></div>
            <div><b>Tokens remaining:</b> {fmt(d.tokens_remaining || 0)}</div>
            {d.usage && (
              <div data-testid="admin-user-usage" style={{ marginTop: 6, padding: "6px 8px",
                background: "rgba(255,255,255,0.03)", borderRadius: 6 }}>
                <div><b>Plan limit:</b> {fmt(d.usage.plan_limit)} · <b>Granted:</b> {fmt(d.usage.tokens_granted)}</div>
                <div><b>Effective:</b> {fmt(d.usage.effective_limit)} · <b>Used:</b> {fmt(d.usage.used)} ({d.usage.pct_used}%)</div>
                <div style={{ color: d.usage.is_exhausted ? "var(--danger)" : "var(--ok)" }}>
                  <b>Remaining:</b> {fmt(d.usage.remaining)}{d.usage.is_exhausted ? " — EXHAUSTED" : ""}
                </div>
              </div>
            )}
            <div><b>Joined:</b> {ago(d.created_at)}</div>
            <div><b>Projects:</b> {d.project_count ?? 0} · <b>Tasks:</b> {d.task_count ?? 0} · <b>Sessions:</b> {d.session_count ?? 0}</div>
          </div>
          <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              data-testid="admin-user-suspend"
              className="btn-ghost"
              disabled={busy}
              style={{ padding: "6px 12px", fontSize: 11,
                       borderColor: "rgba(255,107,107,0.3)", color: "var(--danger)" }}
              onClick={toggleSuspend}>
              {d.status === "suspended" ? "Unsuspend" : "Suspend"}
            </button>
            <button
              data-testid="admin-user-grant-open"
              className="btn-ghost"
              disabled={granting}
              style={{ padding: "6px 12px", fontSize: 11,
                       borderColor: "rgba(120,200,255,0.35)", color: "var(--accent-2)" }}
              onClick={() => setGrantOpen((v) => !v)}>
              {grantOpen ? "Cancel grant" : "Grant tokens"}
            </button>
            <button
              data-testid="admin-user-email"
              className="btn-ghost"
              style={{ padding: "6px 12px", fontSize: 11,
                       borderColor: "rgba(234,179,8,0.35)", color: "#eab308" }}
              onClick={emailUser}
              title={`Compose email to ${d.email}`}>
              Email user
            </button>
          </div>
          {grantOpen && (
            <div data-testid="admin-user-grant-form" style={{
              marginTop: 12, padding: 10,
              background: "rgba(120,200,255,0.06)",
              border: "1px solid rgba(120,200,255,0.25)",
              borderRadius: 8, display: "flex", flexDirection: "column", gap: 8,
            }}>
              <input
                data-testid="admin-grant-tokens-input"
                type="number"
                min="1"
                placeholder="Tokens to grant (e.g. 5000)"
                value={grantTokens}
                onChange={(e) => setGrantTokens(e.target.value)}
                className="input"
                style={{ fontSize: 12, padding: "6px 8px" }}
              />
              <input
                data-testid="admin-grant-reason-input"
                type="text"
                placeholder="Reason (e.g. support credit)"
                value={grantReason}
                onChange={(e) => setGrantReason(e.target.value)}
                className="input"
                style={{ fontSize: 12, padding: "6px 8px" }}
              />
              <button
                data-testid="admin-grant-submit"
                className="btn-primary"
                onClick={submitGrant}
                disabled={granting || !grantTokens}
                style={{ padding: "6px 12px", fontSize: 11 }}>
                {granting ? "Granting…" : "Grant tokens"}
              </button>
            </div>
          )}
          {(d.token_grants || []).length > 0 && (
            <div style={{ marginTop: 12, fontSize: 11, color: "var(--text-dim)" }}>
              <b style={{ fontSize: 11 }}>Recent grants ({d.token_grants.length})</b>
              <div style={{ maxHeight: 110, overflowY: "auto", marginTop: 4 }}>
                {d.token_grants.map((g, i) => (
                  <div key={i} style={{ padding: "3px 0", borderBottom: "1px solid var(--border)" }}>
                    +{fmt(g.tokens)} · {g.reason || "no reason"} · <span style={{ color: "var(--text-faint)" }}>{ago(g.granted_at)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
        <Card style={{ padding: 16 }}>
          <h3 style={{ fontSize: 13, margin: "0 0 10px" }}>Projects ({(d.projects || []).length})</h3>
          <div style={{ maxHeight: 200, overflowY: "auto", fontSize: 12 }}>
            {(d.projects || []).map((p) => (
              <div key={p.project_id} style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                <b>{p.name}</b> · {p.github_owner}/{p.github_repo}@{p.branch}
                {" "}<Badge>{p.tech_stack || "auto"}</Badge>
              </div>
            ))}
            {(!d.projects || d.projects.length === 0) && <div style={{ color: "var(--text-faint)" }}>No projects.</div>}
          </div>
        </Card>
      </div>

      {/* 2026-02-12 · Active Offers / Promo Status — founder must be
          able to see at a glance whether this user already has a promo
          before sending a new one. Data source: /admin/users/{id}.offers
          which merges dev_users flags + user_seo_claims. */}
      {d.offers && (
        <>
          <h3 data-testid="admin-user-offers-header"
              style={{ fontSize: 12, letterSpacing: "0.1em",
                       textTransform: "uppercase",
                       color: "var(--text-faint)", margin: "0 0 8px" }}>
            Active Offers / Promo Status
          </h3>
          <Card style={{ padding: 14, marginBottom: 14 }}>
            <div data-testid="admin-user-offers"
                 style={{ display: "grid",
                          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
                          gap: 10, fontSize: 12 }}>

              {/* Tier + tier_source pill */}
              <div style={{ padding: 10, borderRadius: 8,
                            background: "rgba(255,255,255,0.03)" }}>
                <div style={{ color: "var(--text-faint)", fontSize: 10,
                              textTransform: "uppercase", letterSpacing: "0.08em",
                              marginBottom: 4 }}>
                  Current tier
                </div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>
                  {d.offers.tier || "free"}
                </div>
                <div data-testid="admin-user-tier-source"
                     style={{ marginTop: 4, fontSize: 11 }}>
                  Source:{" "}
                  <Badge color={
                    d.offers.tier_source === "founder"           ? "#eab308" :
                    d.offers.tier_source === "promo_first50"     ? "#a855f7" :
                    d.offers.tier_source === "paid_subscription" ? "#22c55e" :
                    "#64748b"
                  }>
                    {d.offers.tier_source === "promo_first50"    ? "First-50 promo (not paid)" :
                     d.offers.tier_source === "paid_subscription" ? "Paid subscription" :
                     d.offers.tier_source === "founder"          ? "Founder allow-list" :
                     d.offers.tier_source === "paid_or_unknown"  ? "Paid or unknown" :
                     "Free (no active offer)"}
                  </Badge>
                </div>
              </div>

              {/* First-50 promo status */}
              <div data-testid="admin-user-first50-block"
                   style={{ padding: 10, borderRadius: 8,
                            background: d.offers.first50?.claimed
                              ? "rgba(168,85,247,0.10)"
                              : "rgba(255,255,255,0.03)",
                            border: d.offers.first50?.claimed
                              ? "1px solid rgba(168,85,247,0.32)"
                              : "1px solid transparent" }}>
                <div style={{ color: "var(--text-faint)", fontSize: 10,
                              textTransform: "uppercase", letterSpacing: "0.08em",
                              marginBottom: 4 }}>
                  First-50 promo
                </div>
                {d.offers.first50?.claimed ? (
                  <>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "#a855f7" }}>
                      🎁 Claimed
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>
                      Claimed {ago(d.offers.first50.claimed_at)}
                    </div>
                    {d.offers.first50.pro_active ? (
                      <div style={{ fontSize: 11, marginTop: 2, color: "var(--ok)" }}>
                        Pro active · {d.offers.first50.days_left ?? "?"} days left
                      </div>
                    ) : (
                      <div style={{ fontSize: 11, marginTop: 2, color: "var(--text-faint)" }}>
                        Pro window ended
                      </div>
                    )}
                  </>
                ) : (
                  <div style={{ fontSize: 13, color: "var(--text-faint)" }}>
                    Not claimed
                    <div style={{ fontSize: 11, marginTop: 4, color: "var(--text-dim)" }}>
                      {d.email_verified
                        ? "Email verified — promo may have been full at verify time"
                        : "Email unverified — promo not yet available"}
                    </div>
                  </div>
                )}
              </div>

              {/* Founder offer (SEO fix) */}
              <div data-testid="admin-user-founder-offer-block"
                   style={{ padding: 10, borderRadius: 8,
                            background: (d.offers.founder_offer?.claim_count || 0) > 0
                              ? "rgba(234,179,8,0.10)"
                              : "rgba(255,255,255,0.03)",
                            border: (d.offers.founder_offer?.claim_count || 0) > 0
                              ? "1px solid rgba(234,179,8,0.32)"
                              : "1px solid transparent" }}>
                <div style={{ color: "var(--text-faint)", fontSize: 10,
                              textTransform: "uppercase", letterSpacing: "0.08em",
                              marginBottom: 4 }}>
                  Founder SEO offer
                </div>
                {(d.offers.founder_offer?.claim_count || 0) > 0 ? (
                  <>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "#eab308" }}>
                      Claimed × {d.offers.founder_offer.claim_count}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>
                      {(d.offers.founder_offer.active_claims || []).length} active,{" "}
                      {(d.offers.founder_offer.all_claims || []).length -
                       (d.offers.founder_offer.active_claims || []).length} closed
                    </div>
                    {(d.offers.founder_offer.all_claims || []).slice(0, 2).map((c, i) => (
                      <div key={i} style={{ fontSize: 11, color: "var(--text-dim)",
                                           marginTop: 3, whiteSpace: "nowrap",
                                           overflow: "hidden", textOverflow: "ellipsis" }}>
                        · {c.site_url || c.repo_id} · <Badge>{c.fix_status}</Badge>
                      </div>
                    ))}
                  </>
                ) : (
                  <div style={{ fontSize: 13, color: "var(--text-faint)" }}>
                    Not claimed
                  </div>
                )}
              </div>
            </div>
          </Card>
        </>
      )}

      {/* 2026-02-12 · Activity Logs — merged timeline from funnel_events
          + cto_tasks + cto_token_grants + email_verifications + promo
          claims. Newest first. Purely reads existing collections, no
          new logging surface. */}
      <h3 data-testid="admin-user-activity-header"
          style={{ fontSize: 12, letterSpacing: "0.1em",
                   textTransform: "uppercase",
                   color: "var(--text-faint)", margin: "0 0 8px" }}>
        Activity Logs
        <span style={{ marginLeft: 8, color: "var(--text-faint)",
                       textTransform: "none", letterSpacing: 0, fontSize: 11 }}>
          ({(d.activity_timeline || []).length} events, newest first)
        </span>
      </h3>
      <Card style={{ padding: 12, marginBottom: 14 }}>
        <div data-testid="admin-user-activity-list"
             style={{ maxHeight: 320, overflowY: "auto", fontSize: 12,
                      fontFamily: "'JetBrains Mono', monospace" }}>
          {(d.activity_timeline || []).length === 0 && (
            <div style={{ color: "var(--text-faint)", padding: "8px 0" }}>
              No activity recorded yet.
            </div>
          )}
          {(d.activity_timeline || []).map((evt, i) => {
            const f = formatTimelineRow(evt);
            const kindColor =
              evt.kind === "offer" ? "#a855f7" :
              evt.kind === "admin" ? "#eab308" :
              evt.kind === "auth"  ? "#22c55e" :
              evt.kind === "task"  ? "var(--accent-2)" :
              "var(--text-dim)";
            return (
              <div key={i}
                   data-testid={`admin-user-activity-row-${i}`}
                   style={{ padding: "6px 4px", borderBottom: "1px solid var(--border)",
                            display: "grid",
                            gridTemplateColumns: "20px 1fr auto",
                            gap: 10, alignItems: "center" }}>
                <span style={{ color: kindColor, fontSize: 14 }}>{f.icon}</span>
                <span>
                  <span style={{ color: kindColor, fontWeight: 500 }}>{f.label}</span>
                  {f.tail && (
                    <span style={{ color: "var(--text-dim)", marginLeft: 6 }}>{f.tail}</span>
                  )}
                </span>
                <span style={{ color: "var(--text-faint)", fontSize: 11 }}>
                  {ago(evt.at)}
                </span>
              </div>
            );
          })}
        </div>
      </Card>

      {/* 2026-08-20 · Ad-click attribution — which paid ad (if any)
          brought this real user in, joined with their funnel stage
          data above. Reads dev_users.ad_attribution (set once by
          POST /ads/attribute-click right after signup/OAuth). */}
      {d.ad_attribution && (
        <div data-testid="admin-user-ad-attribution"
             style={{ fontSize: 11, color: "var(--text-faint)", margin: "0 0 14px",
                      padding: "8px 10px", borderRadius: 6,
                      background: "rgba(143,184,255,0.06)",
                      border: "1px solid rgba(143,184,255,0.24)" }}>
          <strong style={{ color: "#8fb8ff" }}>
            Ad source: {d.ad_attribution.gclid ? "Google Ads" : d.ad_attribution.fbclid ? "Meta Ads" : (d.ad_attribution.utm_source || "unknown")}
          </strong>
          {d.ad_attribution.utm_campaign && <span> · campaign: {d.ad_attribution.utm_campaign}</span>}
          {d.ad_attribution.landing_path && <span> · landed on {d.ad_attribution.landing_path}</span>}
        </div>
      )}

      {/* 2026-08-20 · Funnel nudge emails sent to this user — stage,
          sent time, and whether they clicked through. Reads
          onboarding_emails via GET /admin/users/{id} (emails_sent). */}
      <h3 data-testid="admin-user-emails-sent-header"
          style={{ fontSize: 12, letterSpacing: "0.1em",
                   textTransform: "uppercase",
                   color: "var(--text-faint)", margin: "0 0 8px" }}>
        Emails sent
        <span style={{ marginLeft: 8, color: "var(--text-faint)",
                       textTransform: "none", letterSpacing: 0, fontSize: 11 }}>
          ({(d.emails_sent || []).length} funnel nudge{(d.emails_sent || []).length === 1 ? "" : "s"})
        </span>
      </h3>
      <Card style={{ padding: 12, marginBottom: 14 }}>
        <div data-testid="admin-user-emails-sent-list"
             style={{ maxHeight: 240, overflowY: "auto", fontSize: 12 }}>
          {(d.emails_sent || []).length === 0 && (
            <div style={{ color: "var(--text-faint)", padding: "8px 0" }}>
              No nudge emails sent to this user yet.
            </div>
          )}
          {(d.emails_sent || []).map((e, i) => (
            <div key={i}
                 data-testid={`admin-user-email-sent-row-${i}`}
                 style={{ padding: "6px 4px", borderBottom: "1px solid var(--border)",
                          display: "grid",
                          gridTemplateColumns: "1fr auto auto", gap: 10, alignItems: "center" }}>
              <span>
                <span style={{ color: "var(--text)", fontWeight: 500 }}>
                  {(e.stage || "").replace(/^stage\d_/, "").replace(/_/g, " ")}
                </span>
                {!e.sent_ok && (
                  <span style={{ color: "var(--danger, #e84646)", marginLeft: 6, fontSize: 10 }}>send failed</span>
                )}
              </span>
              <span style={{ color: "var(--text-faint)", fontSize: 11 }}>
                sent {ago(e.sent_at)}
              </span>
              <span style={{ fontSize: 11, color: e.clicked_at ? "#3ECF8E" : "var(--text-faint)" }}>
                {e.clicked_at ? `clicked ${ago(e.clicked_at)} (×${e.click_count || 1})` : "not clicked"}
              </span>
            </div>
          ))}
        </div>
      </Card>

      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        Support tickets{" "}
        <span style={{ color: "var(--text-faint)", fontSize: 10 }}>
          ({(d.support_tickets || []).length}
          {(d.support_tickets || []).filter(t => t.status === "open").length > 0
            && `, ${(d.support_tickets || []).filter(t => t.status === "open").length} open`}
          )
        </span>
      </h3>
      <Card style={{ padding: 0, marginBottom: 16 }} data-testid="admin-user-support-tickets">
        {(d.support_tickets || []).length === 0 ? (
          <div style={{ padding: 16, color: "var(--text-faint)", fontSize: 12 }}>
            No tickets from this user.
          </div>
        ) : (
          (d.support_tickets || []).map((t) => (
            <div
              key={t.ticket_id}
              data-testid={`admin-user-support-ticket-${t.ticket_id}`}
              style={{
                padding: "10px 14px",
                borderBottom: "1px solid var(--border)",
                display: "flex", justifyContent: "space-between",
                alignItems: "center", gap: 12,
              }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 12, fontWeight: 600,
                              whiteSpace: "nowrap", overflow: "hidden",
                              textOverflow: "ellipsis" }}>
                  {t.subject || "(no subject)"}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-faint)",
                              marginTop: 2, display: "flex", gap: 8,
                              alignItems: "center" }}>
                  <span>{ago(t.created_at)}</span>
                  {t.source && (
                    <span style={{
                      fontSize: 10, fontFamily: "monospace",
                      padding: "1px 6px", border: "1px solid var(--border)",
                      borderRadius: 3,
                    }}>{t.source}</span>
                  )}
                </div>
              </div>
              <Badge color={STATUS_COLOR[t.status === "resolved" ? "done"
                : (t.status === "open" ? "failed" : "running")]}>
                {t.status}
              </Badge>
            </div>
          ))
        )}
      </Card>

      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>Recent tasks</h3>
      <Card>
        <Table
          cols={["Task", "Status", "Commit", "Time"]}
          rows={(d.recent_tasks || []).map((t) => [
            <span key="task" style={{ maxWidth: 400, overflow: "hidden", textOverflow: "ellipsis",
                            whiteSpace: "nowrap", display: "block" }}>{t.task}</span>,
            <Badge key="status" color={STATUS_COLOR[t.status]}>{t.status}</Badge>,
            t.commit_sha ? <span key="commit" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{t.commit_sha}</span> : "—",
            <span key="time" style={{ color: "var(--text-faint)" }}>{ago(t.created_at)}</span>,
          ])}
        />
      </Card>
    </div>
  );
}

function ProjectsPage() {
  const [data, setData] = useState([]);
  const [graph, setGraph] = useState([]);
  useEffect(() => {
    api.get("/admin/projects").then((r) => setData(r.data.projects || [])).catch(() => {});
    // Iter 192 — merged Graph Status into Projects. The /graph-status
    // endpoint returns has_graph + graph_node_count + graph_built_at
    // per project; we left-join in the table so each project row shows
    // its graph state without a second page.
    api.get("/admin/graph-status", { params: { limit: 200 } })
      .then((r) => setGraph(r.data?.rows || []))
      .catch(() => {});
  }, []);
  const graphByProject = useMemo(() => {
    const m = {};
    for (const g of graph) m[g.project_id] = g;
    return m;
  }, [graph]);
  const built = graph.filter((g) => g.has_graph).length;
  return (
    <div style={{ padding: 24 }}>
      {/* Graph coverage summary (merged from the old Graph Status tab). */}
      {!!graph.length && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)",
                      gap: 12, marginBottom: 14 }}>
          <MCard label="Projects with graph" value={built} sub={`of ${graph.length}`} />
          <MCard label="Graph coverage"
                  value={graph.length ? `${Math.round(100 * built / graph.length)}%` : "—"}
                  accent={graph.length && built / graph.length >= 0.6 ? "var(--ok)" : "var(--warn)"} />
          <MCard label="Total projects" value={data.length} />
        </div>
      )}
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        All projects ({data.length})
      </h3>
      <Card>
        <Table
          cols={["Name", "Repo", "Branch", "Stack", "Tasks", "Graph", "Nodes", "Graph built", "User", "Created"]}
          rows={data.map((p) => {
            const g = graphByProject[p.project_id] || {};
            return [
              p.name,
              <span key="repo" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
                {p.github_owner}/{p.github_repo}
              </span>,
              p.branch,
              <Badge key="stack">{p.tech_stack || "auto"}</Badge>,
              p.tasks_done ?? 0,
              <Badge key="graph"
                      color={g.has_graph ? "var(--ok)" : "var(--text-faint)"}>
                {g.has_graph ? "yes" : "no"}
              </Badge>,
              <span key="nodes" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
                {g.graph_node_count ?? "—"}
              </span>,
              <span key="gbuilt" style={{ color: "var(--text-faint)" }}>
                {g.graph_built_at ? ago(g.graph_built_at) : "—"}
              </span>,
              <span key="user" style={{ color: "var(--text-faint)", fontSize: 11 }}>
                {(p.user_id || "").slice(0, 10)}
              </span>,
              <span key="time" style={{ color: "var(--text-faint)" }}>{ago(p.created_at)}</span>,
            ];
          })}
        />
      </Card>
    </div>
  );
}

function TasksPage() {
  const [data, setData] = useState([]);
  const [status, setStatus] = useState("");

  useEffect(() => {
    api.get("/admin/tasks", { params: { status, limit: 100 } })
      .then((r) => setData(r.data.tasks || [])).catch(() => {});
  }, [status]);

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
        <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                      color: "var(--text-faint)", margin: 0 }}>
          Tasks ({data.length})
        </h3>
        <select
          data-testid="admin-tasks-status"
          value={status} onChange={(e) => setStatus(e.target.value)}
          className="input" style={{ width: 140 }}>
          <option value="">All</option>
          <option value="done">Done</option>
          <option value="failed">Failed</option>
          <option value="running">Running</option>
          <option value="queued">Queued</option>
        </select>
      </div>
      <Card>
        <Table
          cols={["Task ID", "Task", "User", "Status", "Commit", "Created"]}
          rows={data.map((t) => [
            <span key="id" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              {t.task_id?.slice(0, 12)}
            </span>,
            <span key="task" style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis",
                            whiteSpace: "nowrap", display: "block" }}>{t.task}</span>,
            <span key="user" style={{ color: "var(--text-faint)", fontSize: 11 }}>
              {(t.user_id || "").slice(0, 10)}
            </span>,
            <Badge key="status" color={STATUS_COLOR[t.status]}>{t.status}</Badge>,
            t.commit_sha ? <span key="commit" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{t.commit_sha}</span> : "—",
            <span key="time" style={{ color: "var(--text-faint)" }}>{ago(t.created_at)}</span>,
          ])}
        />
      </Card>
    </div>
  );
}

function TokenPnL() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/token-pnl").then((r) => setD(r.data)).catch(() => {});
  }, []);
  if (!d) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: 18 }}>
        <MCard label="Revenue (mo)" value={fmtMoney(d.revenue_month)} sub="Stripe pending" />
        <MCard label="AI cost (mo)" value={fmtMoney(d.ai_cost_month)} accent="var(--danger)" />
        <MCard label="Net profit" value={fmtMoney(d.net_profit)}
                accent={d.net_profit >= 0 ? "var(--ok)" : "var(--danger)"} />
        <MCard label="Tasks done (mo)" value={d.tasks_done_month} sub={`${d.tasks_done_today} today`} />
      </div>
      {d._note && (
        <Card style={{ padding: 12, fontSize: 12, color: "var(--text-dim)" }}>
          <b>Note:</b> {d._note}
        </Card>
      )}
    </div>
  );
}

function ComingSoon({ title, note }) {
  return (
    <div style={{ padding: 24 }}>
      <Card style={{ padding: 24, textAlign: "center", color: "var(--text-faint)" }}>
        <h3 style={{ fontSize: 14, marginBottom: 8 }}>{title}</h3>
        <div style={{ fontSize: 12 }}>{note}</div>
      </Card>
    </div>
  );
}

// ── Iter 188 — new admin sections ──────────────────────────────────────
// Each section maps 1:1 to a backend endpoint added in the same iter:
//   /admin/agent-performance, /admin/mcp-usage, /admin/warm-start-stats,
//   /admin/graph-status, /admin/postscan-issues, /admin/overview-metrics.
// All pages share the same loading-skeleton + Card/Table primitives
// used by the existing tabs so the UI stays consistent.

function AgentPerformancePage() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/agent-performance").then((r) => setD(r.data)).catch(() => setD({ per_model_30d: [] }));
  }, []);
  if (!d) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  const rows = d.per_model_30d || [];
  return (
    <div style={{ padding: 24 }}>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        Agent performance — last 30 days ({rows.length} models)
      </h3>
      <Card>
        <Table
          cols={["Model", "Calls", "Total Cost", "Avg Input Tok", "Avg Output Tok"]}
          rows={rows.map((r) => [
            <span key="m" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              {r.model || "—"}
            </span>,
            r.calls,
            typeof r.total_cost_usd === "number" ? `$${r.total_cost_usd.toFixed(4)}` : "—",
            r.avg_input_tokens ?? "—",
            r.avg_output_tokens ?? "—",
          ])}
        />
      </Card>
      {!rows.length && (
        <Card style={{ padding: 16, marginTop: 12, color: "var(--text-faint)", fontSize: 12 }}>
          No model usage yet in the last 30 days. Run a task to populate this view.
        </Card>
      )}
    </div>
  );
}

function McpUsagePage() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/mcp-usage", { params: { limit: 100 } })
      .then((r) => setD(r.data)).catch(() => setD({ rows: [] }));
  }, []);
  if (!d) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  const rows = d.rows || [];
  return (
    <div style={{ padding: 24 }}>
      {/* Iter 192 — API Keys CTA. Keys ARE MCP credentials, so the
          quick-link to the full key management page lives here
          (was a separate Overview button before). */}
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 12, gap: 12 }}>
        <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                      color: "var(--text-faint)", margin: 0 }}>
          MCP API keys ({rows.length})
        </h3>
        <a
          data-testid="goto-api-keys"
          href="/admin/api-keys"
          style={{
            fontSize: 11, fontWeight: 600, letterSpacing: ".04em",
            padding: "6px 12px",
            background: "transparent",
            color: "var(--accent, #ff8a2a)",
            border: "1px solid var(--accent, #ff8a2a)",
            borderRadius: 5,
            textDecoration: "none",
          }}
        >🔑 Manage API Keys →</a>
      </div>
      <Card>
        <Table
          cols={["Key", "User", "Client", "Scope", "Last used", "Created"]}
          rows={rows.map((r) => [
            <span key="k" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              …{r.key_tail || "??????"}
            </span>,
            <span key="u" style={{ color: "var(--text-faint)", fontSize: 11 }}>
              {(r.user_id || "").slice(0, 12)}
            </span>,
            <Badge key="c">{r.client_id || "—"}</Badge>,
            <span key="s" style={{ fontSize: 11, color: "var(--text-dim)" }}>{r.scope || "mcp"}</span>,
            <span key="lu" style={{ color: "var(--text-faint)" }}>
              {r.last_used_at ? ago(r.last_used_at) : "never"}
            </span>,
            <span key="ca" style={{ color: "var(--text-faint)" }}>{ago(r.created_at)}</span>,
          ])}
        />
      </Card>
      {!rows.length && (
        <Card style={{ padding: 16, marginTop: 12, color: "var(--text-faint)", fontSize: 12 }}>
          No MCP keys yet. Users mint keys from Settings → MCP keys.
        </Card>
      )}
    </div>
  );
}

// Iter 192 — Reliability page merges the old Warm Start and Post-scan
// Issues tabs into one operational-health surface. Top half is warm
// start (cold-boot speed + success), bottom half is Vanguard post-scan
// findings (security regressions caught after commit).
function ReliabilityPage() {
  const [warm, setWarm] = useState(null);
  const [scan, setScan] = useState(null);
  useEffect(() => {
    api.get("/admin/warm-start-stats")
      .then((r) => setWarm(r.data))
      .catch(() => setWarm({ breakdown_7d: {} }));
    api.get("/admin/postscan-issues", { params: { limit: 100 } })
      .then((r) => setScan(r.data))
      .catch(() => setScan({ rows: [] }));
  }, []);

  if (!warm || !scan) {
    return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  }

  const breakdown = warm.breakdown_7d || {};
  const warmTotal = Object.values(breakdown).reduce((a, b) => a + b, 0);
  const warmDone  = breakdown.done || 0;
  const warmPct   = warmTotal ? Math.round(100 * warmDone / warmTotal) : 0;
  const issues = scan.rows || [];
  const critical = issues.filter((r) => r.severity === "critical").length;
  const warning  = issues.filter((r) => ["warning", "warn"].includes(r.severity)).length;

  return (
    <div style={{ padding: 24 }}>
      {/* Warm Start */}
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        Warm Start — last 24 h / 7 d / 30 d
      </h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 14 }}>
        <MCard label="Avg warm time" value={warm.avg_seconds ? `${warm.avg_seconds}s` : "—"}
                sub="Last 100 done jobs · 30 d" />
        <MCard label="Success rate (7 d)" value={warmTotal ? `${warmPct}%` : "—"}
                accent={warmPct >= 90 ? "var(--ok)" : warmPct >= 70 ? "var(--warn)" : "var(--danger)"}
                sub={`${warmDone}/${warmTotal} jobs`} />
        <MCard label="Total jobs (7 d)" value={warmTotal} sub={`${warm.window_days}-day window`} />
      </div>
      <Card style={{ marginBottom: 28 }}>
        <Table
          cols={["Status", "Count"]}
          rows={Object.entries(breakdown).map(([k, v]) => [
            <Badge key="s" color={k === "done" ? "var(--ok)" : k === "failed" ? "var(--danger)" : undefined}>{k}</Badge>,
            v,
          ])}
        />
      </Card>

      {/* Post-scan */}
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        Post-scan issues — Vanguard 007 findings
      </h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 14 }}>
        <MCard label="Critical findings" value={critical} accent="var(--danger)"
                sub="Commit blockers" />
        <MCard label="Warnings" value={warning} accent="var(--warn)" />
        <MCard label="Total shown" value={issues.length} />
      </div>
      <Card>
        <Table
          cols={["Severity", "Rule", "File", "Match", "Task", "When"]}
          rows={issues.map((r) => [
            <Badge key="s" color={r.severity === "critical" ? "var(--danger)" : "var(--warn)"}>
              {r.severity}
            </Badge>,
            <span key="r" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              {r.rule || "—"}
            </span>,
            <span key="f" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
                                    maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis",
                                    whiteSpace: "nowrap", display: "block" }}>
              {r.file || "—"}
            </span>,
            <span key="m" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
                                    color: "var(--text-faint)",
                                    maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis",
                                    whiteSpace: "nowrap", display: "block" }}>
              {r.match || ""}
            </span>,
            <span key="t" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              {(r.task_id || "").slice(0, 10)}
            </span>,
            <span key="w" style={{ color: "var(--text-faint)" }}>{ago(r.created_at)}</span>,
          ])}
        />
      </Card>
      {!issues.length && (
        <Card style={{ padding: 16, marginTop: 12, color: "var(--text-faint)", fontSize: 12 }}>
          No post-scan findings recorded. Vanguard 007 runs on every commit — empty here
          means clean ships.
        </Card>
      )}
    </div>
  );
}




// Session G · Item 3 — Cron-death dashboard tile.
// Reads `supervised_tasks: {supervised_count, alive[], dead[]}` from
// the public `/api/health` payload (same field the founder can curl).
// Green + count when all crons alive; red + list of dead names when
// any long-lived cron silently terminated since pod boot. Guard 20
// already opens an incident row for each — this tile just surfaces
// the postmortem list at a glance instead of requiring a curl.
export function SupervisedTasksTile() {
  const [d, setD] = useState(null);
  useEffect(() => {
    const load = () =>
      fetch(`${process.env.REACT_APP_BACKEND_URL}/api/health`, {
        signal: AbortSignal.timeout(5000),
      })
        .then((r) => r.json())
        .then((j) => setD(j.supervised_tasks || null))
        .catch(() => {});
    load();
    // Refresh every 30s so a fresh death appears without a page reload.
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, []);
  if (!d) return null;
  const dead = d.dead || [];
  const alive = d.alive || [];
  const total = d.supervised_count ?? (alive.length + dead.length);
  const hasDead = dead.length > 0;
  return (
    <div data-testid="supervised-tasks-tile" style={{ marginTop: 22 }}>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em",
        textTransform: "uppercase", color: "var(--text-faint)",
        margin: "0 0 8px" }}>Supervised background tasks</h3>
      <Card style={{ padding: 16 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 14,
                       flexWrap: "wrap" }}>
          <div
            data-testid="supervised-tasks-status"
            style={{ fontSize: 22, fontWeight: 700,
              color: hasDead ? "var(--danger)" : "var(--ok)",
              fontFamily: "'JetBrains Mono', monospace",
              textTransform: "uppercase" }}
          >
            {alive.length}/{total} alive
          </div>
          <div style={{ fontSize: 11, color: "var(--text-faint)",
                          lineHeight: 1.8 }}>
            {hasDead
              ? `${dead.length} cron${dead.length === 1 ? "" : "s"} silently terminated since boot — Guard 20 incident row opened for each.`
              : "every long-lived cron is running normally."}
          </div>
        </div>
        {hasDead && (
          <div data-testid="supervised-tasks-dead-list"
                style={{ marginTop: 12, display: "flex",
                          flexDirection: "column", gap: 6 }}>
            {dead.map((row) => (
              <div key={row.name}
                    data-testid={`supervised-task-dead-${row.name}`}
                    style={{ display: "flex", alignItems: "center",
                              gap: 10, flexWrap: "wrap", fontSize: 12,
                              fontFamily: "'JetBrains Mono', monospace" }}>
                <Badge color="var(--danger)">DEAD</Badge>
                <b style={{ color: "var(--danger)" }}>{row.name}</b>
                <span style={{ color: "var(--text-faint)" }}>
                  {row.reason === "exception"
                    ? `${row.exc_type || "Exception"}: ${row.exc_msg || ""}`
                    : row.reason === "silent_completion"
                    ? "silent completion (cron returned without looping)"
                    : row.reason}
                </span>
                {row.died_at_iso && (
                  <span style={{ color: "var(--text-faint)",
                                    fontSize: 10 }}>
                    · died {row.died_at_iso}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
        {!hasDead && alive.length > 0 && (
          <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap",
                          gap: 6 }}>
            {alive.map((name) => (
              <Badge key={name} color="var(--ok)"
                      data-testid={`supervised-task-alive-${name}`}>
                {name}
              </Badge>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

// Iter 350 — Loop intent-gate observability tile. Exported for tests.
export function IntentGateTile() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/loop/intent-stats?hours=24").then((r) => setD(r.data)).catch(() => {});
  }, []);
  if (!d) return null;
  const t = d.totals || {};
  const maxBar = Math.max(1, ...(d.hourly || []).map(
    (h) => (h.chat_redirect || 0) + (h.loop_triggered || 0)));
  return (
    <div data-testid="intent-gate-tile" style={{ marginBottom: 18 }}>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em",
        textTransform: "uppercase", color: "var(--text-faint)",
        margin: "0 0 8px" }}>Loop Intent Gate · last 24h</h3>
      <Card style={{ padding: 16 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
          <div data-testid="intent-gate-redirect-rate"
            style={{ fontSize: 26, fontWeight: 700,
                     color: "var(--ok)",
                     fontFamily: "'JetBrains Mono', monospace" }}>
            {d.redirect_rate == null ? "—" : `${Math.round(d.redirect_rate * 100)}%`}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-faint)", lineHeight: 1.8 }}>
            <span data-testid="intent-gate-totals">
              chat redirects {t.chat_redirect ?? 0} · loops triggered {t.loop_triggered ?? 0}
              {" · "}
              <span style={{ color: (t.timeout_failed || 0) > 0 ? "var(--danger)" : "inherit" }}>
                plan timeouts {t.timeout_failed ?? 0}
              </span>
            </span>
          </div>
          <div style={{ marginLeft: "auto", display: "flex", gap: 2, alignItems: "flex-end", height: 22 }}>
            {(d.hourly || []).map((h, i) => {
              const total = (h.chat_redirect || 0) + (h.loop_triggered || 0);
              return (
                <div key={i}
                  title={`${h.hour_key}Z — chat ${h.chat_redirect || 0} · loop ${h.loop_triggered || 0} · timeout ${h.timeout_failed || 0}`}
                  style={{
                    width: 5,
                    height: Math.max(2, Math.round((total / maxBar) * 22)),
                    background: (h.timeout_failed || 0) > 0 ? "var(--danger)"
                              : total === 0 ? "var(--text-faint)"
                              : "var(--ok)",
                    opacity: total === 0 ? 0.25 : 0.9,
                  }} />
              );
            })}
          </div>
        </div>
      </Card>
    </div>
  );
}

// Iter 331 · PRD #3-e — ORA learning-health tile. Exported for tests.
export function LearningHealthTile() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/learning-health").then((r) => setD(r.data)).catch(() => {});
  }, []);
  if (!d) return null;
  const color = d.status === "green" ? "var(--ok)"
              : d.status === "empty" ? "var(--text-faint)"
              : "var(--danger)";
  const brain = d.brain || {};
  const patterns = d.patterns || {};
  const council = d.council_logs || {};
  const canary = d.canary || {};
  return (
    <div data-testid="learning-health-tile" style={{ marginBottom: 18 }}>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em",
        textTransform: "uppercase", color: "var(--text-faint)",
        margin: "0 0 8px" }}>ORA Learning Health</h3>
      <Card style={{ padding: 16 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
          <div
            data-testid="learning-health-status"
            style={{ fontSize: 22, fontWeight: 700, color,
                     fontFamily: "'JetBrains Mono', monospace",
                     textTransform: "uppercase" }}
          >
            {d.status}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-faint)", lineHeight: 1.8 }}>
            <span data-testid="learning-health-brain">
              brains {brain.count ?? 0}
              {brain.age_hours != null
                ? ` · last write ${brain.age_hours}h ago (${brain.project_id || "—"})`
                : " · never written"}
            </span>
            {" · "}
            <span data-testid="learning-health-patterns">
              patterns {patterns.count ?? 0}
            </span>
            {" · "}
            <span data-testid="learning-health-council">
              council logs {council.count ?? 0} ({council.last_24h ?? 0} in 24h)
            </span>
          </div>
          <div style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Badge color={canary.enabled ? "var(--ok)" : "var(--text-faint)"}>
              canary · {canary.enabled ? "ON" : "OFF"}
              {canary.last_run ? " · ran" : " · no runs yet"}
            </Badge>
            <Badge color={d.eval_cron_enabled ? "var(--ok)" : "var(--text-faint)"}>
              eval cron · {d.eval_cron_enabled ? "ON" : "OFF"}
            </Badge>
            {d.learning_disabled_flag && (
              <Badge color="var(--danger)">ORA_LEARNING_DISABLED=1</Badge>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}





// ── Shell ──────────────────────────────────────────────────────────────
// Iter 192 — sidebar consolidated per founder feedback:
//   • API Keys: lived as a separate top-level page → now opened from
//     inside MCP Usage (keys ARE MCP credentials).
//   • Revenue + Payments: merged into one "Payments & Revenue" tab.
//   • Architecture: was its own tab → now lives inside Overview.
//   • Graph Status: was its own tab → folded into Projects (per-project
//     row already shows graph state).
// ──────────────────────────────────────────────────────────────────



//   • Warm Start + Post-scan Issues: merged into one "Reliability" tab.
const NAV = buildGroupedAdminNav({
  cockpit: Eye,
  overview: Eye,
  llm_credits: DollarSign,
  parliament_live: Cpu,
  // Iter 307 — QA Health dashboard is a separate route (/admin/qa),
  // so `route:` short-circuits the internal setPage(id) switch and
  // uses react-router's navigate(...) instead. Same visual pattern
  // as every other sidebar entry; keeps the founder one click away
  // from the live QA metrics without having to memorize a URL.
  qa_health: ShieldCheck,
  maintenance: Wrench,
  // Iter 331 — Architecture() (learning-health + persona-quality +
  // code-surface tiles) was defined but never wired into renderPage;
  // /admin/architecture silently fell through to Overview.
  arch: Cpu,
  bin_tracker: Users,
  github_bulk_revoke: ShieldAlert,
  users: Users,
  support: Mail,
  suggestions: MessageCircle,   // Iter 212m-193
  audit: ShieldAlert,
  feature_flags: Zap,
  house_rules: ShieldCheck,
  robot_guide: MessageCircle,
  // 2026-08-27 · Admin Compact M4 — folded in from the rail's
  // rail-only ADMIN_ITEMS; previously unreachable from the sidebar.
  api_keys: KeyRound,
  payments: DollarSign,
  tokens: Cpu,
  dash: LayoutDashboard,
  projects: Folder,
  tasks: ListChecks,
  agent_perf: Activity,
  mcp: Plug,
  reliability: ShieldAlert,
  settings: SettingsIcon,
});

export default function Admin({ initialTab = "overview" }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [page, setPage] = useState(initialTab);
  const [selectedUser, setSelectedUser] = useState(null);
  const [me, setMe] = useState(null);

  // Feb 2026 · Sidebar Toggle — hamburger-controlled sidebar visibility.
  // Persisted per-device via localStorage so the founder's preference
  // survives reloads. Defaults: OPEN on desktop, CLOSED on mobile so
  // the drawer overlay doesn't block the initial view on small screens.
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try {
      const v = localStorage.getItem("aurem_admin_sidebar_open");
      if (v === "1") return true;
      if (v === "0") return false;
    } catch { /* ignore */ }
    if (typeof window === "undefined") return true;
    return window.matchMedia("(min-width: 901px)").matches;
  });
  const toggleSidebar = useCallback(() => {
    setSidebarOpen((v) => {
      const next = !v;
      try { localStorage.setItem("aurem_admin_sidebar_open", next ? "1" : "0"); }
      catch { /* ignore */ }
      return next;
    });
  }, []);
  // Close drawer whenever route/tab changes on mobile so the newly
  // rendered page isn't hidden behind the drawer.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.matchMedia("(max-width: 900px)").matches) {
      setSidebarOpen(false);
    }
  }, [page, location.pathname]);

  // Feb 2026 · Sidebar Integrity fix — when the URL changes because
  // the user clicked a sidebar item that carries a `route:` field (or
  // hit browser Back/Forward), react-router hands us a fresh
  // `initialTab` prop but useState above only reads it on FIRST mount.
  // Without this sync the sidebar highlight moves to the new URL but
  // the page body still shows the previous tab's component. Also
  // clear any per-user drilldown state so /admin/users → /admin/tasks
  // doesn't leak a selected user into the new page.
  useEffect(() => {
    setPage(initialTab);
    setSelectedUser(null);
  }, [initialTab]);

  // 2026-08-27 — Per-Customer Drilldown. The Live Cost Alert card
  // (AdminOverview) links straight to a specific offending customer
  // via `/admin/users?drill_user=<id>` instead of making the founder
  // manually search for them in the Users list. UserDetail only ever
  // needs `user.user_id` (it re-fetches full detail itself), so a
  // minimal seed object is enough while the real fetch resolves.
  useEffect(() => {
    if (page !== "users") return;
    const params = new URLSearchParams(location.search);
    const drillId = params.get("drill_user");
    if (!drillId) return;
    setSelectedUser({ user_id: drillId });
    navigate("/admin/users", { replace: true });
  }, [page, location.search, navigate]);

  useEffect(() => {
    // Guard order matters: NO token at all → bounce to /login with a
    // `next=/admin` so we come right back after sign-in. With a token
    // but no admin → /dashboard (the regular app surface).
    // Previously we always went to /dashboard which, for unauthed users,
    // re-bounced to / — making /admin look like it "doesn't exist".
    const tok = localStorage.getItem("aurem_token");
    if (!tok) {
      navigate("/login?next=/admin", { replace: true });
      return;
    }
    api.get("/admin/me")
      .then((r) => setMe(r.data))
      .catch((err) => {
        const status = err?.response?.status;
        if (status === 401) {
          // Token expired / invalid — clear it and bounce to login
          localStorage.removeItem("aurem_token");
          localStorage.removeItem("aurem_user");
          navigate("/login?next=/admin", { replace: true });
        } else {
          // 403 or anything else — user is signed in but not admin
          toast({ message: "Admin access required", kind: "error" });
          navigate("/dashboard", { replace: true });
        }
      });
  }, [navigate]);

  // Toggle the network-mesh glass bg on body while the admin page is
  // mounted (Admin.jsx renders its own layout — it doesn't wrap in
  // Shell — so we need to opt-in to the same body class Shell uses).
  useEffect(() => {
    document.body.classList.add("aurem-glass");
    return () => { document.body.classList.remove("aurem-glass"); };
  }, []);

  if (!me) return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center",
                   background: "transparent", color: "var(--text-faint)" }}>
      <Loader2 size={18} className="spin" />
    </div>
  );

  function logout() {
    // Iter 307 — fire server-side revocation before wiping local state.
    // Fire-and-forget: sign-out UX should never hang on a slow backend.
    const tok = localStorage.getItem("aurem_token");
    if (tok) {
      try {
        fetch(`${process.env.REACT_APP_BACKEND_URL}/api/aurem-dev/auth/logout`, {
          method:  "POST",
          headers: { Authorization: `Bearer ${tok}` },
          keepalive: true,
          signal: AbortSignal.timeout(10000),
        }).catch(() => { /* ignore */ });
      } catch { /* ignore */ }
    }
    localStorage.removeItem("aurem_token");
    localStorage.removeItem("aurem_user");
    navigate("/login");
  }

  function go(id) { setPage(id); setSelectedUser(null); }

  const renderPage = () => {
    if (page === "users" && selectedUser) {
      return <UserDetail user={selectedUser} onBack={() => setSelectedUser(null)} />;
    }
    switch (page) {
      case "overview":       return <AdminOverview />;
      case "cockpit":        return <AdminCockpit />;
      case "arch":           return <AdminArchitecturePageLazy />;
      case "llm_credits":    return <div style={{ padding: "24px 20px", maxWidth: 900 }}>
                                       <h1 style={{ fontSize: 20, fontWeight: 600, margin: "0 0 16px", color: "var(--text)" }}>LLM Credits</h1>
                                       <LLMCreditMonitor />
                                     </div>;
      case "parliament_live":return <div style={{ padding: "24px 20px", maxWidth: 1100 }}>
                                       <h1 style={{ fontSize: 20, fontWeight: 600, margin: "0 0 16px", color: "var(--text)" }}>Parliament Live</h1>
                                       <ParliamentLivePanel />
                                     </div>;
      case "bin_tracker":    return <AdminBINTracker />;
      case "github_bulk_revoke": return <AdminGithubBulkRevoke />;
      case "feature_flags":  return <AdminFeatureFlags />;
      case "dash":           return <Dashboard />;
      case "users":          return <UsersList onSelect={setSelectedUser} />;
      case "projects":       return <ProjectsPage />;
      case "tasks":          return <TasksPage />;
      case "tokens":         return <TokenPnL />;
      case "agent_perf":     return <AgentPerformancePage />;
      case "mcp":            return <McpUsagePage />;
      case "reliability":    return <ReliabilityPage />;
      case "payments":       return <AdminFinancialsLazy />;
      case "support":        return <AdminSupportPageLazy />;
      case "suggestions":    return <AdminSuggestions />;
      case "audit":          return <AdminAuditPageLazy />;
      case "house_rules":    return <AdminHouseRules />;
      case "robot_guide":    return <AdminRobotGuide />;
      case "settings":       return <AdminSettingsPageLazy />;
      default:               return <AdminOverview />;
    }
  };

  return (
    <div className="aurem-admin-shell"
         data-sidebar-open={sidebarOpen ? "true" : "false"}
         data-drawer-open={sidebarOpen ? "true" : "false"}
         style={{
      height: "100vh", maxHeight: "100vh", overflow: "hidden",
      display: "grid",
      gridTemplateColumns: sidebarOpen ? "220px 1fr" : "0 1fr",
      transition: "grid-template-columns 240ms cubic-bezier(0.4, 0, 0.2, 1)",
      background: "transparent",
    }}>
      {/* Feb 2026 · Sidebar backdrop — mobile only, taps close the drawer. */}
      {sidebarOpen && (
        <div
          className="aurem-admin-backdrop"
          data-testid="admin-sidebar-backdrop"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside
        className="glass-sidebar"
        style={{
          padding: "20px 12px",
          display: "flex", flexDirection: "column",
          height: "100vh", overflow: "hidden", minHeight: 0,
        }}
      >
        <div style={{ padding: "0 8px 16px",
                       borderBottom: "1px solid var(--border)", marginBottom: 12,
                       flexShrink: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 14, color: "var(--text)" }}>
            AUREM
          </div>
          <div style={{ fontSize: 10, color: "var(--text-faint)",
                         letterSpacing: "0.1em", textTransform: "uppercase" }}>
            Admin Panel
          </div>
        </div>
        <div className="aurem-rail-scroll" data-testid="admin-nav-scroll">
        {NAV.map((item, idx) => {
          // Iter 212m-171 — support group header entries.
          if (item.group) {
            return (
              <div key={`group-${idx}`}
                   data-testid={`admin-nav-group-${item.group}`}
                   style={{
                     fontSize: 9, letterSpacing: "0.12em",
                     textTransform: "uppercase", color: "var(--text-faint)",
                     padding: "10px 10px 4px", marginTop: idx > 0 ? 8 : 0,
                   }}>
                {item.group}
              </div>
            );
          }
          const { id, label, Icon, route } = item;
          const active = route ? location.pathname === route : page === id;
          return (
            <button
              key={id}
              data-testid={`admin-nav-${id}`}
              onClick={() => (route ? navigate(route) : go(id))}
              className="btn-ghost"
              style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "8px 10px", marginBottom: 2,
                background: active ? "var(--bg-elev)" : "transparent",
                color: active ? "var(--text)" : "var(--text-dim)",
                border: "none", fontSize: 12, textAlign: "left",
                width: "100%", cursor: "pointer", borderRadius: 4,
                whiteSpace: "nowrap",
              }}>
              <Icon size={14} /> {label}
            </button>
          );
        })}
        </div>
        <div style={{ marginTop: "auto", paddingTop: 12, flexShrink: 0,
                       borderTop: "1px solid var(--border)" }}>
          <div style={{ fontSize: 11, color: "var(--text-faint)",
                         padding: "0 8px", marginBottom: 8,
                         overflowWrap: "anywhere" }}>
            {me.email}
          </div>
          <Link to="/dashboard"
                style={{ display: "flex", alignItems: "center", gap: 8,
                          padding: "6px 8px", fontSize: 11,
                          color: "var(--text-faint)", textDecoration: "none" }}>
            <ExternalLink size={11} /> back to app
          </Link>
          <Link to="/admin/personal-track"
                data-testid="admin-nav-personal-track"
                style={{ display: "flex", alignItems: "center", gap: 8,
                          padding: "6px 8px", fontSize: 11,
                          color: "var(--text-faint)", textDecoration: "none" }}>
            <Zap size={11} /> Personal Track ops
          </Link>
          <Link to="/admin/ora-chat"
                data-testid="admin-nav-ora-chat"
                style={{ display: "flex", alignItems: "center", gap: 8,
                          padding: "6px 8px", fontSize: 11,
                          color: "var(--text-faint)", textDecoration: "none" }}>
            <MessageCircle size={11} /> ORA Chat
          </Link>
          <button onClick={logout} className="btn-ghost"
                  data-testid="admin-logout"
                  style={{ display: "flex", alignItems: "center", gap: 8,
                            padding: "6px 8px", fontSize: 11,
                            border: "none", color: "var(--text-faint)",
                            background: "transparent", width: "100%",
                            cursor: "pointer", textAlign: "left" }}>
            <LogOut size={11} /> sign out
          </button>
        </div>
      </aside>
      <main style={{ overflow: "auto", height: "100vh", maxHeight: "100vh", minWidth: 0 }}>
        {/* Feb 2026 — sticky top bar for the bell. Positioned so it
            doesn't overlap page content; page renders below.  */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "12px 20px 0", position: "sticky", top: 0,
          zIndex: 10, background: "transparent", pointerEvents: "none",
        }}>
          <button
            type="button"
            data-testid="admin-sidebar-toggle"
            aria-label={sidebarOpen ? "Hide sidebar" : "Show sidebar"}
            title={sidebarOpen ? "Hide sidebar" : "Show sidebar"}
            onClick={toggleSidebar}
            style={{
              pointerEvents: "auto",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              width: 36, height: 36, borderRadius: 6,
              background: "rgba(13,16,24,0.78)",
              backdropFilter: "blur(8px)",
              border: "1px solid var(--border-strong)",
              color: "var(--accent-2)", cursor: "pointer",
              boxShadow: "0 4px 14px rgba(0,0,0,0.35)",
            }}
          >
            {sidebarOpen ? <X size={16} /> : <Menu size={16} />}
          </button>
          <div style={{ pointerEvents: "auto" }}>
            <NotificationBell />
          </div>
        </div>
        <React.Suspense fallback={
          <div style={{ padding: "24px 20px", fontSize: 12, color: "var(--text-faint)" }}>
            Loading…
          </div>
        }>
          {renderPage()}
        </React.Suspense>
      </main>
      {/* Iter 212m-238 — floating ORA Chat drawer, available on every admin tab */}
      <OraChatDrawer />
    </div>
  );
}
