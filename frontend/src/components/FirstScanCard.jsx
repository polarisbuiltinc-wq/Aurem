/**
 * components/FirstScanCard.jsx — Onboarding Step 4 · S-B (2026-08-26).
 *
 * The first-scan aha: "Connected — scanning your site..." -> plain-
 * language findings card -> "Fix all N for me" -> real commit.
 *
 * Reuses services/onboarding_first_scan.py's GET /status,
 * POST /viewed, POST /apply (NOT founder_offer.py's claim/confirm —
 * that flow is the promotional offer, decoupled per design).
 *
 * Mounted above the chat input in ChatPanel.jsx, mirroring
 * FounderOfferCard's placement/styling conventions.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

const POLL_MS = 2000;
const MAX_POLL_MS = 60_000;

export default function FirstScanCard({ projectId }) {
  const [state, setState] = useState(null);   // full /status response
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState(null); // {commit_sha, commit_url, files_fixed}
  const [dismissed, setDismissed] = useState(false);
  const viewedSentRef = useRef(false);
  const pollStartRef = useRef(null);

  const poll = useCallback(async () => {
    if (!projectId || projectId === "home") return;
    try {
      const r = await api.get("/onboarding/first-scan/status", {
        params: { project_id: projectId },
      });
      setState(r.data);
    } catch (_e) {
      // best-effort — never raise to the UI
    }
  }, [projectId]);

  useEffect(() => {
    if (!projectId || projectId === "home") return;
    pollStartRef.current = Date.now();
    let stopped = false;
    poll();
    const t = setInterval(() => {
      if (stopped) return;
      const st = state?.status;
      if (st === "ready" || st === "clean" || st === "skipped" || st === "error" ||
          Date.now() - pollStartRef.current > MAX_POLL_MS) {
        clearInterval(t);
        return;
      }
      poll();
    }, POLL_MS);
    return () => { stopped = true; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    if (state?.status === "ready" && !viewedSentRef.current) {
      viewedSentRef.current = true;
      api.post("/onboarding/first-scan/viewed", { project_id: projectId }).catch(() => {});
    }
  }, [state, projectId]);

  const handleFix = useCallback(async () => {
    setApplying(true);
    try {
      const r = await api.post("/onboarding/first-scan/apply", { project_id: projectId });
      setApplyResult(r.data);
    } catch (e) {
      setApplyResult({ ok: false, error: e?.response?.data?.detail || "Couldn't apply the fix." });
    } finally {
      setApplying(false);
    }
  }, [projectId]);

  if (!projectId || projectId === "home" || dismissed) return null;
  if (!state || state.status === "skipped") return null;

  const cardStyle = {
    margin: "0 clamp(16px, 17.25%, 240px)",
    padding: "10px 16px",
    display: "flex",
    flexDirection: "column",
    gap: 8,
    background: "linear-gradient(180deg, rgba(56,189,248,0.10) 0%, rgba(56,189,248,0.04) 100%)",
    borderTopLeftRadius: 12,
    borderTopRightRadius: 12,
    borderTop: "1px solid rgba(56,189,248,0.45)",
    borderLeft: "1px solid rgba(56,189,248,0.45)",
    borderRight: "1px solid rgba(56,189,248,0.45)",
  };

  if (state.status === "scanning" || state.status === "still_scanning") {
    return (
      <div data-testid="first-scan-card" style={cardStyle}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: "#7dd3fc" }}>
          <span className="animate-spin" style={{ display: "inline-block" }} aria-hidden="true">⟳</span>
          <span data-testid="first-scan-scanning-label">
            {state.status === "still_scanning"
              ? (state.message || "Still scanning — you can start chatting in the meantime.")
              : "Connected — scanning your site…"}
          </span>
        </div>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div data-testid="first-scan-card" style={cardStyle}>
        <div data-testid="first-scan-error" style={{ fontSize: 12, color: "#fca5a5" }}>
          {state.message || "I couldn't scan your repo right now, but you can still ask me to build or fix anything."}
        </div>
      </div>
    );
  }

  if (state.status === "clean") {
    return (
      <div data-testid="first-scan-card" style={cardStyle}>
        <div data-testid="first-scan-clean" style={{ fontSize: 13, color: "#7dd3fc" }}>
          Your site looks in good shape. What would you like to build or improve?
        </div>
      </div>
    );
  }

  if (applyResult?.commit_sha) {
    return (
      <div data-testid="first-scan-card" style={cardStyle}>
        <div data-testid="first-scan-fixed" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "#86efac" }}>
          <span aria-hidden="true">✓</span>
          <span>
            Fixed {applyResult.files_fixed} file(s) — committed as{" "}
            {applyResult.commit_url ? (
              <a href={applyResult.commit_url} target="_blank" rel="noreferrer"
                 data-testid="first-scan-commit-link" style={{ color: "#7dd3fc" }}>
                {applyResult.commit_sha.slice(0, 7)}
              </a>
            ) : (
              <code>{applyResult.commit_sha.slice(0, 7)}</code>
            )}
          </span>
        </div>
      </div>
    );
  }

  // status === "ready"
  const cards = state.cards || [];
  const primary = cards[0];
  const restCount = (cards.length - 1) + (state.more_count || 0);

  return (
    <div data-testid="first-scan-card" style={cardStyle}>
      <div style={{ fontSize: 13, fontWeight: 600, color: "#7dd3fc" }}>
        I looked at your site. Here&apos;s what I found:
      </div>
      {primary && (
        <ul data-testid="first-scan-primary-finding" style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--text-dim, #555)" }}>
          {primary.bullets.map((b, i) => (
            <li key={i}>{b}</li>
          ))}
        </ul>
      )}
      {restCount > 0 && (
        <div data-testid="first-scan-more-count" style={{ fontSize: 11, color: "var(--text-dim, #777)" }}>
          {restCount} more improvement{restCount === 1 ? "" : "s"}. I can fix them too.
        </div>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          data-testid="first-scan-fix-all-btn"
          disabled={applying}
          onClick={handleFix}
          className="btn-primary"
          style={{ padding: "6px 14px", fontSize: 12 }}
        >
          {applying ? "Working…" : `Fix all ${state.findings_count || cards.length} for me`}
        </button>
        <button
          type="button"
          data-testid="first-scan-dismiss-btn"
          disabled={applying}
          onClick={() => setDismissed(true)}
          className="btn-ghost"
          style={{ padding: "6px 14px", fontSize: 12 }}
        >
          Not now
        </button>
      </div>
      {applyResult?.error && (
        <div data-testid="first-scan-apply-error" style={{ fontSize: 12, color: "#fca5a5" }}>
          {applyResult.error}
        </div>
      )}
    </div>
  );
}
