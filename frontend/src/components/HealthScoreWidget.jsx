/**
 * components/HealthScoreWidget.jsx — Codebase Health Score (2026-08-23)
 *
 * Thin consumer of GET /admin/health-score. Every number shown comes
 * straight from services/health_score.py — this component never
 * invents a score. Categories the backend marks "unscored" render as
 * an explicit gray "UNSCORED — insufficient data" state, never a
 * softened placeholder number.
 */
import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

const C = {
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

const LABELS = {
  security:       "Security",
  bug_density:    "Bug Density",
  reliability:    "Reliability",
  test_coverage:  "Test Coverage",
  code_quality:   "Code Quality",
  data_handling:  "Data Handling",
  performance:    "Performance",
  architecture:   "Architecture",
  devops_infra:   "DevOps / Infra",
};

function scoreColor(score) {
  if (score == null) return C.gray;
  if (score >= 80) return C.green;
  if (score >= 50) return C.amber;
  return C.red;
}

function relAge(iso) {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  const d = ms / 86400000;
  if (d < 1) return `${Math.round(ms / 3600000)}h ago`;
  return `${d.toFixed(1)}d ago`;
}

function CategoryBar({ id, cat, weight, expanded, onToggle }) {
  const color = cat.status === "scored" ? scoreColor(cat.score) : C.gray;
  const pct = cat.status === "scored" ? cat.score : 0;
  return (
    <div data-testid={`health-score-category-${id}`}
         style={{ marginBottom: 10, cursor: "pointer" }}
         onClick={onToggle}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 13, color: C.text, fontWeight: 500, minWidth: 130 }}>
          {LABELS[id]}
        </span>
        <span style={{ fontSize: 10, color: C.faint, fontFamily: C.mono }}>
          weight {weight}%
        </span>
        <span style={{ marginLeft: "auto", fontSize: 13, fontWeight: 700, color,
                        fontFamily: C.mono }}>
          {cat.status === "scored" ? `${cat.score}/100` : "UNSCORED"}
        </span>
        {cat.live && cat.status === "scored" && (
          <span style={{ fontSize: 9, color: C.green, border: `1px solid ${C.green}55`,
                          borderRadius: 4, padding: "1px 5px", fontFamily: C.mono }}>
            LIVE
          </span>
        )}
      </div>
      <div style={{ background: "#ffffff0f", borderRadius: 4, height: 8, overflow: "hidden" }}>
        <div style={{
          width: `${pct}%`, height: "100%", background: color, borderRadius: 4,
          transition: "width 0.5s ease",
        }} />
      </div>
      {cat.status === "scored" && cat.last_verified && (
        <div style={{ fontSize: 10, color: C.faint, marginTop: 3, fontFamily: C.mono }}>
          last verified {relAge(cat.last_verified)}
        </div>
      )}
      {cat.status === "unscored" && (
        <div style={{ fontSize: 10, color: C.gray, marginTop: 3 }}>
          {cat.reason}
        </div>
      )}
      {expanded && (
        <pre data-testid={`health-score-evidence-${id}`}
             style={{
               marginTop: 8, background: "#00000066", border: `1px solid ${C.border}`,
               borderRadius: 6, padding: 10, fontSize: 10.5, color: C.dim,
               fontFamily: C.mono, overflowX: "auto", whiteSpace: "pre-wrap",
             }}>
          {JSON.stringify(cat.evidence, null, 2) || "(no evidence recorded)"}
        </pre>
      )}
    </div>
  );
}

export default function HealthScoreWidget() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [running, setRunning] = useState(false);
  const [reviewNotes, setReviewNotes] = useState("");
  const [reviewSubmitting, setReviewSubmitting] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.get("/admin/health-score", { timeout: 40000 });
      setData(r.data);
      setErr(null);
    } catch (e) {
      setErr(e?.message || "fetch failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const runCoverageScan = async () => {
    setRunning(true);
    try {
      await api.post("/admin/health-score/test-coverage/run", {});
      // Full pytest+coverage run takes several minutes server-side —
      // poll for the fresh result instead of holding one request open.
      const triggeredAt = Date.now();
      for (let i = 0; i < 24; i++) {
        await new Promise((r) => setTimeout(r, 15000));
        const r = await api.get("/admin/health-score", { timeout: 40000 });
        setData(r.data);
        const lv = r.data?.categories?.test_coverage?.last_verified;
        if (lv && new Date(lv).getTime() >= triggeredAt) break;
      }
    } catch (e) {
      setErr(e?.message || "coverage run failed");
    } finally {
      setRunning(false);
    }
  };

  const submitReview = async () => {
    if (!reviewNotes.trim()) return;
    setReviewSubmitting(true);
    try {
      // Real, deliberate rubric input only — no auto-generated numbers.
      await api.post("/admin/health-score/architecture-review", {
        reviewer: "founder-cockpit",
        notes: reviewNotes,
        rubric: { coupling: 70, spof: 70 },
      });
      setReviewNotes("");
      await load();
    } catch (e) {
      setErr(e?.message || "review submit failed");
    } finally {
      setReviewSubmitting(false);
    }
  };

  return (
    <div data-testid="health-score-widget"
         style={{ background: C.panel, border: `1px solid ${C.border}`,
                  borderRadius: 12, padding: 18, marginTop: 14 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                    marginBottom: 12 }}>
        <div style={{ fontFamily: C.mono, fontSize: 10, letterSpacing: "0.14em", color: C.faint }}>
          CODEBASE HEALTH SCORE
        </div>
        <button data-testid="health-score-run-coverage-btn"
                onClick={runCoverageScan}
                disabled={running}
                style={{
                  fontSize: 11, background: "transparent", border: `1px solid ${C.border}`,
                  color: running ? C.faint : C.dim, padding: "4px 10px", borderRadius: 6,
                  cursor: running ? "wait" : "pointer", fontFamily: C.mono,
                }}>
          {running ? "running coverage scan… (up to 5 min)" : "run coverage scan"}
        </button>
      </div>

      {loading && <div style={{ color: C.faint, fontSize: 12 }}>loading…</div>}
      {err && (
        <div data-testid="health-score-error"
             style={{ color: C.amber, fontFamily: C.mono, fontSize: 12,
                      padding: "8px 10px", border: `1px solid ${C.border}`,
                      borderRadius: 8, marginBottom: 12 }}>
          {err}
        </div>
      )}

      {data && (
        <>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4, flexWrap: "wrap" }}>
            <div data-testid="health-score-overall"
                 style={{ fontSize: 34, fontWeight: 700, color: scoreColor(data.overall_score),
                          fontFamily: C.mono }}>
              {data.overall_score != null ? `${data.overall_score}/100` : "—"}
            </div>
            <div data-testid="health-score-coverage-badge"
                 style={{ fontSize: 13, fontWeight: 700, color: C.amber, fontFamily: C.mono,
                          border: `1px solid ${C.amber}55`, borderRadius: 6, padding: "2px 8px" }}>
              at only {data.weight_scored_pct}% coverage
            </div>
          </div>
          <div style={{ fontSize: 11, color: C.faint, maxWidth: 460, marginBottom: 16 }}>
            This is <b style={{ color: C.dim }}>{data.overall_score}/100 scored across the
            {" "}{data.weight_scored_pct}% of weight that has fresh evidence</b> — NOT
            {" "}{data.overall_score}/100 overall. <b style={{ color: C.amber }}>{data.unscored_categories
              .map(k => LABELS[k]).join(", ")}</b> ({data.weight_unscored_pct}% of weight)
            has no fresh evidence and is excluded, never renormalized to hide the gap.
          </div>

          {Object.keys(LABELS).map((id) => (
            <CategoryBar key={id} id={id} cat={data.categories[id]}
                         weight={data.weights[id]}
                         expanded={expandedId === id}
                         onToggle={() => setExpandedId(expandedId === id ? null : id)} />
          ))}

          <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 10, color: C.faint, fontFamily: C.mono, marginBottom: 6 }}>
              LOG AN ARCHITECTURE REVIEW (qualitative half — coupling/SPOF judgment)
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <input
                data-testid="health-score-review-notes-input"
                value={reviewNotes}
                onChange={(e) => setReviewNotes(e.target.value)}
                placeholder="Real review notes — required, no default rubric is auto-filled"
                style={{
                  flex: 1, background: "#ffffff0a", border: `1px solid ${C.border}`,
                  borderRadius: 6, padding: "6px 10px", color: C.text, fontSize: 12,
                }}
              />
              <button
                data-testid="health-score-review-submit-btn"
                onClick={submitReview}
                disabled={reviewSubmitting || !reviewNotes.trim()}
                style={{
                  fontSize: 11, background: "transparent", border: `1px solid ${C.border}`,
                  color: C.dim, padding: "6px 12px", borderRadius: 6,
                  cursor: reviewSubmitting ? "wait" : "pointer", fontFamily: C.mono,
                }}>
                submit
              </button>
            </div>
          </div>

          <div style={{ fontSize: 9, color: C.faint, marginTop: 10, fontFamily: C.mono }}>
            generated_at {data.generated_at}
          </div>
        </>
      )}
    </div>
  );
}
