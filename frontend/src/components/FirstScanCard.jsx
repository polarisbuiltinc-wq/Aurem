/**
 * components/FirstScanCard.jsx — Onboarding Step 4 · S-B (2026-08-26).
 * BUILD PROMPT v4 · Phase A (2026-08-26) — read-back fix + idempotency +
 * WorkCard render, flag-gated via `workcard_first_scan` (default OFF,
 * allowlisted test account only until proven stable — D2/D3).
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
import { CheckCircle2, Search, AlertTriangle, Loader2 } from "lucide-react";
import { api } from "../lib/api";
import WorkCard from "./WorkCard";

const POLL_MS = 2000;
// Overnight run (2026-08-27) — disclosed additive change: env-overridable so
// the >60s heartbeat/timeout branch can be proven live in Preview without a
// 60s+ real wait, without touching any protected file. Production default
// stays 60000 unless REACT_APP_FIRST_SCAN_MAX_POLL_MS is explicitly set.
const MAX_POLL_MS = Number(process.env.REACT_APP_FIRST_SCAN_MAX_POLL_MS) || 60_000;

// Reuses the existing "ora:prefill" event (already wired in ChatPanel.jsx)
// instead of adding a new endpoint/bridge — Phase A guardrail: reuse before
// build.
function promptChat(message) {
  try {
    window.dispatchEvent(new CustomEvent("ora:prefill", { detail: { message } }));
  } catch { /* ignore */ }
}

export default function FirstScanCard({ projectId }) {
  const [state, setState] = useState(null);   // full /status response
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState(null); // {commit_sha, commit_url, files_fixed}
  const [dismissed, setDismissed] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const [lastPolledAt, setLastPolledAt] = useState(null);
  const [, setTick] = useState(0); // re-renders the heartbeat clock every 1s
  const [ackedFix, setAckedFix] = useState(false); // 2026-08-30: fixed-banner acknowledge-once
  const viewedSentRef = useRef(false);
  const pollStartRef = useRef(null);
  const statusRef = useRef(null);
  const applyingRef = useRef(false); // synchronous double-click guard (ahead of React's async setState)
  const ackSentRef = useRef(false);

  // Read-back fix (fixed) computed early so the ack hooks below can use it —
  // must stay unconditional (before any early `return null`) per rules-of-hooks.
  const fixed = applyResult?.commit_sha ? applyResult
    : (state?.commit_sha ? state : null);

  // 2026-08-30 fix (Issue A): the fixed banner must show ONCE then never
  // re-render on refresh/relogin. Server truth (`fix_acknowledged`) is the
  // source, not localStorage — survives incognito/clear/other-device.
  useEffect(() => {
    if (state?.fix_acknowledged) setAckedFix(true);
  }, [state?.fix_acknowledged]);

  const acknowledgeFix = useCallback(() => {
    if (ackSentRef.current) return;
    ackSentRef.current = true;
    setAckedFix(true);
    api.post("/onboarding/first-scan/acknowledge-fix", { project_id: projectId }).catch(() => {});
  }, [projectId]);

  // Auto-vanish: once seen, acknowledge after 7s so it doesn't linger forever
  // (still dismissible immediately via the "Got it" button below).
  useEffect(() => {
    if (!fixed || ackedFix) return undefined;
    const t = setTimeout(acknowledgeFix, 7000);
    return () => clearTimeout(t);
  }, [fixed, ackedFix, acknowledgeFix]);

  // Reset the ack/dismiss session flags on a project switch — this card is
  // not remounted (no `key`) when the active project changes, so without
  // this a just-acknowledged project A would wrongly suppress project B's
  // own, unrelated fresh fixed-banner.
  useEffect(() => {
    ackSentRef.current = false;
    setAckedFix(false);
  }, [projectId]);

  const poll = useCallback(async () => {
    if (!projectId || projectId === "home") return;
    try {
      const r = await api.get("/onboarding/first-scan/status", {
        params: { project_id: projectId },
      });
      setState(r.data);
      statusRef.current = r.data?.status;
      setLastPolledAt(Date.now());
    } catch (_e) {
      // best-effort — never raise to the UI
    }
  }, [projectId]);

  const startPollLoop = useCallback(() => {
    pollStartRef.current = Date.now();
    setTimedOut(false);
    poll();
  }, [poll]);

  useEffect(() => {
    if (!projectId || projectId === "home") return;
    startPollLoop();
    let stopped = false;
    const t = setInterval(() => {
      if (stopped) return;
      const st = statusRef.current;
      const active = st === "scanning" || st === "still_scanning" || st == null;
      if (!active) { clearInterval(t); return; }
      if (Date.now() - pollStartRef.current > MAX_POLL_MS) {
        setTimedOut(true);
        clearInterval(t);
        return;
      }
      poll();
    }, POLL_MS);
    return () => { stopped = true; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Heartbeat clock — ticks every second only while actively scanning or
  // timed-out, so "last checked Ns ago" stays live instead of frozen.
  useEffect(() => {
    const st = state?.status;
    if (st !== "scanning" && st !== "still_scanning" && !timedOut) return undefined;
    const h = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(h);
  }, [state?.status, timedOut]);

  useEffect(() => {
    if (state?.status === "ready" && !viewedSentRef.current) {
      viewedSentRef.current = true;
      api.post("/onboarding/first-scan/viewed", { project_id: projectId }).catch(() => {});
    }
  }, [state, projectId]);

  const handleFix = useCallback(async () => {
    if (applyingRef.current) return;
    applyingRef.current = true;
    setApplying(true);
    try {
      const r = await api.post("/onboarding/first-scan/apply", { project_id: projectId });
      setApplyResult(r.data);
    } catch (e) {
      setApplyResult({ ok: false, error: e?.response?.data?.detail || "Couldn't apply the fix." });
    } finally {
      setApplying(false);
      applyingRef.current = false;
    }
  }, [projectId]);

  const handleRefresh = useCallback(() => { startPollLoop(); }, [startPollLoop]);

  if (!projectId || projectId === "home" || dismissed) return null;
  if (!state) return null;

  const workcardOn = !!state.workcard_enabled;

  if (!workcardOn) {
    if (state.status === "skipped") return null;
    return (
      <LegacyFirstScanCard
        state={state}
        applying={applying}
        applyResult={applyResult}
        handleFix={handleFix}
        onDismiss={() => setDismissed(true)}
      />
    );
  }

  // ── Phase A WorkCard render (flag ON) ──────────────────────────────
  if (state.status === "skipped") {
    return (
      <WorkCard
        testId="first-scan-card"
        tone="grey"
        badgeLabel="Skipped"
        title="Free first-scan already used"
        body="You've already used your free first-scan on another repo — but I'm happy to check this one too."
        primaryAction={{
          label: "Scan this repo",
          testId: "first-scan-request-scan-btn",
          onClick: () => promptChat("Can you check this repo for SEO issues like missing meta tags, titles, or alt text?"),
        }}
      />
    );
  }

  if (state.status === "scanning" || state.status === "still_scanning") {
    if (timedOut) {
      const secsAgo = lastPolledAt ? Math.max(0, Math.round((Date.now() - lastPolledAt) / 1000)) : null;
      return (
        <WorkCard
          testId="first-scan-card"
          tone="amber"
          badgeLabel="Still working"
          icon={<Loader2 size={14} />}
          title="Still working on your scan"
          body={secsAgo != null
            ? `Last checked ${secsAgo}s ago — this can take longer on larger repos.`
            : "This can take longer on larger repos."}
          primaryAction={{ label: "Refresh", testId: "first-scan-refresh-btn", onClick: handleRefresh }}
        />
      );
    }
    return (
      <WorkCard
        testId="first-scan-card"
        tone="blue"
        badgeLabel="Scanning"
        icon={<Loader2 size={14} className="animate-spin" />}
        title={state.status === "still_scanning"
          ? (state.message || "Still scanning — you can start chatting in the meantime.")
          : "Connected — scanning your site…"}
      />
    );
  }

  if (state.status === "error") {
    return (
      <WorkCard
        testId="first-scan-card"
        tone="red"
        badgeLabel="Scan failed"
        icon={<AlertTriangle size={14} />}
        title="I couldn't scan your repo"
        body={state.message || "Something went wrong on my end."}
        primaryAction={{ label: "Retry scan", testId: "first-scan-retry-btn", onClick: handleRefresh }}
        secondaryAction={{ label: "Just chat instead", testId: "first-scan-chat-instead-btn", onClick: () => setDismissed(true) }}
      />
    );
  }

  if (state.status === "clean") {
    return (
      <WorkCard
        testId="first-scan-card"
        tone="green"
        badgeLabel="Clean"
        icon={<CheckCircle2 size={18} />}
        title="Your site looks in good shape!"
        body="No SEO issues found on your homepage — titles, meta description, and alt text all check out."
        primaryAction={{
          label: "Start a task",
          testId: "first-scan-start-task-btn",
          onClick: () => promptChat("What should we build or improve next?"),
        }}
      />
    );
  }

  // status === "ready", fixed already computed above (read-back: prefer a
  // fresh apply result from this session, else the server-saved fix).
  // 2026-08-30 fix (Issue A): once acknowledged (server-side, this session
  // or a prior one), render nothing — the fix already happened, there's
  // nothing left to show. Prevents the perpetual re-appear on refresh/relogin.
  if (fixed) {
    if (ackedFix) return null;
    return (
      <WorkCard
        testId="first-scan-card"
        tone="green"
        badgeLabel="Fixed"
        icon={<CheckCircle2 size={16} />}
        title="Fixed and shipped"
        body={
          <span data-testid="first-scan-fixed">
            Fixed {fixed.files_fixed ?? 0} file(s) — committed as{" "}
            {fixed.commit_url ? (
              <a href={fixed.commit_url} target="_blank" rel="noreferrer"
                 data-testid="first-scan-commit-link" style={{ color: "#7dd3fc" }}>
                {String(fixed.commit_sha).slice(0, 7)}
              </a>
            ) : (
              <code>{String(fixed.commit_sha).slice(0, 7)}</code>
            )}
          </span>
        }
        secondaryAction={{ label: "Got it", testId: "first-scan-fixed-ack-btn", onClick: acknowledgeFix }}
      />
    );
  }

  const cards = state.cards || [];
  const primary = cards[0];
  const restCount = (cards.length - 1) + (state.more_count || 0);

  return (
    <WorkCard
      testId="first-scan-card"
      tone="blue"
      badgeLabel="Findings"
      icon={<Search size={14} />}
      title="I looked at your site. Here's what I found:"
      body={
        <>
          {primary && (
            <ul data-testid="first-scan-primary-finding" style={{ margin: 0, paddingLeft: 18 }}>
              {primary.bullets.map((b, i) => <li key={i}>{b}</li>)}
            </ul>
          )}
          {restCount > 0 && (
            <div data-testid="first-scan-more-count" style={{ marginTop: 4, color: "var(--text-dim, #777)" }}>
              {restCount} more improvement{restCount === 1 ? "" : "s"}. I can fix them too.
            </div>
          )}
          {applyResult?.error && (
            <div data-testid="first-scan-apply-error" style={{ marginTop: 4, color: "#fca5a5" }}>
              {applyResult.error}
            </div>
          )}
        </>
      }
      primaryAction={{
        label: applying ? "Working…" : `Fix all ${state.findings_count || cards.length} for me`,
        testId: "first-scan-fix-all-btn",
        disabled: applying,
        onClick: handleFix,
      }}
      secondaryAction={{ label: "Not now", testId: "first-scan-dismiss-btn", disabled: applying, onClick: () => setDismissed(true) }}
    />
  );
}

// ── Legacy render (flag OFF) — unchanged from pre-Phase-A behaviour. ────
// Kept verbatim as the rollback target: if the WorkCard path regresses,
// flipping the flag off restores exactly this.
function LegacyFirstScanCard({ state, applying, applyResult, handleFix, onDismiss }) {
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
          onClick={onDismiss}
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
