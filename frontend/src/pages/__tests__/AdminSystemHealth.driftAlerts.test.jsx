/**
 * AdminSystemHealth.driftAlerts.test.jsx — R1a gap#4 admin visibility
 * (2026-08-30). "Drift-Blocked Rollbacks" tile, read-only, next to
 * the Webhook Fence tile.
 *
 * Named tests:
 *   t_drift_alert_shows_count — 2 injected drift events -> tile shows 2
 *   t_drift_alert_empty       — 0 events -> shows "0", no expand summary
 *   t_drift_alert_expands     — click -> individual rows visible
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const get = vi.fn();
vi.mock("../../lib/api", () => ({ api: { get: (...args) => get(...args) } }));

import AdminSystemHealth from "../AdminSystemHealth.jsx";

const renderPage = () => render(<MemoryRouter><AdminSystemHealth /></MemoryRouter>);

function mockOtherEndpoints() {
  get.mockImplementation((path) => {
    if (path === "/admin/drift-alerts") return Promise.reject(new Error("override in test"));
    if (path === "/version") return Promise.resolve({ data: { commit_sha: "abc1234", environment: "preview", built_at: Date.now() } });
    return Promise.reject(new Error("not mocked in this test"));
  });
}

beforeEach(() => {
  get.mockReset();
  global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 599 });
});

describe("AdminSystemHealth — Drift-Blocked Rollbacks tile (R1a gap#4)", () => {
  it("t_drift_alert_shows_count: 2 injected drift events -> tile shows 2", async () => {
    mockOtherEndpoints();
    get.mockImplementation((path) => {
      if (path === "/admin/drift-alerts") {
        return Promise.resolve({ data: {
          count: 2,
          events: [
            { loop_id: "loop_a", branch: "main", expected_sha: "aaa1111111", current_sha: "bbb2222222", timestamp: "t1" },
            { loop_id: "loop_b", branch: "auremcto/ship-1", expected_sha: "ccc3333333", current_sha: "ddd4444444", timestamp: "t2" },
          ],
        } });
      }
      return Promise.reject(new Error("not mocked in this test"));
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("drift-alerts-count")).toBeInTheDocument());
    expect(screen.getByTestId("drift-alerts-count").textContent).toBe("2");
  });

  it("t_drift_alert_empty: 0 events -> shows 0, no expand summary", async () => {
    mockOtherEndpoints();
    get.mockImplementation((path) => {
      if (path === "/admin/drift-alerts") return Promise.resolve({ data: { count: 0, events: [] } });
      return Promise.reject(new Error("not mocked in this test"));
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("drift-alerts-count")).toBeInTheDocument());
    expect(screen.getByTestId("drift-alerts-count").textContent).toBe("0");
    expect(screen.queryByTestId("drift-alerts-expand")).toBeNull();
  });

  it("t_drift_alert_expands: click -> individual rows visible", async () => {
    mockOtherEndpoints();
    get.mockImplementation((path) => {
      if (path === "/admin/drift-alerts") {
        return Promise.resolve({ data: {
          count: 1,
          events: [
            { loop_id: "loop_drift_x", branch: "main", expected_sha: "d1234567890", current_sha: "e0987654321", timestamp: "2026-08-30T10:00:00Z" },
          ],
        } });
      }
      return Promise.reject(new Error("not mocked in this test"));
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("drift-alerts-expand")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("drift-alerts-expand"));
    const row = await screen.findByTestId("drift-alert-row-loop_drift_x");
    expect(row.textContent).toMatch(/loop_drift_x/);
    expect(row.textContent).toMatch(/main/);
    expect(row.textContent).toMatch(/d1234567890|d123456789/);
  });
});
