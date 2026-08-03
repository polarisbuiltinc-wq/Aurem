/**
 * LoopProgressBubble.stop_propagation.test.jsx — Feb 2026
 *
 * Founder repro: expanding a completed loop's collapsed
 * "Loop run · N step events [Aborted/Finished]" bubble silently
 * reset the chat composer state:
 *   chatMode: CASUAL → AGENTIC
 *   execMode: LOOP ON → LOOP OFF
 *
 * Root cause: the toggle click bubbled up from the LoopProgressBubble
 * button to an ancestor click handler in the chat scroller, which
 * mutated composer state on any bubble-through click.
 *
 * Fix: stopPropagation() + preventDefault() on the toggle so this
 * read-only historical view can never touch composer state.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import LoopProgressBubble from "../LoopProgressBubble.jsx";

describe("LoopProgressBubble — toggle click does not bubble", () => {
  it("stopPropagation prevents parent onClick from firing on toggle", () => {
    const parentClick = vi.fn();
    render(
      <div onClick={parentClick} data-testid="chat-scroller-mock">
        <LoopProgressBubble
          text="**Step 1 / 5 — plan**\n**Step 2 / 5 — execute**\n**Aborted**"
          streaming={false}
        >
          body
        </LoopProgressBubble>
      </div>,
    );
    const toggle = screen.getByTestId("loop-progress-toggle");
    fireEvent.click(toggle);
    // Bubble expanded (own state changed).
    expect(
      screen.getByTestId("loop-progress-bubble").getAttribute("data-expanded"),
    ).toBe("true");
    // Ancestor handler MUST NOT fire — this is the composer-state guard.
    expect(parentClick).not.toHaveBeenCalled();
  });

  it("collapsing (second click) also does not bubble", () => {
    const parentClick = vi.fn();
    render(
      <div onClick={parentClick}>
        <LoopProgressBubble
          text="**Step 1 / 5 — plan**\n**Aborted**"
          streaming={false}
        >
          body
        </LoopProgressBubble>
      </div>,
    );
    const toggle = screen.getByTestId("loop-progress-toggle");
    fireEvent.click(toggle);   // expand
    fireEvent.click(toggle);   // collapse
    expect(parentClick).not.toHaveBeenCalled();
  });
});
