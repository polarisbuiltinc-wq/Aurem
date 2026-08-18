/**
 * Iter 388m — Bug 9 regression tests
 *
 * Bug 9 (P0, data-loss looking): the chat message that used to leak
 * raw `<longcat_tool_call>read_repo_file …</longcat_tool_call>` (Bug 2)
 * showed as COMPLETELY EMPTY after the Iter 388h sanitizer widening
 * — the founder saw a ghost bubble with only the "via longcat-2.0"
 * attribution footer.
 *
 * Root cause (NOT persistence): backend stored the assistant reply
 * verbatim (see `backend/services/ora_chat/session.py::append_message`
 * — no sanitization at persist time).  The reply itself was ENTIRELY
 * internal tool-call XML with zero user-facing prose, so the frontend
 * sanitizer collapsed it to empty and RenderedMessage returned an
 * empty span.
 *
 * Fix: RenderedMessage now detects the "original had a body but
 * sanitizer stripped everything" case and renders a subtle italic
 * placeholder so the founder knows the turn completed but produced
 * no visible reply.
 */
import { describe, it, expect } from "vitest";
import React from "react";
import { render } from "@testing-library/react";
import RenderedMessage from "../RenderedMessage.jsx";

describe("Bug 9 — placeholder for messages fully stripped by sanitizer", () => {
  it("renders the placeholder when input is ONLY tool-call XML", () => {
    const raw = '<longcat_tool_call>read_repo_file {"path":"backend/routers/health.py"}</longcat_tool_call>';
    const { getByTestId } = render(<RenderedMessage text={raw} />);
    const el = getByTestId("rendered-message-empty-placeholder");
    expect(el.textContent).toMatch(/didn't have a text reply|rephrasing/i);
  });

  it("renders the placeholder for orphan open tag (streaming cutoff)", () => {
    const raw = '<longcat_tool_call>read_repo_file {"path":"backend/main.py';
    const { getByTestId } = render(<RenderedMessage text={raw} />);
    expect(
      getByTestId("rendered-message-empty-placeholder"),
    ).toBeTruthy();
  });

  it("does NOT render placeholder for legitimate prose+tool mix", () => {
    const raw = 'Here is my read.\n<longcat_tool_call>x</longcat_tool_call>\nAll good.';
    const { queryByTestId } = render(<RenderedMessage text={raw} />);
    expect(
      queryByTestId("rendered-message-empty-placeholder"),
    ).toBeNull();
  });

  it("does NOT render placeholder for a truly empty message", () => {
    // Guard: an assistant row with no body (never happens in practice
    // but the sanitizer test-suite mustn't hallucinate a placeholder
    // for a legitimately-empty input).
    const { queryByTestId } = render(<RenderedMessage text={""} />);
    expect(
      queryByTestId("rendered-message-empty-placeholder"),
    ).toBeNull();
  });

  it("does NOT render placeholder for whitespace-only prose", () => {
    const { queryByTestId } = render(<RenderedMessage text={"   \n  "} />);
    expect(
      queryByTestId("rendered-message-empty-placeholder"),
    ).toBeNull();
  });

  it("also covers claude_/qwen_/gpt_ vendor variants", () => {
    for (const prefix of ["claude", "qwen", "gpt"]) {
      const raw = `<${prefix}_tool_call>x</${prefix}_tool_call>`;
      const { getByTestId, unmount } = render(<RenderedMessage text={raw} />);
      expect(getByTestId("rendered-message-empty-placeholder")).toBeTruthy();
      unmount();
    }
  });
});
