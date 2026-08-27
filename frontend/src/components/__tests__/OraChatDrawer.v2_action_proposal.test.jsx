/**
 * OraChatDrawer.v2_action_proposal.test.jsx — 2026-08-27
 *
 * ORA Chat v2 rebuild (P1-P5 checkpoint): the drawer must render an
 * inline action-proposal card for the new `action_proposal` SSE event
 * and let the founder approve/reject it via
 * `/ora-chat/action/approve|reject` — never auto-execute.
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

describe("OraChatDrawer — ORA Chat v2 action proposal card", () => {
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
      if (path === "/ora-chat/action/approve") {
        return Promise.resolve({ data: { ok: true, result: { ok: true } } });
      }
      return Promise.resolve({ data: { ok: true } });
    });
    global.fetch = vi.fn().mockResolvedValue(fakeStreamResponse([
      ["state", { type: "state", state_as_of: "2026-08-27T00:00:00Z" }],
      ["delta", { type: "delta", content: "I can create a backlog item for this." }],
      ["action_proposal", { type: "action_proposal", proposal_id: "p1",
                             action_id: "create_backlog_item", risk: "reversible",
                             summary: "Create backlog item: Ship the thing",
                             params: { title: "Ship the thing" } }],
      ["final", { type: "final", content: "I can create a backlog item for this.",
                   tokens_in: 100, tokens_out: 20, proposal_id: "p1" }],
    ]));
  });

  afterEach(() => { vi.restoreAllMocks(); });

  it("renders the state chip + action proposal card with approve/reject buttons", async () => {
    render(<OraChatDrawer forceOpen />);
    await waitFor(() => expect(screen.getByTestId("ora-chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("ora-chat-input"), { target: { value: "fix the backlog" } });
    fireEvent.click(screen.getByTestId("ora-chat-send"));

    await waitFor(() => expect(screen.getByTestId("ora-action-proposal-card")).toBeInTheDocument());
    expect(screen.getByTestId("ora-action-approve-btn")).toBeInTheDocument();
    expect(screen.getByTestId("ora-action-reject-btn")).toBeInTheDocument();
  });

  it("approving calls /ora-chat/action/approve and shows resolved state", async () => {
    render(<OraChatDrawer forceOpen />);
    await waitFor(() => expect(screen.getByTestId("ora-chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("ora-chat-input"), { target: { value: "fix the backlog" } });
    fireEvent.click(screen.getByTestId("ora-chat-send"));

    await waitFor(() => expect(screen.getByTestId("ora-action-approve-btn")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("ora-action-approve-btn"));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/ora-chat/action/approve", { proposal_id: "p1" }));
    await waitFor(() => expect(screen.getByTestId("ora-action-resolved")).toBeInTheDocument());
    expect(screen.getByTestId("ora-action-resolved").textContent).toContain("Approved and executed");
  });

  it("think mode and advise-only toggles are present and wired into the request body", async () => {
    render(<OraChatDrawer forceOpen />);
    await waitFor(() => expect(screen.getByTestId("ora-chat-think-toggle")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("ora-chat-advise-only-toggle").querySelector("input"));
    fireEvent.change(screen.getByTestId("ora-chat-input"), { target: { value: "what should I do?" } });
    fireEvent.click(screen.getByTestId("ora-chat-send"));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.advise_only).toBe(true);
    expect(body.think_mode).toBe(false);
  });

  it("shows per-message token in/out on the assistant bubble after a turn completes", async () => {
    render(<OraChatDrawer forceOpen />);
    await waitFor(() => expect(screen.getByTestId("ora-chat-input")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("ora-chat-input"), { target: { value: "hi" } });
    fireEvent.click(screen.getByTestId("ora-chat-send"));

    await waitFor(() => expect(screen.getByTestId("ora-chat-msg-tokens")).toBeInTheDocument());
    expect(screen.getByTestId("ora-chat-msg-tokens").textContent).toContain("100 in");
    expect(screen.getByTestId("ora-chat-msg-tokens").textContent).toContain("20 out");
  });
});
