/**
 * PromptStarterPanel.jsx — guided "what do I even type?" panel shown
 * above the composer only before a project's very first real message.
 *
 * 2026-08-22 — replaces the old 3-pill FirstMessageChips with 5 clear,
 * plain-English categories for users who have never used an AI coding
 * assistant before and have no mental model for how to phrase a
 * request. Same interaction contract as the chips it replaces:
 * clicking a card pre-fills the composer (via `onPick`) — it never
 * auto-sends, the user still reviews/edits before hitting send.
 * Disappears the moment a real message is sent (parent gates on
 * `messages.length <= 1`, same as before — no new persistence needed).
 */
import React from "react";
import { Sparkles, Bug, CheckCircle2, Zap, ShieldCheck } from "lucide-react";

const STARTERS = [
  {
    slug: "build-something-new",
    icon: Sparkles,
    label: "Build something new",
    example: "I want a contact form on my website",
  },
  {
    slug: "somethings-broken",
    icon: Bug,
    label: "Something's broken",
    example: "The login button doesn't work, please fix it",
  },
  {
    slug: "check-everything-working",
    icon: CheckCircle2,
    label: "Check everything is working",
    example: "Check my whole website for any bugs",
  },
  {
    slug: "add-new-feature",
    icon: Zap,
    label: "Add a new feature",
    example: "Let users upload a profile photo",
  },
  {
    slug: "security-check",
    icon: ShieldCheck,
    label: "Security check",
    example: "Check my code for any security problems",
  },
];

export default function PromptStarterPanel({ onPick }) {
  return (
    <div data-testid="prompt-starter-panel" style={{ marginBottom: 10 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(15.5rem, 1fr))",
          gap: 8,
        }}
      >
        {STARTERS.map(({ slug, icon: Icon, label, example }) => (
          <button
            key={slug}
            type="button"
            data-testid={`prompt-starter-card-${slug}`}
            onClick={() => onPick(example)}
            title={`Click to try: "${example}"`}
            style={{
              display: "flex", flexDirection: "column", gap: 4,
              alignItems: "flex-start", textAlign: "left",
              padding: "10px 12px", borderRadius: 12,
              color: "#ffb37a",
              background: "rgba(255,102,8,0.06)",
              border: "1px solid rgba(255,102,8,0.22)",
              cursor: "pointer",
              transition: "background 0.15s ease, border-color 0.15s ease, transform 0.1s ease",
              minWidth: 0,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "rgba(255,102,8,0.13)";
              e.currentTarget.style.borderColor = "rgba(255,102,8,0.4)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "rgba(255,102,8,0.06)";
              e.currentTarget.style.borderColor = "rgba(255,102,8,0.22)";
            }}
            onMouseDown={(e) => { e.currentTarget.style.transform = "scale(0.98)"; }}
            onMouseUp={(e) => { e.currentTarget.style.transform = "scale(1)"; }}
          >
            <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, width: "100%" }}>
              <Icon size={14} strokeWidth={2.2} style={{ flexShrink: 0 }} />
              <span style={{
                fontSize: 13, fontWeight: 600,
                color: "var(--text, #e4e6eb)",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {label}
              </span>
            </span>
            <span style={{
              fontSize: 12, lineHeight: 1.35,
              color: "var(--text-dim, #9aa0aa)",
              fontStyle: "italic",
              overflowWrap: "break-word",
            }}>
              &ldquo;{example}&rdquo;
            </span>
          </button>
        ))}
      </div>
      <div
        data-testid="prompt-starter-security-note"
        style={{
          display: "flex", alignItems: "center", gap: 6,
          marginTop: 8, padding: "0 2px",
          fontSize: 11, color: "var(--text-faint, #6a6f78)",
        }}
      >
        <ShieldCheck size={12} strokeWidth={2} style={{ flexShrink: 0, color: "#4ade80" }} />
        <span>
          Don&apos;t worry about phrasing it perfectly — just describe what you want in plain
          English. AUREM writes secure code by default; every change is scanned by Vanguard
          before it ships.
        </span>
      </div>
    </div>
  );
}
