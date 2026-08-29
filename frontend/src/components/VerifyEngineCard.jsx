/**
 * VerifyEngineCard.jsx — V1-dashboard (2026-08-30): the compact,
 * user-facing front for the V1 server-side deploy-verify engine.
 * One pass-rate number, one last-fail line, one current state.
 * The full check list / raw events live on the admin tile
 * (AdminSystemHealth) — this is deliberately not that.
 */
import React, { useEffect, useState, useCallback } from "react";
import { Loader2, ShieldCheck, ShieldAlert } from "lucide-react";
import { api } from "../lib/api";

function relativeTime(iso) {
  if (!iso) return null;
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export default function VerifyEngineCard({ projectId, verifying, refreshSignal, onViewEvidence, initialSummary }) {
  const [summary, setSummary] = useState(initialSummary || null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    // Visual-fixture / test escape hatch — skip the network call
    // entirely when a summary is injected directly (mirrors the
    // `event` prop pattern LoopLiveFeed/LoopStepBar already use for
    // hermetic /dev/visual screenshots).
    if (initialSummary) return;
    try {
      const r = await api.get("/deploy/verify-summary", {
        params: projectId ? { project_id: projectId } : {},
      });
      setSummary(r.data || {});
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [projectId, initialSummary]);

  useEffect(() => { load(); }, [load, refreshSignal]);

  if (failed || !summary) return null;

  const rowStyle = {
    padding: "8px 14px", borderBottom: "1px solid var(--border)",
    display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
    fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
    background: "var(--bg-elev)",
  };

  if (!summary.has_any) {
    return (
      <div data-testid="deploy-verify-card" style={rowStyle}>
        <ShieldCheck size={12} color="var(--text-faint)" />
        <span data-testid="deploy-verify-empty-state" style={{ color: "var(--text-faint)" }}>
          Your first deployment will be verified automatically.
        </span>
      </div>
    );
  }

  return (
    <div data-testid="deploy-verify-card" style={rowStyle}>
      <span data-testid="deploy-verify-pass-rate" style={{ color: "var(--text)" }}>
        Last 30d: {summary.passed}/{summary.total} verifications passed
      </span>
      <span data-testid="deploy-verify-current-state" style={{ color: "var(--text-faint)", display: "inline-flex", alignItems: "center", gap: 5 }}>
        {verifying ? (
          <><Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} /> Verifying deployed site…</>
        ) : summary.last_run_at ? (
          `Last verified: ${relativeTime(summary.last_run_at)}`
        ) : "No deployment yet"}
      </span>
      {summary.last_fail_what_happened && (
        <span
          data-testid="deploy-verify-last-fail"
          style={{ color: "var(--danger)", display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          <ShieldAlert size={11} />
          {summary.last_fail_what_happened}
          {summary.last_fail_run_id && onViewEvidence && (
            <button
              data-testid="deploy-verify-view-evidence-link"
              onClick={() => onViewEvidence(summary.last_fail_run_id)}
              style={{
                background: "none", border: "none", padding: 0,
                color: "var(--accent-2)", textDecoration: "underline",
                cursor: "pointer", fontSize: 11, fontFamily: "inherit",
              }}
            >
              View evidence
            </button>
          )}
        </span>
      )}
    </div>
  );
}
