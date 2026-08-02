/**
 * AdminCockpit.jsx — Unified cockpit page (Feb 2026)
 *
 * THIN consumer of /api/aurem-dev/admin/status/all — never computes
 * its own status. Three separate sections (per founder's 3-column
 * spec):
 *
 *   1. System Health — checks only. Donut + heartbeat + "Needs
 *                       Attention" (red-only) + "Setup Pending"
 *                       (gray-only). health_pct excludes gray.
 *   2. Mode Board   — chips: Stripe test/live, environment, etc.
 *                       Not checks. Not red/green dots.
 *   3. Business Pulse — metric cards from /admin/dashboard.
 *
 * Zero mocks. All data live. Inline-expand for red items — never
 * force a tab jump.
 */
import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import NotificationBell from "../components/NotificationBell";

const C = {
  bg:     "#0a0a0a",
  panel:  "#101013",
  border: "rgba(255,255,255,0.10)",
  text:   "#e5e5e5",
  faint:  "#5f5f5f",
  dim:    "#8a8a8a",
  amber:  "#f5a524",
  red:    "#ef4444",
  green:  "#22c55e",
  gray:   "#6b7280",
  mono:   "SFMono-Regular, Menlo, Consolas, monospace",
};

const POLL_MS = 30000;   // 30s cockpit poll — matches aggregator TTL

function Dot({ status, size = 8 }) {
  const color = status === "green" ? C.green
              : status === "red"   ? C.red
              : C.gray;
  return (
    <span style={{
      display: "inline-block",
      width: size, height: size,
      borderRadius: "50%",
      background: color,
      boxShadow: status === "red" ? `0 0 6px ${C.red}` : "none",
    }} />
  );
}

function useCockpitData() {
  const [payload, setPayload] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancel = false;
    const fetchNow = async () => {
      try {
        const r = await api.get("/admin/status/all");
        if (!cancel) { setPayload(r.data); setErr(null); }
      } catch (e) {
        if (!cancel) setErr(e?.message || "fetch failed");
      } finally {
        if (!cancel) setLoading(false);
      }
    };
    fetchNow();
    const t = setInterval(fetchNow, POLL_MS);
    return () => { cancel = true; clearInterval(t); };
  }, [refreshKey]);

  const refresh = () => setRefreshKey((k) => k + 1);
  return { payload, err, loading, refresh };
}

function HealthDonut({ counts }) {
  const g = counts?.green || 0;
  const r = counts?.red || 0;
  const denom = g + r;
  const pct = denom ? Math.round(100 * g / denom) : 0;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 20,
      padding: 18, background: C.panel, border: `1px solid ${C.border}`,
      borderRadius: 12,
    }}>
      <div style={{ position: "relative", width: 96, height: 96 }}>
        <svg viewBox="0 0 36 36" width="96" height="96">
          <circle cx="18" cy="18" r="15.9" fill="none"
            stroke="rgba(255,255,255,0.06)" strokeWidth="3" />
          <circle cx="18" cy="18" r="15.9" fill="none"
            stroke={r > 0 ? C.red : C.green} strokeWidth="3"
            strokeDasharray={`${pct}, 100`}
            transform="rotate(-90 18 18)" strokeLinecap="round" />
        </svg>
        <div style={{
          position: "absolute", inset: 0,
          display: "flex", alignItems: "center", justifyContent: "center",
          fontFamily: C.mono, fontSize: 22, color: C.text, fontWeight: 600,
        }}>
          {denom ? `${pct}%` : "—"}
        </div>
      </div>
      <div>
        <div style={{ fontFamily: C.mono, fontSize: 10,
             letterSpacing: "0.14em", color: C.faint, marginBottom: 6 }}>
          SYSTEM HEALTH
        </div>
        <div style={{ display: "flex", gap: 14, fontSize: 12, color: C.dim }}>
          <span><Dot status="green" /> <b style={{ color: C.text }}>{counts?.green || 0}</b> passing</span>
          <span><Dot status="red" />   <b style={{ color: C.red  }}>{counts?.red   || 0}</b> failing</span>
          <span><Dot status="gray" />  <b style={{ color: C.gray }}>{counts?.gray  || 0}</b> unconfigured</span>
        </div>
        <div style={{ fontSize: 10, color: C.faint, marginTop: 8, fontFamily: C.mono }}>
          health% excludes gray · total {counts?.total || 0}
        </div>
      </div>
    </div>
  );
}

function CheckRow({ c, expanded, onToggle, onAck }) {
  const dot = c.status;
  const link = (
    c.category === "guard"       ? "/admin/qa" :
    c.category === "integration" ? "/admin/integrations" :
    "/admin/system-health"
  );
  const canAck = c.status === "red" && !c.ack_active;
  return (
    <div data-testid={`cockpit-check-${c.id}`}
      style={{ padding: "10px 12px", borderBottom: `1px solid ${C.border}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}
           onClick={onToggle}>
        <Dot status={dot} />
        <span style={{ color: C.text, fontSize: 13, fontWeight: 500 }}>{c.name}</span>
        <span style={{ marginLeft: "auto", color: C.faint, fontSize: 10, fontFamily: C.mono }}>
          {c.category}
        </span>
      </div>
      {expanded && (
        <div style={{ marginLeft: 18, marginTop: 6 }}>
          <div style={{ fontSize: 11, color: C.dim }}>{c.detail || "—"}</div>
          <div style={{ fontSize: 10, color: C.faint, fontFamily: C.mono, marginTop: 4 }}>
            checked_at: {c.checked_at}
            {c.red_since ? ` · red since ${c.red_since}` : ""}
          </div>
          <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 6 }}>
            <Link to={link}
              data-testid={`cockpit-view-full-${c.id}`}
              style={{ fontSize: 11, color: C.amber }}>
              View full →
            </Link>
            {canAck && (
              <button
                data-testid={`cockpit-ack-${c.id}`}
                onClick={(e) => { e.stopPropagation(); onAck(c.id, 24); }}
                title="Mute this check for 24 hours"
                style={{
                  fontSize: 11, background: "transparent",
                  border: `1px solid ${C.border}`, color: C.dim,
                  padding: "2px 8px", borderRadius: 4, cursor: "pointer",
                  fontFamily: C.mono,
                }}>
                Ack 24h
              </button>
            )}
            {c.ack_active && (
              <button
                data-testid={`cockpit-unack-${c.id}`}
                onClick={(e) => { e.stopPropagation(); onAck(c.id, 0); }}
                title="Clear acknowledgement"
                style={{
                  fontSize: 11, background: "transparent",
                  border: `1px solid ${C.border}`, color: C.dim,
                  padding: "2px 8px", borderRadius: 4, cursor: "pointer",
                  fontFamily: C.mono,
                }}>
                Un-ack
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function CheckList({ title, filterFn, checks, testid, onAck }) {
  const [expanded, setExpanded] = useState(null);
  const rows = (checks || []).filter(filterFn);
  return (
    <div data-testid={testid}
         style={{ background: C.panel, border: `1px solid ${C.border}`,
                  borderRadius: 12, marginTop: 14 }}>
      <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.border}`,
                    fontFamily: C.mono, fontSize: 10, letterSpacing: "0.14em",
                    color: C.faint }}>
        {title} · {rows.length}
      </div>
      {rows.length === 0 && (
        <div style={{ padding: "18px", textAlign: "center", color: C.faint, fontSize: 12 }}>
          none
        </div>
      )}
      {rows.map((c) => (
        <CheckRow key={c.id} c={c}
          expanded={expanded === c.id}
          onToggle={() => setExpanded(expanded === c.id ? null : c.id)}
          onAck={onAck} />
      ))}
    </div>
  );
}

function ModeBoard() {
  // State (mode) surfaces — NOT health checks. Fed by real endpoints.
  const [ver, setVer] = useState(null);
  const [stripe, setStripe] = useState(null);
  useEffect(() => {
    api.get("/version").then(r => setVer(r.data)).catch(() => {});
    // Stripe mode is derivable from env: /admin/settings shows it,
    // but we can also infer from /admin/status/all int_stripe detail.
    api.get("/admin/status/all").then(r => {
      const s = (r.data?.checks || []).find(c => c.id === "int_stripe");
      setStripe(s || null);
    }).catch(() => {});
  }, []);

  return (
    <div data-testid="cockpit-mode-board"
         style={{ background: C.panel, border: `1px solid ${C.border}`,
                  borderRadius: 12, padding: 14, marginTop: 14 }}>
      <div style={{ fontFamily: C.mono, fontSize: 10, letterSpacing: "0.14em",
                    color: C.faint, marginBottom: 10 }}>
        MODE BOARD
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Chip label={`env: ${ver?.environment || "?"}`} />
        <Chip label={`deploy: ${ver?.commit_sha || "?"}`} mono />
        {ver?.last_github_push && (
          <Chip label={`gh: ${ver.last_github_push.commit_sha || "?"}`} mono />
        )}
        <Chip label={`stripe: ${stripe?.status === "green" ? "configured" : "not-set"}`} />
      </div>
    </div>
  );
}

function Chip({ label, mono }) {
  return (
    <span style={{
      padding: "4px 10px", borderRadius: 999,
      border: `1px solid ${C.border}`,
      fontSize: 11, color: C.dim,
      fontFamily: mono ? C.mono : "inherit",
    }}>{label}</span>
  );
}

function BusinessPulse() {
  const [d, setD] = useState(null);
  const [p, setP] = useState(null);
  useEffect(() => {
    api.get("/admin/dashboard").then(r => setD(r.data)).catch(() => {});
    api.get("/admin/pulse").then(r => setP(r.data)).catch(() => {});
  }, []);
  const users = d?.total_users || p?.total_users || 0;
  const dau   = d?.dau || d?.dau_today || 0;
  const rev   = d?.revenue_30d || d?.mrr || 0;
  const ghPct = p?.github_connect_pct;
  const paidNew = p?.paid_new_30d;
  return (
    <div data-testid="cockpit-business-pulse"
         style={{ display: "grid",
                  gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))",
                  gap: 10, marginTop: 14 }}>
      <MetricCard label="TOTAL USERS" value={users.toLocaleString()} />
      <MetricCard label="DAU (today)" value={dau.toLocaleString()} />
      <MetricCard label="REVENUE 30d" value={`$${Number(rev).toLocaleString()}`} />
      {ghPct != null && (
        <MetricCard label="GITHUB CONNECT %"
                    value={`${ghPct}%`}
                    sub={`${p?.github_connected || 0} of ${p?.total_users || 0}`} />
      )}
      {paidNew != null && (
        <MetricCard label="PAID UPGRADES 30d"
                    value={paidNew.toLocaleString()}
                    sub={`${p?.paid_users || 0} paid total`} />
      )}
    </div>
  );
}

function MetricCard({ label, value, sub }) {
  return (
    <div style={{
      background: C.panel, border: `1px solid ${C.border}`,
      borderRadius: 12, padding: 14,
    }}>
      <div style={{ fontFamily: C.mono, fontSize: 10,
           letterSpacing: "0.14em", color: C.faint, marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ color: C.text, fontSize: 22, fontWeight: 600 }}>{value}</div>
      {sub && (
        <div style={{ color: C.faint, fontSize: 10, marginTop: 3, fontFamily: C.mono }}>
          {sub}
        </div>
      )}
    </div>
  );
}

export default function AdminCockpit() {
  const { payload, err, loading, refresh } = useCockpitData();
  const checks = useMemo(() => payload?.checks || [], [payload]);
  const counts = payload?.counts || {};

  const handleAck = async (checkId, hours) => {
    try {
      if (hours > 0) {
        const until = new Date(Date.now() + hours * 3600 * 1000).toISOString();
        await api.post(`/admin/status/${checkId}/ack?until=${encodeURIComponent(until)}`);
      } else {
        // Clear ack.
        await api.post(`/admin/status/${checkId}/ack?until=`);
      }
      refresh();
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn("ack failed", e);
    }
  };

  return (
    <div data-testid="admin-cockpit-page"
         style={{ padding: 24, background: C.bg, minHeight: "100vh", color: C.text }}>
      <div style={{ maxWidth: 1200, margin: "0 auto" }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                      alignItems: "flex-start" }}>
          <div>
            <div style={{ fontFamily: C.mono, fontSize: 11,
                          letterSpacing: "0.18em", color: C.faint, marginBottom: 12 }}>
              COCKPIT · LIVE
            </div>
            <div style={{ fontSize: 24, marginBottom: 20 }}>System Overview</div>
          </div>
          <NotificationBell />
        </div>

        {loading && <div style={{ color: C.faint }}>loading live status…</div>}
        {err && <div style={{ color: C.red }} data-testid="cockpit-error">{err}</div>}

        {!loading && !err && (
          <>
            <HealthDonut counts={counts} />

            <CheckList title="NEEDS ATTENTION (real failures)"
                       filterFn={(c) => c.status === "red" && !c.ack_active}
                       checks={checks}
                       testid="cockpit-needs-attention"
                       onAck={handleAck} />
            <CheckList title="ACKED (muted, still tracked)"
                       filterFn={(c) => c.ack_active}
                       checks={checks}
                       testid="cockpit-acked"
                       onAck={handleAck} />
            <CheckList title="SETUP PENDING (gray — config missing)"
                       filterFn={(c) => c.status === "gray"}
                       checks={checks}
                       testid="cockpit-setup-pending"
                       onAck={handleAck} />
            <CheckList title="ALL GREEN"
                       filterFn={(c) => c.status === "green" && !c.ack_active}
                       checks={checks}
                       testid="cockpit-all-green"
                       onAck={handleAck} />

            <ModeBoard />
            <BusinessPulse />
          </>
        )}
      </div>
    </div>
  );
}
