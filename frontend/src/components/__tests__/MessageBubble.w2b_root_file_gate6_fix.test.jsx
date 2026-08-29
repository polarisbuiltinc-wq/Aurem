/**
 * MessageBubble.w2b_root_file_gate6_fix.test.jsx — Overnight loop
 * W2-B (2026-08-29).
 *
 * Decisive zero-LLM diagnostic (see /app/e2e-proof/W2-diag/run1.log)
 * traced the backend end-to-end for the founder's exact repro shape
 * (a real ```aurem-handoff fence targeting a REPO-ROOT file, e.g.
 * README.md) and proved the backend always emits real, non-empty,
 * well-formed content. The actual bug is here: `FILE_PATH_TOKEN`
 * used to mandate a '/' in the path, so a fence whose ONLY file
 * reference is a root file (no directory prefix) could never pass
 * extractHandoffBrief()'s Gate 6 — the Approve button never
 * rendered for ANY fix confined to a root-level file. This is the
 * exact repro pattern used throughout this codebase's own test
 * fixtures ("fix the README license line").
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import MessageBubble from "../MessageBubble.jsx";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn(() => Promise.resolve({ data: {} })) },
}));
vi.mock("../Toast", () => ({ toast: vi.fn() }));

const ROOT_FILE_FENCE_CONTENT = [
  "Root cause: the README is missing a license line.",
  "",
  "```aurem-handoff",
  "In README.md add a license line at the bottom.",
  "```",
].join("\n");

const ACTIVE_PROJECT = {
  project_id: "proj_1", github_owner: "acme", github_repo: "widgets", branch: "main",
};

describe("W2-B — Approve button renders for a fence targeting a repo-ROOT file (the fix)", () => {
  it("t_button_renders_for_root_level_file_fence: README.md (no slash) now passes Gate 6", () => {
    render(
      <MemoryRouter>
        <MessageBubble
          idx={0}
          m={{ role: "assistant", content: ROOT_FILE_FENCE_CONTENT, provider: "mock", streaming: false }}
          sessionId="sess-1"
          activeProject={ACTIVE_PROJECT}
          exhausted={false}
        />
      </MemoryRouter>,
    );
    const btn = screen.getByTestId("ship-cto-btn-0");
    expect(btn).toBeInTheDocument();
    expect(btn.textContent).toMatch(/Approve the fix/i);
    // The honest "didn't load" fallback must NOT ALSO fire once the
    // real button renders.
    expect(screen.queryByTestId("ship-cta-fallback-0")).toBeNull();
  });

  it("t_nested_path_fence_still_renders_no_regression: a nested path (has a slash) still works exactly as before", () => {
    const nested = [
      "Root cause: null check missing.",
      "```aurem-handoff",
      "Fix the null check in frontend/src/pages/Signup.jsx line 42.",
      "```",
    ].join("\n");
    render(
      <MemoryRouter>
        <MessageBubble
          idx={1}
          m={{ role: "assistant", content: nested, provider: "mock", streaming: false }}
          sessionId="sess-1"
          activeProject={ACTIVE_PROJECT}
          exhausted={false}
        />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("ship-cto-btn-1")).toBeInTheDocument();
  });

  it("t_generic_prose_without_a_real_file_token_still_rejected: a bare word that happens to contain a dot is NOT a false positive", () => {
    const noRealFile = [
      "Root cause: something is off.",
      "```aurem-handoff",
      "Update the config. It handles version 1.2 of the api. No file needed here at all today.",
      "```",
    ].join("\n");
    render(
      <MemoryRouter>
        <MessageBubble
          idx={2}
          m={{ role: "assistant", content: noRealFile, provider: "mock", streaming: false }}
          sessionId="sess-1"
          activeProject={ACTIVE_PROJECT}
          exhausted={false}
          onRetryFix={() => {}}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("ship-cto-btn-2")).toBeNull();
  });

  it("other common repo-root filenames also now qualify (package.json, requirements.txt is NOT in the ext list on purpose — only known-code/config exts)", () => {
    const pkg = [
      "Root cause: missing script entry.",
      "```aurem-handoff",
      "Add a `build` script entry to package.json for the frontend.",
      "```",
    ].join("\n");
    render(
      <MemoryRouter>
        <MessageBubble
          idx={3}
          m={{ role: "assistant", content: pkg, provider: "mock", streaming: false }}
          sessionId="sess-1"
          activeProject={ACTIVE_PROJECT}
          exhausted={false}
        />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("ship-cto-btn-3")).toBeInTheDocument();
  });
});
