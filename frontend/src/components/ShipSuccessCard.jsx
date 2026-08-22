/**
 * ShipSuccessCard.jsx — 2026-06 · Ship ghost-state fix (Rule 6/10).
 *
 * Renders in the exact slot ShipPendingCard occupied, the moment a
 * loop-mode ship COMPLETES. Shows the real commit SHA + a GitHub link
 * — never invented: the card only renders from a terminal
 * state=completed·phase=ship event (SSE) or a polled loop_sessions
 * doc whose engine-persisted context.commit carries a real sha.
 */
import React from "react";
import { CheckCircle2, ExternalLink, GitCommit } from "lucide-react";

export default function ShipSuccessCard({ result, onDismiss }) {
  if (!result || !result.commitSha) return null;
  const short = String(result.commitSha).slice(0, 7);
  return (
    <div
      data-testid="ship-success-card"
      role="status"
      aria-label={`Shipped commit ${short}`}
      style={{
        display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
        padding: "12px 16px", margin: "10px 0", borderRadius: 12,
        background: "rgba(52, 211, 153, 0.08)",
        border: "1px solid rgba(52, 211, 153, 0.45)",
      }}
    >
      <CheckCircle2 size={18} style={{ color: "#4ade80", flexShrink: 0 }} />
      <span style={{ fontSize: 13.5, fontWeight: 700, color: "#4ade80" }}>
        Shipped ✅
      </span>
      <span
        data-testid="ship-success-sha"
        style={{
          display: "inline-flex", alignItems: "center", gap: 5,
          fontFamily: "monospace", fontSize: 12.5, color: "#a7f3d0",
          background: "rgba(52, 211, 153, 0.10)",
          padding: "2px 8px", borderRadius: 6,
        }}
        title={result.fullSha || result.commitSha}
      >
        <GitCommit size={13} />
        {short}
      </span>
      {result.repo && (
        <span style={{ fontSize: 12, color: "var(--text-faint, #9ca3af)" }}>
          {result.repo}{result.branch ? `@${result.branch}` : ""}
        </span>
      )}
      {result.htmlUrl && (
        <a
          data-testid="ship-success-github-link"
          href={result.htmlUrl}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            display: "inline-flex", alignItems: "center", gap: 4,
            fontSize: 12.5, fontWeight: 600, color: "#60a5fa",
            textDecoration: "none",
          }}
        >
          View on GitHub <ExternalLink size={12} />
        </a>
      )}
      {onDismiss && (
        <button
          data-testid="ship-success-dismiss-btn"
          onClick={onDismiss}
          style={{
            marginLeft: "auto", background: "none", border: "none",
            color: "var(--text-faint, #9ca3af)", cursor: "pointer",
            fontSize: 12,
          }}
        >
          Dismiss
        </button>
      )}
    </div>
  );
}
