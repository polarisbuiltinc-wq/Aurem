/**
 * PaletteNudgeBubble.test.jsx — NEXT ROUND Item 2 (2026-09-01)
 *
 * t_palette_nudge_shows_inline_before_after — a nudged palette
 *   renders a visible before/after swatch pair + note.
 * t_palette_note_no_jargon — the rendered note text has no
 *   WCAG/luminance/token words (checked on the DOM text, not just
 *   the backend string, so a future UI change can't reintroduce
 *   jargon silently).
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import PaletteNudgeBubble from "../PaletteNudgeBubble.jsx";

const NUDGE = {
  before_hex: "#cccccc",
  after_hex: "#5c5c5c",
  before_ratio: 1.6,
  after_ratio: 4.6,
  note: "I made some text a touch darker — it wasn't easy to read against its background before (was 1.6:1, now 4.6:1).",
};

describe("NEXT ROUND Item 2 · PaletteNudgeBubble", () => {
  it("t_palette_nudge_shows_inline_before_after — renders both swatches + the note", () => {
    render(<PaletteNudgeBubble nudge={NUDGE} />);
    expect(screen.getByTestId("palette-nudge-bubble")).toBeTruthy();
    expect(screen.getByTestId("palette-swatch-before")).toBeTruthy();
    expect(screen.getByTestId("palette-swatch-after")).toBeTruthy();
    expect(screen.getByTestId("palette-nudge-note").textContent).toContain("was 1.6:1, now 4.6:1");
  });

  it("t_palette_note_no_jargon — no WCAG/luminance/token words in the rendered note", () => {
    render(<PaletteNudgeBubble nudge={NUDGE} />);
    const text = screen.getByTestId("palette-nudge-note").textContent.toLowerCase();
    for (const banned of ["wcag", "luminance", "token"]) {
      expect(text).not.toContain(banned);
    }
  });

  it("renders nothing when no nudge is passed", () => {
    const { container } = render(<PaletteNudgeBubble nudge={null} />);
    expect(container.innerHTML).toBe("");
  });
});
