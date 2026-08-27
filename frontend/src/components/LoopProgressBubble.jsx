/**
 * LoopProgressBubble.jsx — Iter 331 · collapsible loop-progress chat
 * bubble (founder-reported: loop step lines rendered as an ever-
 * growing flat list in the transcript; finished runs should collapse
 * to a one-line summary and expand on click).
 *
 * Detection is content-based (`**Step N / 5 —` lines from
 * ChatPanel.renderEventLine) so PERSISTED history turns from before
 * this iteration collapse too — not just live `loopLive` messages.
 *
 * Behaviour:
 *   - streaming (live run)  → expanded, header shows "Running…"
 *   - terminal / historical → collapsed by default, click to expand
 */
import React, { useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";

export function isLoopProgressContent(text) {
  if (!text) return false;
  return /\*\*Step \d \/ 5 —/.test(text)
      || /Plan ready — awaiting.*approval/i.test(text);
}

// 2026-08-27 · P4 (Journey/Intent-Grounding build round) — a plan
// awaiting the user's approval is NOT a terminal/historical state; it
// must stay expanded until the user acts. Previously `expanded =
// streaming || open` collapsed the SECOND the SSE stream paused for
// approval (which happens the instant the plan is ready), hiding the
// plan behind a one-line "Finished" summary right as the approval
// control needed it visible. Deliberately checked AFTER the terminal
// markers below (Aborted/Failed/Shipped) — a real terminal event that
// happened after the plan supersedes "awaiting approval".
function isAwaitingApproval(text) {
  return /Plan ready — awaiting.*approval/i.test(text || "")
      && !/\*\*(Aborted|Failed)\*\*/.test(text || "")
      && !/ship complete|shipped/i.test(text || "");
}

function summarize(text, streaming) {
  const stepCount = (text.match(/\*\*Step \d \/ 5 —/g) || []).length;
  if (/\*\*Aborted\*\*/.test(text))
    return { stepCount, status: "Aborted", color: "var(--text-faint)" };
  if (/\*\*Failed\*\*/.test(text))
    return { stepCount, status: "Failed", color: "var(--danger, #ef4444)" };
  if (/ship complete|shipped/i.test(text))
    return { stepCount, status: "Shipped", color: "var(--ok, #22c55e)" };
  if (isAwaitingApproval(text))
    return { stepCount, status: "Awaiting approval", color: "var(--accent-2, #e8a020)" };
  if (streaming)
    return { stepCount, status: "Running…", color: "var(--accent-2, #e8a020)" };
  return { stepCount, status: "Finished", color: "var(--text-dim)" };
}

export default function LoopProgressBubble({ text, streaming, children }) {
  const [open, setOpen] = useState(false);
  const awaitingApproval = isAwaitingApproval(text || "");
  // OR'd in deliberately — even if the user clicks to "collapse" while
  // a plan is awaiting their decision, it stays pinned visible; there
  // is no code path where an approval control's subject is hidden.
  const expanded = streaming || open || awaitingApproval;
  const { stepCount, status, color } = summarize(text || "", streaming);
  // Feb 2026 — Founder repro: expanding a completed loop's collapsed
  // "Loop run · N step events [Aborted/Finished]" bubble silently
  // reset the composer's tier indicator (CASUAL → AGENTIC) AND the
  // Loop toggle (LOOP ON → LOOP OFF). Root cause turned out to be
  // click-event bubbling up from this button to an ancestor handler
  // in the chat scroller. Stopping propagation on the toggle click
  // isolates this READ-ONLY historical view from any composer-state
  // mutations upstream — LoopProgressBubble is display-only, its
  // click MUST not touch composer state.
  const onToggle = (e) => {
    if (e && typeof e.stopPropagation === "function") e.stopPropagation();
    if (e && typeof e.preventDefault === "function") e.preventDefault();
    setOpen((v) => !v);
  };
  return (
    <div data-testid="loop-progress-bubble" data-expanded={expanded ? "true" : "false"}>
      <button
        type="button"
        data-testid="loop-progress-toggle"
        onClick={onToggle}
        onPointerDown={(e) => { if (e && typeof e.stopPropagation === "function") e.stopPropagation(); }}
        disabled={streaming}
        aria-expanded={expanded}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          width: "100%", textAlign: "left",
          background: "var(--panel-2, rgba(255,255,255,0.03))",
          border: "1px solid var(--border, rgba(255,255,255,0.08))",
          borderRadius: 8, padding: "7px 10px",
          color: "var(--text-dim)", cursor: streaming ? "default" : "pointer",
          fontSize: 12,
          fontFamily: "ui-monospace, SFMono-Regular, JetBrains Mono, monospace",
        }}
      >
        {expanded
          ? <ChevronDown size={13} style={{ flexShrink: 0 }} />
          : <ChevronRight size={13} style={{ flexShrink: 0 }} />}
        <span data-testid="loop-progress-count">
          Loop run · {stepCount} step event{stepCount === 1 ? "" : "s"}
        </span>
        <span
          data-testid="loop-progress-status"
          style={{ marginLeft: "auto", color, fontWeight: 600 }}
        >
          {status}
        </span>
      </button>
      {expanded && (
        <div data-testid="loop-progress-body" style={{ marginTop: 8 }}>
          {children}
        </div>
      )}
    </div>
  );
}
