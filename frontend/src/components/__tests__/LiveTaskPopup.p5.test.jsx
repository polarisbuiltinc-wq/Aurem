/**
 * LiveTaskPopup.p5.test.jsx — 2026-08-27, P5 (Journey/Intent-Grounding
 * build round).
 *
 * Reproduces + fixes: "6 chips for one small task — 'Reading repo'
 * ×2, 'Thinking', 'Reading repo', 'Writing', 'Security check'" — the
 * old logic only collapsed CONSECUTIVE same-kind steps, so the two
 * non-adjacent "Reading repo" visits rendered as two separate chips.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { api } from "../../lib/api";
import LiveTaskPopup from "../LiveTaskPopup.jsx";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn() },
}));

const TASK_WITH_NON_CONSECUTIVE_REPEAT = {
  status: "running",
  steps: [
    { kind: "phase_read",   step: "Reading repo",   ts: 1 },
    { kind: "phase_think",  step: "Thinking",       ts: 2 },
    { kind: "phase_read",   step: "Reading repo",   ts: 3 },  // non-consecutive repeat
    { kind: "phase_write",  step: "Writing",        ts: 4 },
    { kind: "phase_verify", step: "Security check", ts: 5 },
  ],
  files_changed: [], vanguard_findings: [], files_read: [],
};

describe("2026-08-27 P5 · step chips aggregate non-consecutive repeats", () => {
  beforeEach(() => {
    api.get.mockResolvedValue({ data: { task: TASK_WITH_NON_CONSECUTIVE_REPEAT } });
  });

  it("renders exactly ONE 'Reading repo' chip (not two) once expanded", async () => {
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("ltp-phase-strip")).toBeInTheDocument());
    fireEvent.mouseEnter(screen.getByTestId("ltp-phase-strip"));
    const readChips = screen.getAllByTestId("ltp-phase-chip-phase_read");
    expect(readChips.length).toBe(1);
    expect(readChips[0].textContent).toContain("×2");
  });

  it("collapses to just the active chip by default (progressive disclosure)", async () => {
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("ltp-phase-strip")).toBeInTheDocument());
    // Default (not hovered/clicked) — only the active step's chip shown.
    expect(screen.getByTestId("ltp-phase-chip-phase_verify")).toBeInTheDocument();
    expect(screen.queryByTestId("ltp-phase-chip-phase_read")).not.toBeInTheDocument();
    expect(screen.getByTestId("ltp-phase-strip-more")).toBeInTheDocument();
  });

  it("caps the expanded chip strip at 4 distinct phases", async () => {
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("ltp-phase-strip")).toBeInTheDocument());
    fireEvent.mouseEnter(screen.getByTestId("ltp-phase-strip"));
    // 4 distinct kinds in TASK_WITH_NON_CONSECUTIVE_REPEAT: read, think, write, verify.
    const allChips = screen.getAllByTestId(/^ltp-phase-chip-/);
    expect(allChips.length).toBeLessThanOrEqual(4);
  });
});
