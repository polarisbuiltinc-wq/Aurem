/**
 * OraChatDrawer.browser_unavailable_badge.test.jsx — 2026-08-30
 *
 * KIT A / C1 — when a `tool_result` SSE event for web_verify/web_inspect
 * comes back with `browser_available: false` (C1's graceful Chromium-
 * missing degrade), the drawer must show an explicit "browser
 * unavailable" badge on that assistant turn — never a silent skip that
 * makes the founder assume the check ran normally.
 *
 * t_degraded_result_shows_browser_unavailable_badge
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { api } from "../../lib/api";
import OraChatDrawer from "../OraChatDrawer.jsx";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  getToken: () => "fake-token",
}));

function sseChunk(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function fakeStreamResponse(events) {
  const text = events.map(([e, d]) => sseChunk(e, d)).join("");
  const enc = new TextEncoder();
  const bytes = enc.encode(text);
  let sent = false;
  return {
    ok: true, status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { value: undefined, done: true };
          sent = true;
          return { value: bytes, done: false };
        },
      }),
    },
  };
}

describe("OraChatDrawer — C1 browser-unavailable badge", () => {
  beforeEach(() => {
    api.get.mockImplementation((path) => {
      if (path === "/ora-chat/sessions") return Promise.resolve({ data: { sessions: [] } });
      if (path === "/ora-chat/usage") return Promise.resolve({ data: { budget: null } });
      return Promise.resolve({ data: {} });
    });
    api.post.mockImplementation((path) => {
      if (path === "/ora-chat/sessions") {
        return Promise.resolve({ data: { session: { session_id: "s1" } } });
      }
      return Promise.resolve({ data: { ok: true } });
    });
  });

  afterEach(() => { vi.restoreAllMocks(); });

  it("t_degraded_result_shows_browser_unavailable_badge — shows the badge when a tool_result degrades", async () => {
    global.fetch = vi.fn().mockResolvedValue(fakeStreamResponse([
      ["tool_call", { type: "tool_call", name: "web_verify", args: { url: "https://auremcto.com" } }],
      ["tool_result", { type: "tool_result", name: "web_verify",
                         summary: "{'verdict': 'degraded', 'browser_available': False}",
                         browser_available: false }],
      ["delta", { type: "delta", content: "Chromium isn't installed here, so I ran a text-only check." }],
      ["final", { type: "final", content: "Chromium isn't installed here, so I ran a text-only check.",
                   tokens_in: 50, tokens_out: 10 }],
    ]));

    render(<OraChatDrawer forceOpen />);
    await waitFor(() => expect(screen.getByTestId("ora-chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("ora-chat-input"), { target: { value: "verify auremcto.com" } });
    fireEvent.click(screen.getByTestId("ora-chat-send"));

    await waitFor(() => expect(screen.getByTestId("ora-browser-unavailable-badge")).toBeInTheDocument());
    expect(screen.getByTestId("ora-browser-unavailable-badge").textContent)
      .toContain("Browser unavailable");
  });

  it("does NOT show the badge on a normal (non-degraded) tool result", async () => {
    global.fetch = vi.fn().mockResolvedValue(fakeStreamResponse([
      ["tool_call", { type: "tool_call", name: "web_verify", args: { url: "https://auremcto.com" } }],
      ["tool_result", { type: "tool_result", name: "web_verify",
                         summary: "{'verdict': 'pass', 'browser_available': True}",
                         browser_available: true }],
      ["delta", { type: "delta", content: "Homepage is up and passes all checks." }],
      ["final", { type: "final", content: "Homepage is up and passes all checks.",
                   tokens_in: 50, tokens_out: 10 }],
    ]));

    render(<OraChatDrawer forceOpen />);
    await waitFor(() => expect(screen.getByTestId("ora-chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("ora-chat-input"), { target: { value: "verify auremcto.com" } });
    fireEvent.click(screen.getByTestId("ora-chat-send"));

    await waitFor(() => expect(screen.getByTestId("ora-chat-msg-tokens")).toBeInTheDocument());
    expect(screen.queryByTestId("ora-browser-unavailable-badge")).not.toBeInTheDocument();
  });
});
