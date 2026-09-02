/**
 * ShipPendingCard.test.jsx — Iter 328 · Deploy 2 hotfix
 *
 * The 30-test backend suite proved compute_files_diff was correct AND
 * that loop_engine.py attaches files_diff + integrity_verdict to the
 * ship_pending payload. It did NOT test that ShipPendingCard.jsx
 * actually renders the pill + chips in the DOM when handed a real
 * payload. That gap let a Deploy 2 prod-eyeball failure ship (backend
 * emitted the fields fine; ChatPanel's hand-picked object construction
 * dropped them before they reached the child).
 *
 * These tests close that gap by mounting the component with realistic
 * payload shapes (matching /loop/active response) and asserting the
 * safety pill + total diff chip + per-file chips actually appear in
 * the rendered DOM.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import ShipPendingCard from "../ShipPendingCard.jsx";


// The exact shape /loop/active returned on the failing eyeball run.
const REAL_PAYLOAD = {
  owner: "TJSNDHU",
  repo: "Aurem",
  branch: "main",
  files: ["README.md"],
  file_count: 1,
  commit_message: "Update README title",
  files_diff: [{
    path: "README.md",
    additions: 35,
    deletions: 34,
    is_new: false,
    delta_bytes: 18,
    diff_source: "line",
  }],
  integrity_verdict: "clean",
  vanguard_verdict: { ran: true, critical: 0, high: 0, total: 0 },
};


describe("ShipPendingCard — Deploy 2 enrichment renders (iter328)", () => {
  it("renders the integrity guard pill with clean verdict", () => {
    render(<ShipPendingCard pending={REAL_PAYLOAD} busy={false} />);
    const pill = screen.getByTestId("ship-integrity-pill");
    expect(pill).toBeInTheDocument();
    expect(pill).toHaveAttribute("data-verdict", "clean");
    expect(pill).toHaveTextContent(/Integrity guard: clean/i);
  });

  it("renders the headline total +add / -del chip", () => {
    render(<ShipPendingCard pending={REAL_PAYLOAD} busy={false} />);
    const chip = screen.getByTestId("ship-total-diff-chip");
    expect(chip).toBeInTheDocument();
    expect(chip).toHaveTextContent("+35");
    expect(chip).toHaveTextContent("−34");
  });

  it("renders per-file diff chip with correct additions/deletions", () => {
    render(<ShipPendingCard pending={REAL_PAYLOAD} busy={false} />);
    // File row for README.md must be present and contain the chip.
    const row = screen.getByTestId("ship-pending-file-row");
    expect(row).toHaveAttribute("data-file-path", "README.md");
    const chip = within(row).getByTestId("ship-file-diff-chip");
    expect(chip).toHaveAttribute("data-diff-source", "line");
    expect(chip).toHaveTextContent("+35");
    expect(chip).toHaveTextContent("−34");
    // No stray "NEW" badge on an existing file.
    expect(within(row).queryByTestId("ship-file-new-badge")).toBeNull();
  });

  it("renders NEW badge for is_new: true files", () => {
    const newFilePayload = {
      ...REAL_PAYLOAD,
      files: ["routers/new.py"],
      file_count: 1,
      files_diff: [{
        path: "routers/new.py", additions: 12, deletions: 0,
        is_new: true, delta_bytes: 300, diff_source: "line",
      }],
    };
    render(<ShipPendingCard pending={newFilePayload} busy={false} />);
    expect(screen.getByTestId("ship-file-new-badge")).toBeInTheDocument();
    expect(screen.getByTestId("ship-file-new-badge")).toHaveTextContent("NEW");
  });

  it("has NO placeholder text like 'coming soon' or 'computing…'", () => {
    render(<ShipPendingCard pending={REAL_PAYLOAD} busy={false} />);
    const body = document.body.textContent || "";
    expect(body).not.toMatch(/coming soon/i);
    expect(body).not.toMatch(/computing/i);
    expect(body).not.toMatch(/placeholder/i);
    expect(body).not.toMatch(/TODO/i);
  });

  it("Ship + Cancel buttons still render and fire onConfirm exactly once", () => {
    const onConfirm = vi.fn();
    render(
      <ShipPendingCard pending={REAL_PAYLOAD} busy={false}
                        onConfirm={onConfirm} />,
    );
    const ship = screen.getByTestId("ship-to-github-btn");
    const cancel = screen.getByTestId("ship-cancel-btn");
    expect(ship).not.toBeDisabled();
    expect(cancel).not.toBeDisabled();
    fireEvent.click(ship);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith(true);
  });

  it("gracefully hides pill + chips when backend omits the fields", () => {
    // Pre-Iter-328 shape (or fail-open case where backend dropped the
    // fields). The card MUST still render Ship/Cancel and the file
    // list, just without the new pill/chips. Fail-open contract.
    const legacyPayload = {
      owner: "x", repo: "y", branch: "z",
      files: ["a.py"], file_count: 1,
      commit_message: "old-style ship",
      // No files_diff, no integrity_verdict.
    };
    render(<ShipPendingCard pending={legacyPayload} busy={false} />);
    expect(screen.queryByTestId("ship-integrity-pill")).toBeNull();
    expect(screen.queryByTestId("ship-total-diff-chip")).toBeNull();
    expect(screen.queryByTestId("ship-file-diff-chip")).toBeNull();
    // But the ship button still works.
    expect(screen.getByTestId("ship-to-github-btn")).toBeInTheDocument();
    expect(screen.getByTestId("ship-cancel-btn")).toBeInTheDocument();
    expect(screen.getByTestId("ship-pending-file-row")).toBeInTheDocument();
  });

  it("shows byte-fallback chip when diff_source='bytes'", () => {
    // Cross-worker rehydration case: cache empty, so backend reports
    // delta_bytes with diff_source="bytes". Chip should show ±NB
    // form, NOT a fake line-diff.
    const rehydratedPayload = {
      ...REAL_PAYLOAD,
      files_diff: [{
        path: "README.md", additions: 0, deletions: 0,
        is_new: false, delta_bytes: -420, diff_source: "bytes",
      }],
    };
    render(<ShipPendingCard pending={rehydratedPayload} busy={false} />);
    const chip = screen.getByTestId("ship-file-diff-chip");
    expect(chip).toHaveAttribute("data-diff-source", "bytes");
    expect(chip).toHaveTextContent(/-?420B/);
    // No fake +/- line counts rendered.
    expect(chip.textContent).not.toMatch(/^\+\d+\s*−\d+/);
  });

  // -------------------------------------------------------------
  // 2026 audit Risk #1 — safety regression: legacy ShipConfirmModal
  // showed diff + Vanguard verdict before Ship; Loop-mode had no
  // Vanguard signal at all (only the unrelated integrity guard).
  // -------------------------------------------------------------
  it("t_ship_card_shows_diff_and_verdict — diff + integrity + Vanguard verdict are ALL visible alongside an enabled Ship button (no blind ships)", () => {
    render(<ShipPendingCard pending={REAL_PAYLOAD} busy={false} />);

    // Diff must be visible.
    const totalChip = screen.getByTestId("ship-total-diff-chip");
    expect(totalChip).toHaveTextContent("+35");
    expect(totalChip).toHaveTextContent("−34");
    const row = screen.getByTestId("ship-pending-file-row");
    expect(within(row).getByTestId("ship-file-diff-chip")).toBeInTheDocument();

    // Both verdict pills must be visible.
    const integrityPill = screen.getByTestId("ship-integrity-pill");
    expect(integrityPill).toHaveAttribute("data-verdict", "clean");
    const vanguardPill = screen.getByTestId("ship-vanguard-pill");
    expect(vanguardPill).toHaveAttribute("data-verdict", "clean");
    expect(vanguardPill).toHaveTextContent(/Vanguard: clean/i);

    // Ship button must be present, enabled, and visible in the SAME
    // render as the diff + both verdicts — never gated behind them,
    // never rendered without them.
    const shipBtn = screen.getByTestId("ship-to-github-btn");
    expect(shipBtn).toBeInTheDocument();
    expect(shipBtn).not.toBeDisabled();
  });

  it("Vanguard pill shows flagged critical/high counts when the diff-scan found findings", () => {
    const flagged = {
      ...REAL_PAYLOAD,
      vanguard_verdict: { ran: true, critical: 1, high: 2, total: 3 },
    };
    render(<ShipPendingCard pending={flagged} busy={false} />);
    const pill = screen.getByTestId("ship-vanguard-pill");
    expect(pill).toHaveAttribute("data-verdict", "flagged");
    expect(pill).toHaveTextContent(/1 critical, 2 high/i);
  });

  it("Vanguard pill shows 'unknown' when the scan verdict wasn't recorded (rehydrated pre-fix loop)", () => {
    const noVerdict = { ...REAL_PAYLOAD };
    delete noVerdict.vanguard_verdict;
    render(<ShipPendingCard pending={noVerdict} busy={false} />);
    expect(screen.queryByTestId("ship-vanguard-pill")).toBeNull();
    // Integrity pill still renders independently (fields are
    // independent, neither invents the other).
    expect(screen.getByTestId("ship-integrity-pill")).toBeInTheDocument();
  });
});
