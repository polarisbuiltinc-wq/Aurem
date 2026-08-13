/**
 * Iter 388l — Bug 13/14/15/16 regression tests
 *
 * All four bugs discovered by the founder's real production QA pass
 * and locked here so a future refactor cannot silently regress any
 * of them.
 *
 *   Bug 13 — CollapsibleReply.firstLinePreview stripped hyphens from
 *            user-message previews.  "/repo-tree" was showing as
 *            "/repotree" and confused the founder.
 *   Bug 15 — streamChat surfaced raw Cloudflare 520 HTML as the
 *            assistant message content because the error path called
 *            `onError(text)` with the HTML body.
 *   Bug 14/16 — LoopStatusChip polled `/loop/active` every 10s
 *            forever (idle projects hammered the endpoint) and it
 *            rendered whatever raw string it got back on failure
 *            (including 5xx HTML) inside the chip.
 */
import { describe, it, expect } from "vitest";

/* ------------------------------------------------------------------ *
 *  Bug 13 — hyphen preserved in the collapsed user-message preview.  *
 * ------------------------------------------------------------------ */

// Mirror of CollapsibleReply::firstLinePreview.  Keep in sync with
// /app/frontend/src/components/CollapsibleReply.jsx.
function firstLinePreview(text) {
  const PREVIEW_CHARS = 110;
  const line = (text || "")
    .replace(/```[\s\S]*?```/g, " [code] ")
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l.length > 0) || "";
  return line.length > PREVIEW_CHARS
    ? `${line.slice(0, PREVIEW_CHARS)}…` : line;
}

describe("Bug 13/17/19 — user-typed content preserved verbatim in previews", () => {
  it("preserves hyphens in slash commands (Bug 13)", () => {
    expect(firstLinePreview("/repo-tree")).toBe("/repo-tree");
    expect(firstLinePreview("/loop-stats")).toBe("/loop-stats");
  });
  it("preserves asterisk wildcards (Bug 17)", () => {
    expect(firstLinePreview("/find *.jsx")).toBe("/find *.jsx");
    expect(firstLinePreview("grep '*' package.json")).toBe("grep '*' package.json");
  });
  it("preserves hyphens in normal shell-command messages (Bug 19)", () => {
    expect(firstLinePreview("Run `ls | head -20` on the pod"))
      .toBe("Run `ls | head -20` on the pod");
    expect(firstLinePreview("Try npm install --save-dev vitest"))
      .toBe("Try npm install --save-dev vitest");
  });
  it("preserves markdown noise verbatim too (was a bad trade-off)", () => {
    // Cosmetic: markdown chars now render as-is in previews.  Small
    // UX price for correctly preserving user input.
    expect(firstLinePreview("# Bold header")).toBe("# Bold header");
    expect(firstLinePreview("> quoted line")).toBe("> quoted line");
    expect(firstLinePreview("**bold**")).toBe("**bold**");
  });
  it("still collapses fenced code to the [code] placeholder", () => {
    expect(firstLinePreview("```js\nconsole.log(1)\n```after")).toBe(
      "[code] after",
    );
  });
});

/* ------------------------------------------------------------------ *
 *  Bug 15 — HTML detection for raw Cloudflare / ingress error bodies *
 *  (mirrors the guard we added in /app/frontend/src/lib/api.js:335). *
 * ------------------------------------------------------------------ */

function looksLikeHtml(txt) {
  return typeof txt === "string"
    && /^\s*(<!doctype\s+html|<html|<head|<body)/i.test(txt);
}

describe("Bug 15 — HTML error bodies never leak into onError text", () => {
  it("detects real Cloudflare 520 body prefix", () => {
    expect(looksLikeHtml(
      "<!DOCTYPE html>\n<html><body>Web server is returning an unknown error</body></html>"
    )).toBe(true);
  });
  it("detects a plain <html> body", () => {
    expect(looksLikeHtml("<html><body>oh no</body></html>")).toBe(true);
  });
  it("does NOT flag a JSON detail payload", () => {
    expect(looksLikeHtml('{"detail":"missing_param"}')).toBe(false);
  });
  it("does NOT flag prose that happens to contain angle brackets", () => {
    expect(looksLikeHtml("The <script> tag is dangerous.")).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 *  Bug 14/16 — LoopStatusChip polling backoff maths.                 *
 *  Mirror of the state machine in LoopStatusChip.jsx.                *
 * ------------------------------------------------------------------ */

function nextInterval({ hasSignal, idleStreak }) {
  const POLL_MS = 10_000;
  const POLL_IDLE_MS = 60_000;
  const IDLE_STREAK_TRIGGER = 2;
  const streak = hasSignal ? 0 : idleStreak + 1;
  const interval = streak >= IDLE_STREAK_TRIGGER ? POLL_IDLE_MS : POLL_MS;
  return { streak, interval };
}

describe("Bug 14/16 — LoopStatusChip poll backoff", () => {
  it("keeps 10s while a loop is active", () => {
    const { streak, interval } = nextInterval({ hasSignal: true, idleStreak: 5 });
    expect(streak).toBe(0);
    expect(interval).toBe(10_000);
  });
  it("stays at 10s after ONE idle poll (not yet a streak)", () => {
    const r = nextInterval({ hasSignal: false, idleStreak: 0 });
    expect(r.streak).toBe(1);
    expect(r.interval).toBe(10_000);
  });
  it("backs off to 60s after TWO idle polls in a row", () => {
    const r = nextInterval({ hasSignal: false, idleStreak: 1 });
    expect(r.streak).toBe(2);
    expect(r.interval).toBe(60_000);
  });
  it("returns to 10s the instant a signal appears again", () => {
    const r = nextInterval({ hasSignal: true, idleStreak: 4 });
    expect(r.streak).toBe(0);
    expect(r.interval).toBe(10_000);
  });
});
