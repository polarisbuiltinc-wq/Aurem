/**
 * FirstMessageChips.jsx — 3 example-prompt chips shown above the
 * composer only before a project's very first real message.
 *
 * 2026-08-20 — part of the "ORA Guide" system. Clicking a chip
 * pre-fills the composer (via `onPick`) — it never auto-sends, the
 * user still reviews/edits before hitting send. Disappears the moment
 * a real message is sent, permanently (per-project, not per-session —
 * localStorage, not sessionStorage), so it never resurfaces on a
 * project that's already past onboarding.
 */
import React from "react";

const CHIPS = [
  { label: "Fix a bug", prompt: "Can you find and fix a bug in this codebase?" },
  { label: "Add a feature", prompt: "I'd like to add a small feature — what would you suggest first?" },
  { label: "Explain this codebase", prompt: "Can you explain how this codebase is structured?" },
];

export default function FirstMessageChips({ onPick }) {
  return (
    <div
      data-testid="first-message-chips-row"
      style={{
        display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8,
      }}
    >
      {CHIPS.map((c) => (
        <button
          key={c.label}
          type="button"
          data-testid={`first-message-chip-${c.label.toLowerCase().replace(/\s+/g, "-")}`}
          onClick={() => onPick(c.prompt)}
          style={{
            display: "inline-flex", alignItems: "center",
            padding: "6px 12px", borderRadius: 999,
            fontSize: 12, fontWeight: 500,
            color: "#ffb37a",
            background: "rgba(255,102,8,0.08)",
            border: "1px solid rgba(255,102,8,0.28)",
            cursor: "pointer",
          }}
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}
