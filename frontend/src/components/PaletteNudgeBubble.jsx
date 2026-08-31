/**
 * PaletteNudgeBubble.jsx — Item 2 (2026-08-31)
 *
 * Makes a contrast-guard palette nudge VISIBLE to the owner in chat,
 * not just in the logs. Renders a before/after color swatch pair +
 * the plain-English note built by
 * services/contrast_guard.py::describe_nudge() (no WCAG/luminance/
 * token words — see test_palette_note_no_jargon). Purely a rendering
 * component; the guard math itself is unchanged.
 */
import React from "react";

function Swatch({ hex, label }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span
        data-testid={`palette-swatch-${label}`}
        style={{
          width: 20, height: 20, borderRadius: 5,
          background: hex,
          border: "1px solid rgba(255,255,255,0.18)",
          flexShrink: 0,
        }}
        title={hex}
      />
      <span style={{ fontSize: 10, color: "var(--text-faint)", fontFamily: "ui-monospace, monospace" }}>
        {hex}
      </span>
    </div>
  );
}

export default function PaletteNudgeBubble({ nudge }) {
  if (!nudge) return null;
  return (
    <div
      data-testid="palette-nudge-bubble"
      style={{
        display: "flex", alignItems: "center", gap: 12,
        margin: "8px 0", padding: "10px 12px",
        border: "1px solid var(--border)", borderRadius: 8,
        background: "rgba(255,255,255,0.02)",
      }}
    >
      <Swatch hex={nudge.before_hex} label="before" />
      <span style={{ color: "var(--text-faint)", fontSize: 13 }}>→</span>
      <Swatch hex={nudge.after_hex} label="after" />
      <span data-testid="palette-nudge-note" style={{ fontSize: 12, color: "var(--text-dim)", marginLeft: 4 }}>
        {nudge.note}
      </span>
    </div>
  );
}
