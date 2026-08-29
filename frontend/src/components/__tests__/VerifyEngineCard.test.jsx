/**
 * VerifyEngineCard.test.jsx — V1-dashboard (2026-08-30): the compact,
 * user-facing front for the V1 deploy-verify engine on the Deploy
 * panel. Locks in the 3 founder-required behaviors:
 *   t_dashboard_verify_card_renders    — pass rate + last state from summary data
 *   t_dashboard_verify_last_fail_shown — a failed verify shows its "what happened" line
 *   t_dashboard_verify_empty_state     — no verifies -> honest empty message
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));

import VerifyEngineCard from "../VerifyEngineCard.jsx";
import { api } from "../../lib/api";

beforeEach(() => { vi.clearAllMocks(); });

describe("V1-dashboard · VerifyEngineCard", () => {
  it("t_dashboard_verify_card_renders — pass rate + last-verified state from summary data", async () => {
    api.get.mockResolvedValue({
      data: {
        has_any: true, total: 15, passed: 14, pass_pct: 93,
        last_run_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
        last_fail_what_happened: null, last_fail_run_id: null,
      },
    });
    render(<VerifyEngineCard projectId="p1" verifying={false} refreshSignal={0} />);
    await waitFor(() => {
      expect(screen.getByTestId("deploy-verify-card")).toBeInTheDocument();
    });
    expect(screen.getByTestId("deploy-verify-pass-rate")).toHaveTextContent("14/15");
    expect(screen.getByTestId("deploy-verify-current-state")).toHaveTextContent("Last verified: 2h ago");
    expect(screen.queryByTestId("deploy-verify-last-fail")).toBeNull();
    expect(api.get).toHaveBeenCalledWith("/deploy/verify-summary", { params: { project_id: "p1" } });
  });

  it("shows a verifying spinner state while a run is mid-verification", async () => {
    api.get.mockResolvedValue({
      data: { has_any: true, total: 3, passed: 3, pass_pct: 100, last_run_at: new Date().toISOString() },
    });
    render(<VerifyEngineCard projectId="p1" verifying={true} refreshSignal={0} />);
    await waitFor(() => {
      expect(screen.getByTestId("deploy-verify-current-state")).toHaveTextContent("Verifying deployed site");
    });
  });

  it("t_dashboard_verify_last_fail_shown — a failed verify shows its what-happened line + evidence link", async () => {
    const onViewEvidence = vi.fn();
    api.get.mockResolvedValue({
      data: {
        has_any: true, total: 5, passed: 4, pass_pct: 80,
        last_run_at: new Date().toISOString(),
        last_fail_what_happened: "stale build detected on /pricing",
        last_fail_run_id: "run_abc123",
      },
    });
    render(<VerifyEngineCard projectId="p1" verifying={false} refreshSignal={0} onViewEvidence={onViewEvidence} />);
    await waitFor(() => {
      expect(screen.getByTestId("deploy-verify-last-fail")).toHaveTextContent("stale build detected on /pricing");
    });
    fireEvent.click(screen.getByTestId("deploy-verify-view-evidence-link"));
    expect(onViewEvidence).toHaveBeenCalledWith("run_abc123");
  });

  it("t_dashboard_verify_empty_state — no verifies yet -> honest empty message, no fake stats", async () => {
    api.get.mockResolvedValue({ data: { has_any: false, total: 0, passed: 0, pass_pct: null } });
    render(<VerifyEngineCard projectId="p1" verifying={false} refreshSignal={0} />);
    await waitFor(() => {
      expect(screen.getByTestId("deploy-verify-empty-state")).toHaveTextContent(
        "Your first deployment will be verified automatically.",
      );
    });
    expect(screen.queryByTestId("deploy-verify-pass-rate")).toBeNull();
  });

  it("renders nothing (not a broken card) if the summary fetch fails", async () => {
    api.get.mockRejectedValue(new Error("network"));
    const { container } = render(<VerifyEngineCard projectId="p1" verifying={false} refreshSignal={0} />);
    await waitFor(() => {
      expect(api.get).toHaveBeenCalled();
    });
    expect(container.firstChild).toBeNull();
  });
});
