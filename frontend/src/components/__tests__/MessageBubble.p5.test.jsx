/**
 * MessageBubble.p5.test.jsx — 2026-08-27, P5 (Journey/Intent-Grounding
 * build round). Confirmed engine-leak fixes:
 *   1. A raw ```aurem-handoff fence rendering verbatim in the chat
 *      stream (its only job is driving the Ship button, never meant
 *      to be visible prose).
 */
import { describe, it, expect } from "vitest";
import { stripHandoffFenceForDisplay } from "../MessageBubble.jsx";

describe("2026-08-27 P5 · aurem-handoff fence never renders as visible text", () => {
  it("strips a real aurem-handoff fence, keeping surrounding prose", () => {
    const raw = [
      "I read the file and found the bug.",
      "```aurem-handoff",
      "Fix the null check in Signup.jsx line 42.",
      "```",
    ].join("\n");
    const shown = stripHandoffFenceForDisplay(raw);
    expect(shown).not.toContain("aurem-handoff");
    expect(shown).not.toContain("Fix the null check");
    expect(shown).toContain("I read the file and found the bug.");
  });

  it("leaves normal content (no fence) completely untouched", () => {
    const raw = "Just a normal reply with no handoff block.";
    expect(stripHandoffFenceForDisplay(raw)).toBe(raw);
  });

  it("strips multiple aurem-handoff fences if present", () => {
    const raw = "A\n```aurem-handoff\nX\n```\nB\n```aurem-handoff\nY\n```\nC";
    const shown = stripHandoffFenceForDisplay(raw);
    expect(shown).not.toContain("aurem-handoff");
    expect(shown).toContain("A");
    expect(shown).toContain("B");
    expect(shown).toContain("C");
  });
});
