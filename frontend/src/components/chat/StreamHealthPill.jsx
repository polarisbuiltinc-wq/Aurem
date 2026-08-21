/**
 * components/chat/StreamHealthPill.jsx — Iter 212m-153
 *
 * (Originally Iter 212m-57.)
 *
 * Tiny inline status pill that sits above the composer when the SSE
 * chat stream stalls.  Driven by `streamHealth` state in ChatPanel.
 *
 * 2026-08-22 — no longer exposes HOW slow things are. No "Slow
 * response" label, no "Ns silent" / "auto-retry in Ms" countdown, and
 * no manual "Retry now" button — retries are fully automatic and
 * invisible to the user now. Shows a reassuring, slowly-progressing
 * narrative phrase instead (same pool used by MessageBubble's
 * thinking indicator, via useFriendlyStatusPhrase).
 */
import React from "react";
import { Zap } from "lucide-react";
import { useFriendlyStatusPhrase } from "../../hooks/useFriendlyStatusPhrase";

export default function StreamHealthPill({ state, compact }) {
  const active = !!state && state.phase !== "idle";
  const phrase = useFriendlyStatusPhrase(active);
  if (!active) return null;
  const isReconnect = state.phase === "reconnecting";
  const accent = isReconnect ? "#EF4444" : "#FF6608";
  return (
    <div
      data-testid="chat-stream-health-pill"
      data-stream-phase={state.phase}
      role="status"
      aria-live="polite"
      style={{
        display: "inline-flex", alignItems: "center", gap: compact ? 7 : 10,
        padding: compact ? "5px 10px" : "12px 16px",
        margin: compact ? "4px 0 6px 4px" : "8px 12px 0",
        borderRadius: compact ? 8 : 12,
        width: compact ? "fit-content" : "auto",
        maxWidth: "100%",
        background: isReconnect
          ? "rgba(239,68,68,0.07)"
          : "rgba(255,102,8,0.07)",
        border: `1px solid ${accent}`,
        color: "#E5E5E5",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: compact ? 10.5 : 12,
        animation: "pillPulse 1.6s ease-in-out infinite",
      }}
    >
      <Zap size={compact ? 11 : 14} strokeWidth={2.5} style={{ color: accent, flexShrink: 0 }} />
      <span
        data-testid="chat-stream-health-phrase"
        style={{ flex: 1, minWidth: 0, color: "#E5E5E5" }}
      >
        {phrase}
      </span>
      <style>{`
        @keyframes pillPulse {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0.75; }
        }
      `}</style>
    </div>
  );
}
