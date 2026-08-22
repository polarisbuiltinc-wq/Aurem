/**
 * components/FindingsTeaserStrip.jsx — 2026-08-23
 *
 * The ONE new chat-native surface for the findings-to-fix bridge.
 * Renders once ORA's reply fully completes (never mid-generation —
 * the parent only passes new entries via `newFindings` after the
 * `onDone` SSE event) and shows a plain-English bundle of what was
 * found ("Found 4 issues worth reviewing — 2 urgent"), with a
 * "Review & fix →" CTA that expands an INLINE findings list (no new
 * drawer/modal component — founder-approved: "inline expansion
 * within FindingsTeaserStrip itself"), a "Fix all" button that
 * reuses the existing <BulkFixConfirmModal/> + global fix pipeline,
 * and a "Later" dismiss (reuses the same 24h dismiss endpoint
 * findings already use elsewhere).
 *
 * Wrapped in its own error boundary at the bottom of this file —
 * a broken teaser must never hide or break ORA's underlying reply.
 *
 * Dedup/staleness (founder-approved spec):
 *   1. Only one strip visible at a time — new findings MERGE into
 *      this same instance's count, never stack a second strip.
 *      Parent mounts this component with `key={sessionId}` so state
 *      cleanly resets on session switch instead of leaking counts
 *      across chats.
 *   2. Before rendering a count, re-check against the backend
 *      (GET /findings/backlog?ids=…) so an externally-resolved
 *      finding (fixed via chat, dashboard, or dismissed elsewhere)
 *      drops out instead of showing a stale count. Also listens for
 *      the global `aurem:finding-fixed` event (fired by the SAME
 *      FixJobContext regardless of which surface started the job)
 *      for an instant drop, plus a 30s poll safety net for
 *      out-of-band resolutions (e.g. Settings page).
 *   3. "Later" persists a 24h dismiss server-side (cross-device),
 *      scoped to exactly the finding set currently shown.
 */
import React, { useEffect, useMemo, useState, useCallback } from "react";
import { AlertTriangle, ChevronDown, ChevronUp, ShieldAlert, Bug, X } from "lucide-react";
import { api } from "../lib/api";
import BulkFixConfirmModal from "./BulkFixConfirmModal";

const POLL_INTERVAL_MS = 30_000;

function FindingsTeaserStripInner({ newFindings, projectId }) {
  const [tracked, setTracked] = useState([]);
  const [dismissed, setDismissed] = useState(false);
  const [batchId, setBatchId] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [fixAllOpen, setFixAllOpen] = useState(false);

  // Merge new turn(s) into the tracked set, deduped by finding_id —
  // requirement #1 (single strip, counts merge, never stack).
  useEffect(() => {
    if (!Array.isArray(newFindings) || !newFindings.length) return;
    setTracked((cur) => {
      const seen = new Set(cur.map((f) => f.finding_id));
      const merged = cur.slice();
      for (const f of newFindings) {
        if (f?.finding_id && !seen.has(f.finding_id)) {
          seen.add(f.finding_id);
          merged.push(f);
        }
      }
      return merged;
    });
    // A genuinely new finding arriving means the user is actively
    // auditing again — surface the strip even if an older batch had
    // been dismissed.
    setDismissed(false);
  }, [newFindings]);

  const ids = useMemo(
    () => tracked.map((f) => f.finding_id).filter(Boolean),
    [tracked],
  );

  // Requirement #2 — staleness check + teaser-specific dismiss state,
  // re-verified against the backend whenever the tracked set changes,
  // and again every POLL_INTERVAL_MS so an out-of-band resolve (e.g.
  // the Settings page's manual "Resolve" button, which doesn't fire
  // `aurem:finding-fixed`) still clears the strip in a reasonable time.
  const refreshTrackedStatus = useCallback(() => {
    if (!ids.length || !projectId) return;
    api.get("/findings/backlog", {
      params: { project_id: projectId, ids: ids.join(",") },
    }).then((res) => {
      const d = res.data || {};
      const status = d.tracked_status || {};
      const matchedById = new Map(
        (d.matched || []).map((m) => [m.finding_id, m]),
      );
      setTracked((cur) => cur
        .filter((f) => status[f.finding_id] !== "resolved")
        // Enrich with rule_id (needed by the fix pipeline's LLM
        // re-validation step) — the chat-stream payload never
        // carried it, only the persisted backlog doc does.
        .map((f) => {
          const m = matchedById.get(f.finding_id);
          return m ? { ...f, rule_id: f.rule_id || m.rule_id } : f;
        }));
      setBatchId(d.teaser_batch_id || null);
      setDismissed(!!d.teaser_dismissed);
    }).catch(() => { /* silent — teaser just uses last-known state */ });
  }, [ids.join(","), projectId]);

  useEffect(() => {
    refreshTrackedStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids.join(","), projectId]);

  useEffect(() => {
    if (!ids.length || dismissed) return undefined;
    const t = setInterval(refreshTrackedStatus, POLL_INTERVAL_MS);
    return () => clearInterval(t);
  }, [ids.length, dismissed, refreshTrackedStatus]);

  // Instant drop the moment a fix lands — fired globally by
  // FixJobContext regardless of which surface (chat Fix all,
  // CodebaseHealth per-item fix, etc.) started the job.
  useEffect(() => {
    function onFixed(e) {
      const fid = e?.detail?.finding_id;
      if (!fid) return;
      setTracked((cur) => cur.filter((f) => f.finding_id !== fid));
    }
    window.addEventListener("aurem:finding-fixed", onFixed);
    return () => window.removeEventListener("aurem:finding-fixed", onFixed);
  }, []);

  if (!tracked.length || dismissed) return null;

  const criticalCount = tracked.filter((f) => f.severity === "critical").length;
  const totalCount = tracked.length;

  const handleDismiss = async () => {
    if (busy) return;
    setBusy(true);
    setDismissed(true); // optimistic — never block the UI on the network
    setExpanded(false);
    try {
      await api.post("/findings/dismiss", {
        project_id: projectId,
        finding_batch_id: batchId || `chat_teaser::${ids.slice(0, 20).sort().join("|")}`,
      });
    } catch { /* silent — worst case it resurfaces sooner than 24h */ }
    setBusy(false);
  };

  // Shape tracked findings into what BulkFixConfirmModal → POST
  // /fix-pipeline/bulk → apply_finding_fix already expects (same
  // contract the CodebaseHealth scanner path uses).
  const findingsForFix = tracked.map((f) => ({
    id: f.finding_id,
    file: f.file,
    line: f.line,
    severity: f.severity,
    rule_id: f.rule_id || f.finding_id,
    title: f.title,
    message: f.message,
  }));

  return (
    <div data-testid="findings-teaser-strip" className="mb-2">
      <div
        role="status"
        aria-live="polite"
        className={
          "flex flex-wrap items-center gap-2 rounded-md border px-3 py-1.5 text-[11px] " +
          (criticalCount > 0
            ? "bg-red-500/10 border-red-500/30 text-red-200"
            : "bg-amber-500/10 border-amber-500/30 text-amber-200")
        }
      >
        <AlertTriangle className="size-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate">
          Found <b>{totalCount} thing{totalCount === 1 ? "" : "s"}</b> worth reviewing
          {criticalCount > 0 && <> — <b>{criticalCount} urgent</b></>}
        </span>
        <button
          type="button"
          data-testid="findings-teaser-review-btn"
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1 text-[11px] font-medium hover:underline shrink-0"
        >
          Review &amp; fix
          {expanded ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
        </button>
        <button
          type="button"
          data-testid="findings-teaser-later-btn"
          onClick={handleDismiss}
          aria-label="Remind me later"
          className="ml-1 shrink-0 rounded p-0.5 opacity-70 hover:bg-white/10 hover:opacity-100"
        >
          <X className="size-3" />
        </button>
      </div>

      {expanded && (
        <div
          data-testid="findings-teaser-expanded-panel"
          className="mt-1.5 rounded-md border border-white/10 bg-black/20 p-2.5"
        >
          <ul className="flex flex-col gap-1.5 max-h-64 overflow-y-auto">
            {tracked.slice(0, 30).map((f) => (
              <li
                key={f.finding_id}
                data-testid={`findings-teaser-row-${f.finding_id}`}
                className="flex items-start gap-2 text-[11px] text-foreground/90"
              >
                {f.severity === "critical"
                  ? <ShieldAlert className="size-3.5 mt-0.5 shrink-0 text-red-400" />
                  : <Bug className="size-3.5 mt-0.5 shrink-0 text-amber-400" />}
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{f.title || "Untitled finding"}</div>
                  {f.file && (
                    <div className="truncate font-mono text-[10px] opacity-60">
                      {f.file}{f.line ? `:${f.line}` : ""}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {tracked.length > 30 && (
            <div className="mt-1 text-[10px] opacity-60">
              +{tracked.length - 30} more not shown
            </div>
          )}
          <div className="mt-2.5 flex flex-wrap items-center justify-end gap-2">
            <button
              type="button"
              data-testid="findings-teaser-collapse-btn"
              onClick={() => setExpanded(false)}
              className="rounded px-2.5 py-1 text-[11px] text-foreground/70 hover:bg-white/10"
            >
              Collapse
            </button>
            <button
              type="button"
              data-testid="findings-teaser-fix-all-btn"
              onClick={() => setFixAllOpen(true)}
              className="rounded px-3 py-1 text-[11px] font-semibold bg-primary text-primary-foreground hover:opacity-90"
            >
              Fix all ({totalCount})
            </button>
          </div>
        </div>
      )}

      <BulkFixConfirmModal
        open={fixAllOpen}
        onClose={() => { setFixAllOpen(false); setExpanded(false); }}
        projectId={projectId}
        findings={findingsForFix}
        category="Chat findings"
        tool="health-scan"
      />
    </div>
  );
}

// ── Error boundary — a broken teaser must NEVER break the reply above it ──
class FindingsTeaserBoundary extends React.Component {
  constructor(props) { super(props); this.state = { broke: false }; }
  static getDerivedStateFromError() { return { broke: true }; }
  componentDidCatch(err) {
    // eslint-disable-next-line no-console
    console.error("[FindingsTeaserStrip] isolated render error:", err);
  }
  render() {
    if (this.state.broke) return null;
    return this.props.children;
  }
}

export default function FindingsTeaserStrip(props) {
  if (!props.projectId || !Array.isArray(props.newFindings)) return null;
  return (
    <FindingsTeaserBoundary>
      <FindingsTeaserStripInner {...props} />
    </FindingsTeaserBoundary>
  );
}
