/**
 * WorkCard.jsx — BUILD PROMPT v4 · Phase A shared render shell.
 *
 * One presentational anatomy for every background-work result card:
 *   [icon] title [status badge]
 *   body
 *   meta line (duration / counts / ref id)
 *   [primary action] [secondary action]
 *
 * Pure render — callers own all state, polling, and mutations. First
 * consumer: FirstScanCard (Phase A). Do not duplicate this shell per
 * surface; extend props instead (Phase B/C reuse it for the
 * security-scan strip and Loop gate/receipt cards).
 */
import React from "react";
import { Chip } from "./Chip";
import { isChipV2Enabled } from "../lib/chipFlag";

const TONE = {
  blue:  { soft: "rgba(56,189,248,0.14)",  fg: "#7dd3fc", border: "rgba(56,189,248,0.45)" },
  green: { soft: "rgba(34,197,94,0.16)",   fg: "#4ade80", border: "rgba(34,197,94,0.45)" },
  amber: { soft: "rgba(245,158,11,0.14)",  fg: "#fbbf24", border: "rgba(245,158,11,0.45)" },
  red:   { soft: "rgba(239,68,68,0.14)",   fg: "#fca5a5", border: "rgba(239,68,68,0.4)" },
  grey:  { soft: "rgba(148,163,184,0.10)", fg: "#cbd5e1", border: "rgba(148,163,184,0.30)" },
};
// Phase E — WorkCard tone names already match <Chip>'s tone set 1:1
// (blue→info, green→success, amber→warn, red→error, grey→neutral).
const CHIP_TONE = { blue: "info", green: "success", amber: "warn", red: "error", grey: "neutral" };

export default function WorkCard({
  testId,
  tone = "blue",
  badgeLabel,
  icon,
  title,
  body,
  meta,
  primaryAction,
  secondaryAction,
}) {
  const c = TONE[tone] || TONE.blue;
  return (
    <div
      data-testid={testId}
      data-tone={tone}
      role="status"
      aria-live="polite"
      style={{
        margin: "0 clamp(16px, 17.25%, 240px)",
        padding: "10px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        background: `linear-gradient(180deg, ${c.soft} 0%, rgba(255,255,255,0.02) 100%)`,
        borderTopLeftRadius: 12,
        borderTopRightRadius: 12,
        borderTop: `1px solid ${c.border}`,
        borderLeft: `1px solid ${c.border}`,
        borderRight: `1px solid ${c.border}`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {icon && <span aria-hidden="true" style={{ display: "inline-flex", color: c.fg }}>{icon}</span>}
        {title && (
          <span style={{ fontSize: 13, fontWeight: 600, color: c.fg, flex: 1 }}>
            {title}
          </span>
        )}
        {badgeLabel && (
          isChipV2Enabled() ? (
            <Chip
              size="sm"
              tone={CHIP_TONE[tone] || "neutral"}
              testId={testId ? `${testId}-badge` : undefined}
              className="chip-uppercase"
            >
              {badgeLabel}
            </Chip>
          ) : (
            <span
              data-testid={testId ? `${testId}-badge` : undefined}
              className="chip chip-sm chip-uppercase"
              style={{
                fontWeight: 700,
                background: c.soft, color: c.fg,
                border: `1px solid ${c.border}`,
              }}
            >
              {badgeLabel}
            </span>
          )
        )}
      </div>
      {body && (
        <div style={{ fontSize: 12, color: "var(--text-dim, #555)" }}>
          {body}
        </div>
      )}
      {meta && (
        <div
          data-testid={testId ? `${testId}-meta` : undefined}
          style={{
            fontSize: 11, color: "var(--text-faint, #8b949e)",
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {meta}
        </div>
      )}
      {(primaryAction || secondaryAction) && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {primaryAction && (
            <button
              type="button"
              data-testid={primaryAction.testId}
              disabled={primaryAction.disabled}
              onClick={primaryAction.onClick}
              className="btn-primary"
              style={{ padding: "6px 14px", fontSize: 12 }}
            >
              {primaryAction.label}
            </button>
          )}
          {secondaryAction && (
            <button
              type="button"
              data-testid={secondaryAction.testId}
              disabled={secondaryAction.disabled}
              onClick={secondaryAction.onClick}
              className="btn-ghost"
              style={{ padding: "6px 14px", fontSize: 12 }}
            >
              {secondaryAction.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
