/**
 * AdminSystemHealth.mockFlagVisibility.test.jsx — R9 MOCK_LLM
 * investigation follow-up (STEP 4, 2026-08-30). "Live Model Mode"
 * tile must surface the boot-cached MOCK_LLM value + flag a
 * restart-pending mismatch, so a founder's env edit is never
 * silently invisible.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const get = vi.fn();
vi.mock("../../lib/api", () => ({ api: { get: (...args) => get(...args) } }));

import AdminSystemHealth from "../AdminSystemHealth.jsx";

const renderPage = () => render(<MemoryRouter><AdminSystemHealth /></MemoryRouter>);

function mockOtherEndpoints() {
  get.mockImplementation((path) => {
    if (path === "/admin/live-model-mode") return Promise.reject(new Error("override in test"));
    if (path === "/version") return Promise.resolve({ data: { commit_sha: "abc1234", environment: "preview", built_at: Date.now() } });
    return Promise.reject(new Error("not mocked in this test"));
  });
}

beforeEach(() => {
  get.mockReset();
  global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 599 });
});

describe("AdminSystemHealth — Live Model Mode boot-cached visibility", () => {
  it("shows the boot-cached note always, and no restart-pending warning when values match", async () => {
    mockOtherEndpoints();
    get.mockImplementation((path) => {
      if (path === "/admin/live-model-mode") {
        return Promise.resolve({ data: {
          mode: "mock", mock_detected_in_live_24h: 0, recent_mock_events: [],
          mock_flag_boot_cached: true, mock_boot_value: true, mock_current_env_value: true,
          restart_pending: false,
        } });
      }
      return Promise.reject(new Error("not mocked in this test"));
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("mock-flag-boot-cached-note")).toBeInTheDocument());
    expect(screen.queryByTestId("mock-flag-restart-pending")).toBeNull();
  });

  it("shows a restart-pending warning when the env was edited but the process hasn't restarted", async () => {
    mockOtherEndpoints();
    get.mockImplementation((path) => {
      if (path === "/admin/live-model-mode") {
        return Promise.resolve({ data: {
          mode: "mock", mock_detected_in_live_24h: 3, recent_mock_events: [],
          mock_flag_boot_cached: true, mock_boot_value: true, mock_current_env_value: false,
          restart_pending: true,
        } });
      }
      return Promise.reject(new Error("not mocked in this test"));
    });
    renderPage();
    const warn = await screen.findByTestId("mock-flag-restart-pending");
    expect(warn.textContent).toMatch(/restart pending/i);
    expect(warn.textContent).toMatch(/MOCK/);
  });
});
