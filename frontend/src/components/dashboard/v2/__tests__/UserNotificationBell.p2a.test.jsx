/**
 * UserNotificationBell.p2a.test.jsx — P2-A (2026-08-28).
 *
 * Named tests:
 *   t_bell_renders_and_counts             — unread count badge shows
 *                                           the real number from the API.
 *   t_bell_persistent_error_not_auto_cleared — a persistent item shows
 *                                           the warning icon and stays
 *                                           in the list (not silently
 *                                           dropped) until marked read.
 *   t_bell_mark_read                      — clicking an unread item
 *                                           calls the mark-read endpoint.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const get = vi.fn();
const post = vi.fn();
vi.mock("../../../../lib/api", () => ({ api: { get: (...a) => get(...a), post: (...a) => post(...a) } }));

import { UserNotificationBell } from "../UserNotificationBell.jsx";

const ITEMS = [
  { notif_id: "n1", type: "payment_failed", text: "Card declined.", persistent: true, read_at: null, created_at: Date.now() / 1000 },
  { notif_id: "n2", type: "scan_done", text: "Scan complete.", persistent: false, read_at: null, created_at: Date.now() / 1000 },
];

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

describe("UserNotificationBell — P2-A", () => {
  it("t_bell_renders_and_counts: shows the real unread count from the API", async () => {
    get.mockResolvedValue({ data: { items: ITEMS, unread_count: 2 } });
    render(<UserNotificationBell />);
    await waitFor(() => expect(screen.getByTestId("user-notification-bell-count")).toBeInTheDocument());
    expect(screen.getByTestId("user-notification-bell-count").textContent).toBe("2");
  });

  it("t_bell_persistent_error_not_auto_cleared: a persistent item stays listed with a warning marker", async () => {
    get.mockResolvedValue({ data: { items: ITEMS, unread_count: 2 } });
    render(<UserNotificationBell />);
    await waitFor(() => expect(screen.getByTestId("user-notification-bell-trigger")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("user-notification-bell-trigger"));
    const item = await screen.findByTestId("user-notification-item-n1");
    expect(item).toHaveAttribute("data-persistent", "true");
    expect(item).toHaveAttribute("data-unread", "true");
    expect(item.textContent).toMatch(/Card declined/);
  });

  it("t_bell_mark_read: clicking an unread item calls the mark-read endpoint", async () => {
    get.mockResolvedValue({ data: { items: ITEMS, unread_count: 2 } });
    post.mockResolvedValue({ data: { ok: true } });
    render(<UserNotificationBell />);
    fireEvent.click(await screen.findByTestId("user-notification-bell-trigger"));
    const item = await screen.findByTestId("user-notification-item-n2");
    fireEvent.click(item);
    await waitFor(() => expect(post).toHaveBeenCalledWith("/notifications/n2/read"));
  });
});
