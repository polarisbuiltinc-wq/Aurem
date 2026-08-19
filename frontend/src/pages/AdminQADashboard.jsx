/**
 * AdminQADashboard.jsx — Iter 303
 *
 * /admin/qa — founder-facing QA health dashboard. Locks:
 *
 *   - Test counts (backend Pytest AST-authoritative, Vitest, Playwright)
 *   - Test-style ratio (STATIC_GREP % + weak-P0 count vs CI threshold)
 *   - a11y baseline health (component + journey surfaces)
 *   - CI job status per named workflow (or honest "unavailable" if
 *     GITHUB_ACTIONS_TOKEN + GITHUB_REPO env not set)
 *
 * Every number is fetched live from `/api/aurem-dev/admin/qa/status`
 * on mount and on manual refresh — no cached/stale numbers in the UI.
 *
 * Admin-gated (backend rejects with 403). Reads the founder JWT from
 * localStorage via the canonical `getToken()` helper in `lib/api.js`
 * (which reads the `aurem_token` key set by Login.jsx). Feb 2026 fix:
 * previously this file looked up a legacy `aurem_admin_token` key
 * first — which is never actually set anywhere — and only fell back
 * to `aurem_token`. The fallback was correct in theory but produced
 * "Invalid authorization format" errors in preview because the header
 * became `"Bearer "` for a subset of session states. Standardising on
 * the same helper every other admin page uses eliminates the drift.
 */
import React, { useEffect, useState, useCallback } from "react";
import axios from "axios";
import { getToken } from "../lib/api";

const API = process.env.REACT_APP_BACKEND_URL;


function Card({ title, sub, testid, children }) {
  return (
    <div data-testid={testid}
          style={{
            background: "#0f0f0f",
            border: "1px solid #262626",
            borderRadius: 12,
            padding: 22,
            fontFamily: "system-ui, -apple-system, sans-serif",
          }}>
      <div style={{
        fontSize: 11, letterSpacing: 1.2, color: "#666",
        textTransform: "uppercase", marginBottom: 8,
      }}>{title}</div>
      {sub && <div style={{ fontSize: 12, color: "#888",
                             marginBottom: 12 }}>{sub}</div>}
      {children}
    </div>
  );
}


function BigNumber({ value, label }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
      <div style={{ fontSize: 32, fontWeight: 700, color: "#e5e5e5",
                     fontVariantNumeric: "tabular-nums" }}>{value}</div>
      <div style={{ fontSize: 12, color: "#666" }}>{label}</div>
    </div>
  );
}


function CIJobChip({ name, job }) {
  const conc = job?.conclusion;
  const status = job?.status;
  let bg = "#333", fg = "#aaa", label = "unknown";
  if (conc === "success")        { bg = "#0a2416"; fg = "#4ade80"; label = "pass"; }
  else if (conc === "failure")   { bg = "#2b0a0a"; fg = "#f87171"; label = "fail"; }
  else if (conc === "cancelled") { bg = "#2b230a"; fg = "#facc15"; label = "cancelled"; }
  else if (status === "in_progress") { bg = "#0a1e2b"; fg = "#60a5fa"; label = "running"; }
  else if (status === "queued")  { bg = "#1a1a1a"; fg = "#94a3b8"; label = "queued"; }
  return (
    <a href={job?.html_url || "#"} target="_blank" rel="noreferrer"
        data-testid={`ci-job-${name}`}
        data-ci-conclusion={conc || status || "unknown"}
        style={{
          display: "flex", justifyContent: "space-between",
          alignItems: "center", padding: "10px 12px",
          background: bg, borderRadius: 8, textDecoration: "none",
          fontSize: 12, color: fg, marginBottom: 6,
          border: "1px solid rgba(255,255,255,0.05)",
        }}>
      <span style={{ fontFamily: "monospace" }}>{name}</span>
      <span style={{ textTransform: "uppercase",
                      fontSize: 10, letterSpacing: 1 }}>{label}</span>
    </a>
  );
}


export default function AdminQADashboard() {
  const [data,    setData]    = useState(null);
  const [error,   setError]   = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const tok = getToken();
      const r = await axios.get(
        `${API}/api/aurem-dev/admin/qa/status`,
        { headers: { Authorization: `Bearer ${tok || ""}` }, timeout: 20000 },
      );
      setData(r.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || "unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) {
    return (
      <div style={{ padding: 40, color: "#666",
                     fontFamily: "system-ui" }}
            data-testid="admin-qa-loading">
        Loading QA status…
      </div>
    );
  }

  if (error && !data) {
    return (
      <div style={{ padding: 40, color: "#f87171",
                     fontFamily: "system-ui" }}
            data-testid="admin-qa-error">
        <div style={{ fontSize: 14, marginBottom: 8 }}>
          Failed to load QA status
        </div>
        <div style={{ fontSize: 12, color: "#888" }}>{String(error)}</div>
        <button onClick={load}
                 style={{ marginTop: 12, padding: "6px 12px",
                           background: "#1a1a1a", color: "#e5e5e5",
                           border: "1px solid #333", borderRadius: 6,
                           cursor: "pointer" }}>Retry</button>
      </div>
    );
  }

  const counts = data.test_counts;
  const style  = data.test_style;
  const a11y   = data.a11y;
  const ci     = data.ci_status;
  return (
    <div data-testid="admin-qa-dashboard"
          style={{ minHeight: "100vh", padding: "32px 40px",
                    background: "#0a0a0a", color: "#e5e5e5",
                    fontFamily: "system-ui" }}>
      {/* Header row */}
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "center", marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
            QA Health
          </h1>
          <div style={{ fontSize: 11, color: "#666", marginTop: 4 }}>
            Live from `/api/admin/qa/status` — generated {" "}
            {new Date(data.generated_at * 1000).toLocaleTimeString()}
            {" · "}{data.took_ms}ms
          </div>
        </div>
        <button onClick={load} disabled={loading}
                 data-testid="admin-qa-refresh"
                 style={{ padding: "8px 16px", background: "#1a1a1a",
                           color: "#e5e5e5", border: "1px solid #333",
                           borderRadius: 6, cursor: "pointer",
                           opacity: loading ? 0.5 : 1 }}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {/* 4-card grid */}
      <div style={{ display: "grid",
                     gridTemplateColumns: "1fr 1fr",
                     gap: 20, maxWidth: 1100 }}>

        {/* CARD 1 — Test counts */}
        <Card testid="admin-qa-card-counts"
              title="Test counts"
              sub={`grand total: ${counts.grand_total_tests}`}>
          <div style={{ display: "grid",
                          gridTemplateColumns: "1fr 1fr",
                          gap: 14, marginTop: 8 }}>
            <BigNumber value={counts.backend_pytest.tests}
                        label={`backend (${counts.backend_pytest.files} files)`} />
            <BigNumber value={counts.frontend_vitest.tests}
                        label={`vitest (${counts.frontend_vitest.files} files)`} />
            <BigNumber value={counts.playwright.tests}
                        label={`playwright (${counts.playwright.files} files)`} />
            <BigNumber value={counts.reasoning_evals.tests}
                        label={`reasoning evals`} />
          </div>
        </Card>

        {/* CARD 2 — Test-style ratio */}
        <Card testid="admin-qa-card-style"
              title="Test-style ratio (iter289 guard)"
              sub={style.available
                    ? `${style.total_tests} tests analysed · CI threshold ${style.threshold_pct}%`
                    : "analyser unavailable"}>
          {style.available ? (
            <>
              <BigNumber value={`${style.ratio_pct}%`}
                          label="STATIC_GREP" />
              <div style={{ marginTop: 10, display: "flex", gap: 16,
                              fontSize: 12, color: "#888" }}>
                <span>weak_p0: <strong style={{
                  color: style.weak_p0_count === 0 ? "#4ade80" :
                          (style.weak_p0_count > 15 ? "#f87171" : "#facc15"),
                }}
                   data-testid="admin-qa-weak-p0">
                  {style.weak_p0_count}</strong></span>
                <span>threshold: <strong style={{
                  color: style.passes_threshold ? "#4ade80" : "#f87171",
                }}>
                  {style.passes_threshold ? "PASS" : "FAIL"}</strong></span>
              </div>
            </>
          ) : (
            <div style={{ fontSize: 12, color: "#888" }}>
              {style.reason}
            </div>
          )}
        </Card>

        {/* CARD 3 — a11y baselines */}
        <Card testid="admin-qa-card-a11y"
              title="a11y baseline"
              sub="charter L3 burn-down">
          <div style={{ display: "grid",
                          gridTemplateColumns: "1fr 1fr",
                          gap: 14 }}>
            {Object.entries(a11y).map(([kind, v]) => (
              <div key={kind}>
                <div style={{ fontSize: 11, color: "#666",
                                textTransform: "uppercase",
                                marginBottom: 4 }}>{kind}</div>
                {v.available ? (
                  <>
                    <BigNumber value={v.total_known_violations}
                                label={`known violations`} />
                    <div style={{ fontSize: 11, color: "#888",
                                    marginTop: 4 }}>
                      {v.surfaces_clean}/{v.surfaces_tracked} clean
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: 12, color: "#888" }}>
                    unavailable
                  </div>
                )}
              </div>
            ))}
          </div>
          {a11y.journeys?.available && (
            <details style={{ marginTop: 14, fontSize: 12,
                                color: "#888" }}>
              <summary style={{ cursor: "pointer" }}>
                per-surface violations
              </summary>
              <pre style={{ background: "#0a0a0a",
                             padding: 10, marginTop: 8,
                             borderRadius: 4, overflow: "auto" }}>
{JSON.stringify(a11y.journeys.per_surface, null, 2)}
              </pre>
            </details>
          )}
        </Card>

        {/* CARD 4 — CI status */}
        <Card testid="admin-qa-card-ci"
              title="CI status (GitHub Actions)"
              sub={ci.available
                    ? `run ${ci.workflow_run_id} · ${(ci.commit_sha || "").slice(0,7)}`
                    : "not wired"}>
          {ci.available ? (
            <>
              {ci.commit_message && (
                <div style={{ fontSize: 12, color: "#888",
                                marginBottom: 12,
                                whiteSpace: "nowrap",
                                overflow: "hidden",
                                textOverflow: "ellipsis" }}>
                  “{(ci.commit_message || "").split("\n")[0]}”
                </div>
              )}
              {Object.entries(ci.jobs).map(([name, job]) => (
                <CIJobChip key={name} name={name} job={job} />
              ))}
              <a href={ci.workflow_url} target="_blank" rel="noreferrer"
                  style={{ fontSize: 11, color: "#60a5fa",
                            textDecoration: "none", marginTop: 8,
                            display: "inline-block" }}>
                open workflow run →
              </a>
            </>
          ) : (
            <div style={{ fontSize: 12, color: "#888",
                            lineHeight: 1.6 }}
                  data-testid="admin-qa-ci-unavailable">
              <div style={{ color: "#facc15", marginBottom: 6 }}>
                CI status not wired
              </div>
              {ci.reason}
            </div>
          )}
        </Card>
      </div>

      {/* Iter 334 — Auto-QA agent latest report */}
      <LatestAutoQASection />

      {/* Iter 364/365 — Loop-beta rollout kill-switch */}
      <LoopKillSwitchSection />

      {/* Iter 363 · Guard 20 — automated postmortem / incident log */}
      <IncidentLogSection />

      {/* 2026-08 — Fabrication learning loop: recurring patterns */}
      <FabricationPatternsSection />

      {/* 2026-08-19 — Regression pattern registry (RECURRING_ISSUES.md fold-in) */}
      <RegressionPatternsSection />
    </div>
  );
}


// Iter 365 · Phase 5 — one-click kill-switch for Loop Mode. Toggles
// the DB `system_flags.loop_mode_kill_switch` row via the admin API
// so the founder can flip Loop OFF for everyone (including beta) in
// one click, without curl. Confirms twice on enable, once on disable.
function LoopKillSwitchSection() {
  const [status, setStatus] = useState(null);
  const [busy,   setBusy]   = useState(false);

  const load = () => {
    const tok = getToken();
    axios.get(`${API}/api/aurem-dev/admin/loop-beta/status`,
      { headers: { Authorization: `Bearer ${tok || ""}` }, timeout: 15000 })
      .then((r) => setStatus(r.data))
      .catch((e) => setStatus({ error: e?.response?.data?.detail || e.message }));
  };
  useEffect(load, []);

  const flip = async (nextOn) => {
    const confirmMsg = nextOn
      ? "This will BLOCK every /loop/start call for every user, including beta users. Continue?"
      : "Re-enable Loop Mode for all beta-flagged users?";
    if (!window.confirm(confirmMsg)) return;
    setBusy(true);
    try {
      const tok = getToken();
      await axios.post(`${API}/api/aurem-dev/admin/loop-beta/kill-switch`,
        { enabled: nextOn, reason: nextOn ? "manual UI flip" : "manual UI un-flip" },
        { headers: { Authorization: `Bearer ${tok || ""}` }, timeout: 15000 });
      load();
    } catch (e) {
      alert("Kill-switch flip failed: " + (e?.response?.data?.detail || e.message));
    } finally {
      setBusy(false);
    }
  };

  if (!status) return null;
  if (status.error) {
    return (
      <div style={{ maxWidth: 1100, marginTop: 20 }}>
        <Card testid="admin-loop-killswitch"
              title="Loop Mode Kill-Switch"
              sub="Iter 364/365 — one-click Loop rollback">
          <div style={{ fontSize: 12, color: "#f87171" }}>
            {typeof status.error === "string" ? status.error : "Failed to load"}
          </div>
        </Card>
      </div>
    );
  }
  const on = !!status.kill_switch_db;
  return (
    <div style={{ maxWidth: 1100, marginTop: 20 }}
         data-testid="admin-loop-killswitch">
      <Card testid="admin-loop-killswitch-card"
            title="Loop Mode Kill-Switch"
            sub={`${status.beta_users} beta users · ${status.active_loops} active loops · ${status.stuck_last_10min} stuck (10m)`}>
        <div style={{ display: "flex", alignItems: "center",
                       justifyContent: "space-between", gap: 16 }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600,
                           color: on ? "#f87171" : "#4ade80" }}>
              {on ? "🔴  KILL-SWITCH ON — all /loop/start calls blocked"
                  : "🟢  Loop Mode enabled per tier gate"}
            </div>
            <div style={{ fontSize: 11, color: "#888", marginTop: 4 }}>
              {status.kill_switch_env
                ? `Env override active: LOOP_MODE_KILL_SWITCH=${status.kill_switch_env}`
                : "DB flag drives this — env override empty"}
            </div>
            {status.kill_switch_reason && (
              <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 4 }}>
                Reason: {status.kill_switch_reason}
              </div>
            )}
          </div>
          <button
            data-testid="loop-killswitch-toggle"
            disabled={busy}
            onClick={() => flip(!on)}
            style={{
              padding: "10px 20px",
              background: on ? "#4ade80" : "#f87171",
              color: "#0a0a0a",
              border: "none",
              borderRadius: 6,
              fontWeight: 700,
              fontSize: 13,
              cursor: busy ? "wait" : "pointer",
              opacity: busy ? 0.6 : 1,
            }}
          >
            {busy ? "…" : (on ? "Re-enable Loop" : "🚨 Kill Loop Mode")}
          </button>
        </div>
      </Card>
    </div>
  );
}


// Iter 334 — renders .emergent/latest-qa-report.md served by
// GET /admin/qa/latest-report. Raw markdown in a <pre> (no
// react-markdown dependency in this repo — deliberate, documented).
function LatestAutoQASection() {
  const [report, setReport] = useState(null);
  useEffect(() => {
    const tok = getToken();
    axios.get(`${API}/api/aurem-dev/admin/qa/latest-report`,
      { headers: { Authorization: `Bearer ${tok || ""}` }, timeout: 20000 })
      .then((r) => setReport(r.data))
      .catch((e) => setReport({
        error: e?.response?.data?.detail || e.message,
      }));
  }, []);
  if (!report) return null;
  return (
    <div className="latest-auto-qa" data-testid="admin-qa-latest-report"
          style={{ maxWidth: 1100, marginTop: 20 }}>
      <Card testid="admin-qa-card-auto-report"
            title="Latest Auto-QA Report"
            sub={report.modified_at
              ? `written ${new Date(report.modified_at * 1000).toLocaleString()}`
              : "auto-qa-agent job output"}>
        {report.content ? (
          <pre data-testid="admin-qa-report-content"
                style={{ whiteSpace: "pre-wrap", fontSize: 11.5,
                          lineHeight: 1.6, color: "#d4d4d4",
                          fontFamily: "'JetBrains Mono', monospace",
                          maxHeight: 420, overflowY: "auto",
                          margin: 0 }}>
            {report.content}
          </pre>
        ) : (
          <div data-testid="admin-qa-report-empty"
                style={{ fontSize: 12, color: "#888" }}>
            {report.error || "No report yet — auto-qa-agent job has not run"}
          </div>
        )}
      </Card>
    </div>
  );
}


// Iter 363 · Guard 20 — Incident log: every RED/critical alert from any
// guard auto-creates a postmortem entry. Chronological, filter open vs
// resolved, shows MTTR (30d).
function IncidentLogSection() {
  const [data,   setData]   = useState(null);
  const [status, setStatus] = useState("all");
  useEffect(() => {
    const tok = getToken();
    axios.get(`${API}/api/aurem-dev/admin/qa/guard20-incidents?status=${status}`,
      { headers: { Authorization: `Bearer ${tok || ""}` }, timeout: 20000 })
      .then((r) => setData(r.data))
      .catch((e) => setData({ error: e?.response?.data?.detail || e.message }));
  }, [status]);

  const fmtMttr = (s) => {
    if (s == null) return "—";
    if (s < 60) return `${Math.round(s)}s`;
    if (s < 3600) return `${Math.round(s / 60)}m`;
    return `${(s / 3600).toFixed(1)}h`;
  };

  return (
    <div style={{ maxWidth: 1100, marginTop: 20 }}>
      <Card testid="admin-qa-incident-log"
            title="Incident Log (Guard 20)"
            sub={data?.stats
              ? `${data.stats.open} open · ${data.stats.resolved_30d} resolved (30d) · MTTR ${fmtMttr(data.stats.mttr_30d_s)}`
              : "auto-created from any guard's RED/critical alert"}>
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          {["all", "open", "resolved"].map((s) => (
            <button key={s} data-testid={`incident-filter-${s}`}
              onClick={() => setStatus(s)}
              style={{ padding: "4px 12px", fontSize: 11, borderRadius: 5,
                       cursor: "pointer", textTransform: "capitalize",
                       fontFamily: "'JetBrains Mono', monospace",
                       border: "1px solid",
                       borderColor: status === s ? "#f59e0b" : "rgba(148,163,184,0.25)",
                       background: status === s ? "rgba(245,158,11,0.12)" : "transparent",
                       color: status === s ? "#fbbf24" : "#94a3b8" }}>
              {s}
            </button>
          ))}
        </div>
        {data?.error && (
          <div data-testid="incident-log-error" style={{ fontSize: 12, color: "#f87171" }}>
            {data.error}
          </div>
        )}
        {data && !data.error && (data.incidents || []).length === 0 && (
          <div data-testid="incident-log-empty" style={{ fontSize: 12, color: "#888" }}>
            No incidents{status !== "all" ? ` (${status})` : ""} — nothing has gone RED.
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {(data?.incidents || []).map((inc) => (
            <div key={inc.incident_id} data-testid={`incident-row-${inc.incident_id}`}
              style={{ padding: "10px 12px", borderRadius: 6,
                       border: "1px solid rgba(148,163,184,0.15)",
                       background: "rgba(148,163,184,0.04)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 6px",
                               borderRadius: 4, fontFamily: "'JetBrains Mono', monospace",
                               color: inc.status === "open" ? "#f87171" : "#4ade80",
                               background: inc.status === "open"
                                 ? "rgba(239,68,68,0.12)" : "rgba(34,197,94,0.12)" }}>
                  {inc.status.toUpperCase()}
                </span>
                <span style={{ fontSize: 10, color: "#64748b",
                               fontFamily: "'JetBrains Mono', monospace" }}>
                  {inc.guard}
                </span>
                <span style={{ fontSize: 13, color: "#e2e8f0", fontWeight: 500 }}>
                  {inc.title}
                </span>
                <span style={{ marginLeft: "auto", fontSize: 10, color: "#64748b" }}>
                  {new Date(inc.detected_at * 1000).toLocaleString()}
                </span>
              </div>
              <div style={{ fontSize: 11.5, color: "#94a3b8", lineHeight: 1.5 }}>
                {inc.detail}
              </div>
              {inc.status === "resolved" && (
                <div style={{ fontSize: 11, color: "#64748b", marginTop: 5 }}>
                  ✔ {inc.resolution} · MTTR {fmtMttr(inc.mttr_s)}
                  {inc.root_cause ? ` · cause: ${inc.root_cause}` : ""}
                </div>
              )}
              {inc.follow_up && inc.status === "open" && (
                <div style={{ fontSize: 11, color: "#fbbf24", marginTop: 5 }}>
                  → {inc.follow_up}
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}


// 2026-08 — Fabrication learning loop. Shows recurring per-project
// per-route CitationGuard / ORA-grounding fabrication patterns from
// `/admin/qa/fabrication-patterns`. `caution_active` (count >= 3 in
// 30d) mirrors exactly the runtime threshold used to inject the
// caution — this view never claims a caution is live when it isn't.
function FabricationPatternsSection() {
  const [data, setData] = useState(null);

  const load = () => {
    const tok = getToken();
    axios.get(`${API}/api/aurem-dev/admin/qa/fabrication-patterns`,
      { headers: { Authorization: `Bearer ${tok || ""}` }, timeout: 20000 })
      .then((r) => setData(r.data))
      .catch((e) => setData({ error: e?.response?.data?.detail || e.message }));
  };
  useEffect(load, []);

  const fmtAgo = (ts) => {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleString();
  };

  return (
    <div style={{ maxWidth: 1100, marginTop: 20 }}>
      <Card testid="admin-qa-fabrication-patterns"
            title="Fabrication Learning Loop"
            sub={data?.patterns
              ? `${data.recurring_count} recurring (≥3 in ${data.since_days}d) · ${data.patterns.length} total signatures`
              : "CitationGuard + ORA-grounding fabrication patterns, last 30d"}>
        {data?.error && (
          <div data-testid="fabrication-patterns-error" style={{ fontSize: 12, color: "#f87171" }}>
            {data.error}
          </div>
        )}
        {data && !data.error && data.patterns.length === 0 && (
          <div data-testid="fabrication-patterns-empty" style={{ fontSize: 12, color: "#888" }}>
            No fabrication incidents logged in the last {data.since_days} days.
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {(data?.patterns || []).map((p, i) => (
            <div key={i} data-testid={`fabrication-pattern-row-${i}`}
              style={{ padding: "10px 12px", borderRadius: 6,
                       border: "1px solid rgba(148,163,184,0.15)",
                       background: "rgba(148,163,184,0.04)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 6px",
                               borderRadius: 4, fontFamily: "'JetBrains Mono', monospace",
                               color: p.caution_active ? "#f87171" : "#94a3b8",
                               background: p.caution_active
                                 ? "rgba(239,68,68,0.12)" : "rgba(148,163,184,0.08)" }}>
                  {p.caution_active ? "CAUTION LIVE" : `${p.count}× seen`}
                </span>
                <span style={{ fontSize: 10, color: "#64748b",
                               fontFamily: "'JetBrains Mono', monospace" }}>
                  {p.source} · project={p.project_id} · route={p.route}
                </span>
                <span style={{ marginLeft: "auto", fontSize: 10, color: "#64748b" }}>
                  last {fmtAgo(p.last_at)}
                </span>
              </div>
              <div style={{ fontSize: 11.5, color: "#94a3b8", lineHeight: 1.5 }}>
                {(p.sample_paths || []).join(", ") || "(no paths captured)"}
              </div>
              <div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>
                corrected {p.corrected}/{p.count}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}


// 2026-08-19 — Regression pattern registry. Replaces the markdown-only
// RECURRING_ISSUES.md approach — shows each known dev-bug pattern,
// whether it has a REAL test (not grep-lock), and that test's last
// LIVE result (written by scripts/verify_regression_patterns.py — this
// card only reads, never runs pytest inline).
function RegressionPatternsSection() {
  const [data, setData] = useState(null);

  const load = () => {
    const tok = getToken();
    axios.get(`${API}/api/aurem-dev/admin/qa/regression-patterns`,
      { headers: { Authorization: `Bearer ${tok || ""}` }, timeout: 20000 })
      .then((r) => setData(r.data))
      .catch((e) => setData({ error: e?.response?.data?.detail || e.message }));
  };
  useEffect(load, []);

  const fmtAgo = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "never run");

  const statusOf = (p) => {
    if (!p.test_ref) return { label: "NO AUTOMATED TEST", color: "#94a3b8", bg: "rgba(148,163,184,0.08)" };
    if (p.last_verified_at == null) return { label: "NOT YET VERIFIED", color: "#eab308", bg: "rgba(234,179,8,0.10)" };
    return p.last_verified_passed
      ? { label: "VERIFIED PASSING", color: "#4ade80", bg: "rgba(74,222,128,0.10)" }
      : { label: "REGRESSED — FAILING", color: "#f87171", bg: "rgba(239,68,68,0.12)" };
  };

  return (
    <div style={{ maxWidth: 1100, marginTop: 20 }}>
      <Card testid="admin-qa-regression-patterns"
            title="Recurring Bug Pattern Registry"
            sub={data?.patterns
              ? `${data.with_real_test}/${data.total} have a real behavioral test · source of truth: ${data.doc_ref}`
              : "Known dev-bug patterns — real test status, not grep-lock"}>
        {data?.error && (
          <div data-testid="regression-patterns-error" style={{ fontSize: 12, color: "#f87171" }}>
            {data.error}
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {(data?.patterns || []).map((p, i) => {
            const s = statusOf(p);
            return (
              <div key={i} data-testid={`regression-pattern-row-${i}`}
                style={{ padding: "10px 12px", borderRadius: 6,
                         border: "1px solid rgba(148,163,184,0.15)",
                         background: "rgba(148,163,184,0.04)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 6px",
                                 borderRadius: 4, fontFamily: "'JetBrains Mono', monospace",
                                 color: s.color, background: s.bg }}>
                    {s.label}
                  </span>
                  <span style={{ fontSize: 12, color: "#cbd5e1" }}>{p.title}</span>
                  <span style={{ marginLeft: "auto", fontSize: 10, color: "#64748b" }}>
                    {fmtAgo(p.last_verified_at)}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: "#64748b" }}>
                  {p.pattern_id} · status={p.status} · {p.test_ref || "no test_ref"}
                </div>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}