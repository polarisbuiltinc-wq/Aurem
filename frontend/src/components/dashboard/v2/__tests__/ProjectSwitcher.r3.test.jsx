/**
 * ProjectSwitcher.r3.test.jsx — R3 (Repo Quick-Switch).
 *
 * Named tests (per the R3 spec):
 *   t_switch_repo_a_to_b            — clicking a valid project calls onSelect
 *   t_revoked_repo_non_selectable   — a project with a broken connection
 *                                     status renders disabled + the
 *                                     exact "repo unreachable" label and
 *                                     never fires onSelect
 *   t_login_landing_avoids_revoked  — if the saved active project is
 *                                     revoked, auto-switches to the next
 *                                     valid one + shows a notice toast
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const get = vi.fn();
vi.mock("../../../../lib/api", () => ({ api: { get: (...args) => get(...args) } }));

const toast = vi.fn();
vi.mock("../../../Toast", () => ({ toast: (...args) => toast(...args) }));

import { ProjectSwitcher } from "../ProjectSwitcher.jsx";

const PROJECTS = [
  { project_id: "p_a", github_owner: "acme", github_repo: "web", name: "web" },
  { project_id: "p_b", github_owner: "acme", github_repo: "api", name: "api" },
];

beforeEach(() => {
  get.mockReset();
  toast.mockReset();
});

describe("ProjectSwitcher — R3 Repo Quick-Switch", () => {
  it("t_switch_repo_a_to_b: selecting a valid, reachable project calls onSelect with its id", async () => {
    get.mockResolvedValue({ data: { statuses: [
      { project_id: "p_a", status: "connected" },
      { project_id: "p_b", status: "connected" },
    ] } });
    const onSelect = vi.fn();
    render(<ProjectSwitcher projects={PROJECTS} activeProjectId="p_a" onSelect={onSelect} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("project-switcher-trigger"));
    fireEvent.click(screen.getByTestId("project-switcher-item-p_b"));
    expect(onSelect).toHaveBeenCalledWith("p_b");
  });

  it("t_revoked_repo_non_selectable: a disconnected project is dimmed, labelled, and never selectable", async () => {
    get.mockResolvedValue({ data: { statuses: [
      { project_id: "p_a", status: "connected" },
      { project_id: "p_b", status: "disconnected" },
    ] } });
    const onSelect = vi.fn();
    render(<ProjectSwitcher projects={PROJECTS} activeProjectId="p_a" onSelect={onSelect} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("project-switcher-trigger"));
    const row = await screen.findByTestId("project-switcher-item-p_b");
    expect(row).toHaveAttribute("data-disabled", "true");
    expect(row.textContent).toMatch(/repo unreachable/i);
    fireEvent.click(row);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("t_login_landing_avoids_revoked: saved active project revoked -> auto-switches + notice shown", async () => {
    get.mockResolvedValue({ data: { statuses: [
      { project_id: "p_a", status: "disconnected" },
      { project_id: "p_b", status: "connected" },
    ] } });
    const onSelect = vi.fn();
    render(<ProjectSwitcher projects={PROJECTS} activeProjectId="p_a" onSelect={onSelect} />);
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("p_b"));
    expect(toast).toHaveBeenCalledTimes(1);
    expect(toast.mock.calls[0][0].message).toMatch(/unreachable — showing/i);
  });

  it("t_login_landing_noop_when_active_is_healthy: a reachable active project never triggers an auto-switch", async () => {
    get.mockResolvedValue({ data: { statuses: [
      { project_id: "p_a", status: "connected" },
      { project_id: "p_b", status: "connected" },
    ] } });
    const onSelect = vi.fn();
    render(<ProjectSwitcher projects={PROJECTS} activeProjectId="p_a" onSelect={onSelect} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 10));
    expect(onSelect).not.toHaveBeenCalled();
    expect(toast).not.toHaveBeenCalled();
  });
});
