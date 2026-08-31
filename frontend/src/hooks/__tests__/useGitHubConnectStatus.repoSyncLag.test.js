/**
 * useGitHubConnectStatus.repoSyncLag.test.js — Connect-flow
 * investigation fix (2026-09-01), Bug-2 (Mike Froedge) / Bug-3
 * (Michael Pelletier) — confirmed shared root: `installation_active`
 * (GitHub confirmed the grant) was being treated the same as "not
 * connected yet" whenever the popup closed, even though real
 * production installs can have `installation_active: true` with an
 * empty repo list for well over an hour. That flipped a genuine
 * success into a false "denied".
 */
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn() },
  API_BASE: "https://api.test",
  getToken: () => "fake-token",
}));
vi.mock("../../lib/githubFunnel", () => ({ trackFunnel: vi.fn() }));

import useGitHubConnectStatus from "../useGitHubConnectStatus.js";
import { api } from "../../lib/api";
import { trackFunnel } from "../../lib/githubFunnel";

function fakePopup() {
  return { closed: false, close: vi.fn() };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});
afterEach(() => { vi.useRealTimers(); });

describe("Bug-2/Bug-3 shared root — installation_active vs state===connected", () => {
  it("t_no_false_denied_when_installation_active_but_repos_still_empty", async () => {
    const popup = fakePopup();
    vi.spyOn(window, "open").mockReturnValue(popup);
    // First poll tick: install succeeded, but GitHub's repo list is
    // still empty (real production case — can last well over an
    // hour). The popup already auto-closed (bridge closes in 400ms).
    api.get.mockResolvedValue({
      data: { installation_active: true, state: "pending",
              installations: [{ installation_id: 1, repositories: [] }] },
    });

    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); }); // initial mount fetch

    act(() => { result.current.startConnect(); });
    popup.closed = true; // bridge already closed the popup

    await act(async () => {
      vi.advanceTimersByTime(2500);
      await Promise.resolve();
    });

    expect(result.current.denied).toBe(false);
    expect(result.current.connecting).toBe(false);
    expect(trackFunnel).toHaveBeenCalledWith("app_install_granted", "wizard");
    expect(trackFunnel).not.toHaveBeenCalledWith(
      "app_install_denied", "wizard", expect.anything(),
    );
  });

  it("t_denied_still_fires_when_truly_no_installation", async () => {
    const popup = fakePopup();
    vi.spyOn(window, "open").mockReturnValue(popup);
    api.get.mockResolvedValue({
      data: { installation_active: false, state: "pending", installations: [] },
    });

    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); });

    act(() => { result.current.startConnect(); });
    popup.closed = true; // user genuinely closed without installing

    await act(async () => {
      vi.advanceTimersByTime(2500);
      await Promise.resolve();
    });

    expect(result.current.denied).toBe(true);
    expect(trackFunnel).toHaveBeenCalledWith(
      "app_install_denied", "wizard", { reason: "popup_closed" },
    );
  });

  it("t_background_sync_keeps_polling_until_repos_populate_then_stops", async () => {
    const popup = fakePopup();
    vi.spyOn(window, "open").mockReturnValue(popup);
    let call = 0;
    api.get.mockImplementation(async () => {
      call += 1;
      if (call <= 2) {
        return { data: { installation_active: true, state: "pending", installations: [] } };
      }
      return { data: { installation_active: true, state: "connected", installations: [] } };
    });

    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); }); // call 1 (initial mount)

    act(() => { result.current.startConnect(); });
    await act(async () => {
      vi.advanceTimersByTime(2500); // poll tick -> call 2, still pending -> starts bg sync
      await Promise.resolve();
    });
    expect(result.current.status.state).toBe("pending");

    await act(async () => {
      vi.advanceTimersByTime(5000); // bg sync tick -> call 3, now connected
      await Promise.resolve();
    });
    expect(result.current.status.state).toBe("connected");

    const callsAfterConnected = call;
    await act(async () => {
      vi.advanceTimersByTime(20_000); // bg sync should have stopped
      await Promise.resolve();
    });
    expect(call).toBe(callsAfterConnected);
  });
});
