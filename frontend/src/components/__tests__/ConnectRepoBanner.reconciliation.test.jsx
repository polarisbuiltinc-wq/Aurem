/**
 * ConnectRepoBanner.reconciliation.test.jsx — Connect-flow
 * investigation fix (2026-09-01), Bug-3 (Michael Pelletier) / Bug-2
 * (Mike Froedge) — confirmed DIFFERENT sub-causes, each with its own
 * regression test:
 *   t_install_with_repos_no_project_shows_repo_picker (Michael-class:
 *     install active + real repos + 0 projects -> banner offers a
 *     repo picker that creates the project directly, not "connect
 *     again").
 *   t_install_with_0_repos_shows_select_not_denied (Mike-class:
 *     install active + 0 repos -> banner says "select your repos",
 *     never "denied").
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(() => Promise.resolve({ data: { remaining: 10, total: 500 } })), post: vi.fn() },
}));
vi.mock("../../lib/githubFunnel", () => ({
  trackFunnel: vi.fn(),
  getFunnelSessionId: () => "sess-1",
}));

const mockUseGitHubConnectStatus = vi.fn();
vi.mock("../../hooks/useGitHubConnectStatus", () => ({
  default: () => mockUseGitHubConnectStatus(),
}));

import ConnectRepoBanner from "../ConnectRepoBanner.jsx";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ data: { remaining: 10, total: 500 } });
});

describe("Bug-3/Bug-2 — confirmed different sub-causes, each fixed + tested", () => {
  it("t_install_with_repos_no_project_shows_repo_picker (Michael-class)", async () => {
    mockUseGitHubConnectStatus.mockReturnValue({
      status: {
        installation_active: true,
        installations: [{
          installation_id: 157944565,
          repositories: [
            { full_name: "mpelletier0691-byte/forseti", default_branch: "main" },
            { full_name: "mpelletier0691-byte/BrokkrForge-", default_branch: "main" },
          ],
        }],
      },
    });
    api.post.mockResolvedValue({ data: { project_id: "p123" } });
    const onProjectCreated = vi.fn();

    render(<ConnectRepoBanner onConnect={vi.fn()} onProjectCreated={onProjectCreated} />);
    await waitFor(() => expect(api.get).toHaveBeenCalled());

    // Offers the repo picker, NOT the "connect again" button.
    expect(screen.queryByTestId("connect-repo-banner-cta")).toBeNull();
    const repoBtn = screen.getByTestId(
      "connect-repo-banner-repo-mpelletier0691-byte/forseti",
    );
    expect(repoBtn).toBeTruthy();

    fireEvent.click(repoBtn);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/cto/projects/add",
      expect.objectContaining({
        github_url: "https://github.com/mpelletier0691-byte/forseti",
        installation_id: 157944565,
      }),
    ));
    // 2026-09-02 — D3 refinement: a short "Connected ✓" beat now
    // shows for ~1s before onProjectCreated fires (standard SaaS
    // pattern), instead of an instant silent hand-off.
    expect(screen.getByTestId("connect-repo-banner-success-beat")).toBeTruthy();
    await waitFor(
      () => expect(onProjectCreated).toHaveBeenCalledWith("p123"),
      { timeout: 2000 },
    );
  });

  it("t_install_with_0_repos_shows_select_not_denied (Mike-class)", async () => {
    mockUseGitHubConnectStatus.mockReturnValue({
      status: {
        installation_active: true,
        installations: [{ installation_id: 157839994, repositories: [] }],
      },
    });

    render(<ConnectRepoBanner onConnect={vi.fn()} onProjectCreated={vi.fn()} />);
    await waitFor(() => expect(api.get).toHaveBeenCalled());

    const cta = screen.getByTestId("connect-repo-banner-cta");
    expect(cta.textContent).toMatch(/select your repos/i);
    expect(cta.textContent.toLowerCase()).not.toContain("denied");

    const steps = screen.getByTestId("connect-repo-banner-steps");
    expect(steps.textContent.toLowerCase()).toContain("haven't selected any repos yet");
    expect(steps.textContent.toLowerCase()).not.toContain("denied");
  });

  it("shows the plain connect CTA when there's no install at all yet", async () => {
    mockUseGitHubConnectStatus.mockReturnValue({
      status: { installation_active: false, installations: [] },
    });
    render(<ConnectRepoBanner onConnect={vi.fn()} onProjectCreated={vi.fn()} />);
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(screen.getByTestId("connect-repo-banner-cta").textContent).toMatch(/connect repo/i);
  });

  it("t_already_added_shows_open_existing_project", async () => {
    mockUseGitHubConnectStatus.mockReturnValue({
      status: {
        installation_active: true,
        installations: [{
          installation_id: 157944565,
          repositories: [
            { full_name: "mpelletier0691-byte/forseti", default_branch: "main" },
          ],
        }],
      },
    });
    api.post.mockRejectedValue({
      response: { data: { detail: {
        error: "already_connected",
        message: "'mpelletier0691-byte/forseti' is already your project 'forseti'.",
        project_id: "p_existing1",
        project_name: "forseti",
      } } },
    });
    const onProjectCreated = vi.fn();

    render(<ConnectRepoBanner onConnect={vi.fn()} onProjectCreated={onProjectCreated} />);
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("connect-repo-banner-repo-mpelletier0691-byte/forseti"));

    const panel = await screen.findByTestId("connect-repo-banner-already-connected");
    expect(panel.textContent).toMatch(/already your project/i);
    expect(panel.textContent).toMatch(/forseti/);

    fireEvent.click(screen.getByTestId("connect-repo-banner-open-existing-btn"));
    expect(onProjectCreated).toHaveBeenCalledWith("p_existing1");
  });
});
