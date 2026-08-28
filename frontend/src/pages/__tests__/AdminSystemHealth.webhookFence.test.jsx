/**
 * AdminSystemHealth.webhookFence.test.jsx — R5c "App Fence Tile".
 *
 * Named tests:
 *   t_fence_tile_renders_missing_subscription — shows the ⚠ missing
 *     "pull_request" badge when GitHub hasn't subscribed to it
 *   t_fence_tile_shows_failing_deliveries — expands to show each
 *     delivery's success/fail state from the real delivery log
 *   t_fence_tile_ok_state — a fully healthy fence renders OK, no
 *     missing-subscription badge
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const get = vi.fn();
vi.mock("../../lib/api", () => ({ api: { get: (...args) => get(...args) } }));

import AdminSystemHealth from "../AdminSystemHealth.jsx";

const renderPage = () => render(<MemoryRouter><AdminSystemHealth /></MemoryRouter>);

// Every other card's endpoint just needs to resolve so the page settles.
function mockOtherEndpoints() {
  get.mockImplementation((path) => {
    if (path === "/admin/github-webhook-fence") return Promise.reject(new Error("override in test"));
    if (path === "/version") return Promise.resolve({ data: { commit_sha: "abc1234", environment: "preview", built_at: Date.now() } });
    return Promise.reject(new Error("not mocked in this test"));
  });
}

beforeEach(() => {
  get.mockReset();
  global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 599 });
});

describe("AdminSystemHealth — GitHub Webhook Fence tile (R5c)", () => {
  it("t_fence_tile_renders_missing_subscription: shows the missing pull_request badge", async () => {
    mockOtherEndpoints();
    get.mockImplementation((path) => {
      if (path === "/admin/github-webhook-fence") {
        return Promise.resolve({ data: {
          ok: false, configured: true,
          subscribed_events: [],
          missing_subscriptions: ["pull_request"],
          recent_deliveries: [
            { id: 1, event: "installation", action: "created", delivered_at: "t1", status_code: 401, success: false },
          ],
          failing_count: 1,
        } });
      }
      return Promise.reject(new Error("not mocked in this test"));
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("webhook-fence-missing")).toBeInTheDocument());
    expect(screen.getByTestId("webhook-fence-missing").textContent).toMatch(/pull_request/);
  });

  it("t_fence_tile_shows_failing_deliveries: expanding reveals each delivery's real success/fail state", async () => {
    mockOtherEndpoints();
    get.mockImplementation((path) => {
      if (path === "/admin/github-webhook-fence") {
        return Promise.resolve({ data: {
          ok: false, configured: true,
          subscribed_events: [],
          missing_subscriptions: ["pull_request"],
          recent_deliveries: [
            { id: 42, event: "pull_request", action: "closed", delivered_at: "t1", status_code: 401, success: false },
          ],
          failing_count: 1,
        } });
      }
      return Promise.reject(new Error("not mocked in this test"));
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("webhook-fence-deliveries-expand")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("webhook-fence-deliveries-expand"));
    const row = await screen.findByTestId("webhook-delivery-42");
    expect(row.textContent).toMatch(/FAIL 401/);
  });

  it("t_fence_tile_ok_state: a fully healthy fence shows no missing-subscription badge", async () => {
    mockOtherEndpoints();
    get.mockImplementation((path) => {
      if (path === "/admin/github-webhook-fence") {
        return Promise.resolve({ data: {
          ok: true, configured: true,
          subscribed_events: ["pull_request"],
          missing_subscriptions: [],
          recent_deliveries: [
            { id: 7, event: "pull_request", action: "opened", delivered_at: "t1", status_code: 200, success: true },
          ],
          failing_count: 0,
        } });
      }
      return Promise.reject(new Error("not mocked in this test"));
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("card-webhook-fence")).toBeInTheDocument());
    expect(screen.queryByTestId("webhook-fence-missing")).toBeNull();
  });
});
