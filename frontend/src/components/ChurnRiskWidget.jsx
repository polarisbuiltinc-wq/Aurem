/**
 * components/ChurnRiskWidget.jsx — Churn × Complexity risk ranking (2026-08-26)
 *
 * Thin consumer of GET /admin/churn-risk. Every number shown comes
 * straight from services/churn_risk.py (real `git log` + the existing
 * architecture_health scan) — no invented scores. Phase 3a follow-up:
 * this is the "high churn + high complexity/bloat = real risk" signal
 * proposed during the Code Quality formula research.
 */
import { useCallback, useEffect } from "react";
import { api } from "../lib/api";
import { useAsyncState } from "../hooks/useAsyncState";

const C = {
  panel:  "#101013",
  border: "rgba(255,255,255,0.10)",
  text:   "#e5e5e5",
  faint:  "#5f5f5f",
  dim:    "#8a8a8a",
  amber:  "#f5a524",
  red:    "#ef4444",
  green:  "#22c55e",
  mono:   "SFMono-Regular, Menlo, Consolas, monospace",
};

function riskColor(score) {
  if (score >= 400) return C.red;
  if (score >= 150) return C.amber;
  return C.green;
}

export default function ChurnRiskWidget() {
  const { data, isProcessing, isFailed, run } = useAsyncState(20000);

  const load = useCallback(() => {
    return run(() => api.get("/admin/churn-risk").then((r) => r.data));
  }, [run]);

  useEffect(() => { load(); }, [load]);

  return (
    <div data-testid="churn-risk-widget" style={{
      background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10,
      padding: 16, fontFamily: C.mono,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <span style={{ color: C.text, fontSize: 13, fontWeight: 600 }}>
          Churn Risk — files changing most while already flagged bloated/complex
        </span>
        <button data-testid="churn-risk-refresh-button" onClick={load}
          style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.dim,
                   borderRadius: 6, padding: "2px 8px", fontSize: 11, cursor: "pointer" }}>
          refresh
        </button>
      </div>

      {isProcessing && !data && (
        <div data-testid="churn-risk-loading" style={{ color: C.dim, fontSize: 12 }}>Scanning git log…</div>
      )}
      {isFailed && (
        <div data-testid="churn-risk-error" style={{ color: C.red, fontSize: 12 }}>Could not load churn risk.</div>
      )}
      {data && data.ok === false && (
        <div data-testid="churn-risk-unavailable" style={{ color: C.dim, fontSize: 12 }}>
          {data.reason || "unavailable"}
        </div>
      )}
      {data && data.ok && (
        <>
          <div style={{ color: C.faint, fontSize: 11, marginBottom: 8 }}>
            {data.flagged_files} of {data.total_files_considered} changed files (last {data.window_days}d) are
            already bloated/complex — top {data.rows.length} by risk:
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {data.rows.map((r) => (
              <div key={r.file} data-testid={`churn-risk-row-${r.file}`}
                   style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                            fontSize: 11, borderLeft: `3px solid ${riskColor(r.risk_score)}`, paddingLeft: 8 }}>
                <span style={{ color: C.text, overflow: "hidden", textOverflow: "ellipsis",
                               whiteSpace: "nowrap", maxWidth: "58%" }} title={r.file}>
                  {r.file}
                </span>
                <span style={{ color: C.dim }}>
                  {r[`commits_last_${data.window_days}d`]} commits
                  {r.bloated ? ` · ${r.lines}L` : ""}
                  {r.has_complex_function ? " · CC>10" : ""}
                </span>
                <span style={{ color: riskColor(r.risk_score), fontWeight: 700, minWidth: 34, textAlign: "right" }}>
                  {r.risk_score}
                </span>
              </div>
            ))}
            {data.rows.length === 0 && (
              <div style={{ color: C.dim, fontSize: 12 }}>No high-churn flagged files in this window.</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
