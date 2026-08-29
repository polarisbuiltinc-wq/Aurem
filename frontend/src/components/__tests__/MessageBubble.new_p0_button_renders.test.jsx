/**
 * MessageBubble.new_p0_button_renders.test.jsx — 2026-08-28, NEW P0
 * Task 1a — "t_button_renders_from_valid_fence".
 *
 * Founder-required deterministic proof (model-independent): the
 * Approve-the-fix button (ShipDialog's `ship-cto-btn-{idx}`) MUST
 * render whenever the message content carries a valid
 * ```aurem-handoff fence that passes extractHandoffBrief()'s 7
 * gates, canShip's preconditions are met (activeProject set, not
 * exhausted), and the turn hasn't already shipped. This is a UI
 * "renders from a valid signal" invariant, entirely separate from
 * whether the MODEL reliably emits that signal — proven here with
 * two different `m.provider` values to show the render path never
 * inspects which model wrote the message.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import MessageBubble from "../MessageBubble.jsx";

vi.mock("../../lib/api", () => ({
  api: {
    get: vi.fn(() => Promise.resolve({ data: {} })),
    post: vi.fn(() => Promise.resolve({ data: {} })),
  },
}));
vi.mock("../Toast", () => ({ toast: vi.fn() }));

const VALID_FENCE_CONTENT = [
  "I found the bug and can fix it now.",
  "",
  "```aurem-handoff",
  "Fix the null check in frontend/src/pages/Signup.jsx line 42.",
  "```",
].join("\n");

const ACTIVE_PROJECT = {
  project_id: "proj_1",
  github_owner: "acme",
  github_repo: "widgets",
  branch: "main",
};

function makeMessage(provider) {
  return {
    role: "assistant",
    content: VALID_FENCE_CONTENT,
    provider,
    streaming: false,
  };
}

describe("NEW P0 Task 1a — Approve button renders from a valid injected fence", () => {
  it("t_button_renders_from_valid_fence: renders with a mock-provider message", () => {
    render(
      <MemoryRouter>
        <MessageBubble
          idx={0}
          m={makeMessage("mock")}
          sessionId="sess-1"
          activeProject={ACTIVE_PROJECT}
          exhausted={false}
        />
      </MemoryRouter>,
    );
    const btn = screen.getByTestId("ship-cto-btn-0");
    expect(btn).toBeInTheDocument();
    expect(btn.textContent).toMatch(/Approve the fix/i);
    // The fallback "button didn't load" banner must NOT also render.
    expect(screen.queryByTestId("ship-cta-fallback-0")).toBeNull();
  });

  it("t_button_renders_from_valid_fence: renders identically with a different (real-model-shaped) provider value — the render path is model-independent", () => {
    render(
      <MemoryRouter>
        <MessageBubble
          idx={1}
          m={makeMessage("claude-sonnet-5")}
          sessionId="sess-1"
          activeProject={ACTIVE_PROJECT}
          exhausted={false}
        />
      </MemoryRouter>,
    );
    const btn = screen.getByTestId("ship-cto-btn-1");
    expect(btn).toBeInTheDocument();
    expect(btn.textContent).toMatch(/Approve the fix/i);
  });

  it("does NOT render the button when no project is connected (canShip false) — but still acknowledges the valid fence with the disabled hint, not the fallback banner", () => {
    render(
      <MemoryRouter>
        <MessageBubble
          idx={2}
          m={makeMessage("mock")}
          sessionId="sess-1"
          activeProject={null}
          exhausted={false}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("ship-cto-btn-2")).toBeNull();
    expect(screen.getByTestId("ship-cto-row-2").textContent).toMatch(
      /connected project/i,
    );
  });

  it("t_honest_fallback_is_a_real_action (Task 1d): when the fence attempt fails, the fallback renders a WORKING retry button that actually calls onRetryFix — not just an instruction to retype", () => {
    const onRetryFix = vi.fn();
    const proseNoFence = "I found the bug. Click the Approve the fix button to commit it.";
    render(
      <MemoryRouter>
        <MessageBubble
          idx={3}
          m={{ role: "assistant", content: proseNoFence, provider: "mock", streaming: false }}
          sessionId="sess-1"
          activeProject={ACTIVE_PROJECT}
          exhausted={false}
          onRetryFix={onRetryFix}
        />
      </MemoryRouter>,
    );
    const retryBtn = screen.getByTestId("ship-cta-fallback-retry-3");
    expect(retryBtn).toBeInTheDocument();
    retryBtn.click();
    expect(onRetryFix).toHaveBeenCalledTimes(1);
  });
});
