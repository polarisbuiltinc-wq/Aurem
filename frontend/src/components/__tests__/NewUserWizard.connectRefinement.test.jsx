/**
 * NewUserWizard.connectRefinement.test.jsx — connect-flow refinement
 * (2026-09-02), on top of the shipped Michael/Mike repo-picker fix.
 *
 * D3 — standard SaaS pattern (GitHub/Linear/Notion/Slack): a short
 * "Connected ✓" success beat (~1s), then auto-land — no 12s blocking
 * interstitial, no silent instant vanish either.
 *   t_connect_shows_success_beat_then_lands
 *   t_no_12s_blocking_interstitial
 *
 * D2 — an already-connected repo redirects to the existing project
 * instead of dead-ending.
 *   t_already_added_shows_open_existing_project
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  getToken: () => "fake-token",
  API_BASE: "https://api.test",
}));
vi.mock("../../lib/githubFunnel", () => ({
  trackFunnel: vi.fn(),
  withFunnelParams: (url) => url,
  getFunnelSessionId: () => "sess-1",
}));
const mockSetActiveProjectId = vi.fn();
vi.mock("../TabBar", () => ({ setActiveProjectId: (...args) => mockSetActiveProjectId(...args) }));

const mockUseGitHubConnectStatus = vi.fn();
vi.mock("../../hooks/useGitHubConnectStatus", () => ({
  default: () => mockUseGitHubConnectStatus(),
}));

import NewUserWizard from "../NewUserWizard.jsx";
import { api } from "../../lib/api";

const HOOK_STATUS = {
  installation_active: true,
  installations: [{
    installation_id: 1,
    github_login: "octo",
    repositories: [{ full_name: "octo/mine", default_branch: "main" }],
  }],
  connected_repo: "octo/mine",
  repos: [{ full_name: "octo/mine", default_branch: "main" }],
  state: "connected",
};

function setupHook() {
  mockUseGitHubConnectStatus.mockReturnValue({
    status: HOOK_STATUS,
    connecting: false,
    timedOut: false,
    denied: false,
    startConnect: vi.fn(),
    retry: vi.fn(),
    refresh: vi.fn().mockResolvedValue(HOOK_STATUS),
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  setupHook();
  api.get.mockResolvedValue({ data: { connected: false } });
  localStorage.clear();
});
afterEach(() => { vi.useRealTimers(); });

async function mountAndSelectRepo() {
  const onComplete = vi.fn();
  render(<NewUserWizard onComplete={onComplete} />);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  await waitFor(() => expect(screen.getByTestId("wizard-next")).toBeTruthy());
  return onComplete;
}

describe("D3 — connect success beat, not a 12s block", () => {
  it("t_connect_shows_success_beat_then_lands", async () => {
    api.post.mockResolvedValue({ data: { project_id: "px1" } });
    const onComplete = await mountAndSelectRepo();

    fireEvent.click(screen.getByTestId("wizard-next"));
    await act(async () => { await Promise.resolve(); });

    // Beat shows immediately — project already lands (active project
    // set) while the beat is visible.
    expect(screen.getByTestId("wizard-success-beat")).toBeTruthy();
    expect(mockSetActiveProjectId).toHaveBeenCalledWith("px1");
    expect(onComplete).not.toHaveBeenCalled();

    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("t_no_12s_blocking_interstitial", async () => {
    api.post.mockResolvedValue({ data: { project_id: "px2" } });
    const onComplete = await mountAndSelectRepo();

    fireEvent.click(screen.getByTestId("wizard-next"));
    await act(async () => { await Promise.resolve(); });

    // Well under the old 12s hold — the standard beat lands fast.
    await act(async () => { vi.advanceTimersByTime(1500); });
    expect(onComplete).toHaveBeenCalledTimes(1);
  });
});

describe("D2 — already-connected redirect", () => {
  it("t_already_added_shows_open_existing_project", async () => {
    api.post.mockRejectedValue({
      response: { data: { detail: {
        error: "already_connected",
        message: "'octo/mine' is already your project 'mine'.",
        project_id: "p_existing9",
        project_name: "mine",
      } } },
    });
    const onComplete = await mountAndSelectRepo();

    fireEvent.click(screen.getByTestId("wizard-next"));
    const panel = await screen.findByTestId("wizard-already-connected");
    expect(panel.textContent).toMatch(/already your project/i);
    expect(panel.textContent).toMatch(/mine/);
    expect(screen.queryByTestId("wizard-success-beat")).toBeNull();

    fireEvent.click(screen.getByTestId("wizard-open-existing-project-btn"));
    expect(mockSetActiveProjectId).toHaveBeenCalledWith("p_existing9");
    expect(onComplete).toHaveBeenCalledTimes(1);
  });
});
