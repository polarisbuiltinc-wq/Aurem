/**
 * Iter 388t — Bug 24 + Bug 25 regression tests.
 *
 * Bug 24: Rail navigation icons technically Tab-reachable (they're
 * real `<button>` elements) but had NO :focus-visible outline, so the
 * founder saw no visual indicator when Tab landed on them.  Fixed by
 * adding a global CSS rule targeting `[data-testid="rail-shell"] button`
 * (and every rail data-testid) with a 2px orange outline on
 * `:focus-visible`.  The tests below verify that:
 *   (a) The rail buttons in the rendered DOM carry the expected
 *       data-testid attributes so the CSS selector actually applies.
 *   (b) The index.css file contains the exact selector list so a
 *       future refactor can't silently drop the rule.
 *
 * Bug 25: Skip-to-content link was present but "useless as-is" per
 * prod feedback.  Fixed by:
 *   - Repositioning from `position: absolute` @ top:0/left:0 (landed
 *     under browser chrome) to `position: fixed` @ top:8/left:8.
 *   - zIndex 10000 (beats every drawer/modal).
 *   - onClick handler that programmatically focuses `#main-content`
 *     (some browsers don't move focus on a plain fragment jump).
 * Tests verify the onClick handler exists and points to `#main-content`.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { resolve } from "path";

describe("Bug 24 — Rail focus-visible outline CSS rule exists", () => {
  const cssPath = resolve(__dirname, "../../index.css");
  const css = readFileSync(cssPath, "utf8");

  it("targets rail-shell child buttons on :focus-visible", () => {
    expect(css).toMatch(/\[data-testid="rail-shell"\] button:focus-visible/);
  });

  it("covers every rail button data-testid family", () => {
    // If any of these selectors go missing, a whole class of rail
    // controls becomes invisible to keyboard users — hard-assert them
    // so the test file is the audit trail.
    const required = [
      '[data-testid^="rail-icon-"]:focus-visible',
      '[data-testid^="rail-item-"]:focus-visible',
      '[data-testid^="rail-repo-"]:focus-visible',
      '[data-testid^="rail-session-"]:focus-visible',
      '[data-testid="rail-new-chat"]:focus-visible',
      '[data-testid="rail-flyout-close"]:focus-visible',
      '[data-testid="rail-autohide-toggle"]:focus-visible',
      '[data-testid="rail-logout-btn"]:focus-visible',
      '[data-testid="rail-peek-pill"]:focus-visible',
      '[data-testid="ds2-sidebar-logo"]:focus-visible',
    ];
    for (const sel of required) {
      expect(css.includes(sel)).toBe(true);
    }
  });

  it("uses the site's accent-2 focus ring color, not a random outline", () => {
    // Grab the block starting at the rail selectors and check the
    // outline declaration references --accent-2.
    const idx = css.indexOf('[data-testid="rail-shell"] button:focus-visible');
    expect(idx).toBeGreaterThan(-1);
    const block = css.slice(idx, idx + 1500);
    expect(block).toMatch(/outline:\s*2px solid var\(--accent-2,\s*#ffc560\)/);
    expect(block).toMatch(/outline-offset:\s*2px/);
  });
});

describe("Bug 24 — Rail buttons stamp the data-testids the CSS relies on", () => {
  const railSrc = readFileSync(
    resolve(__dirname, "../nav/RailShell.jsx"),
    "utf8",
  );

  it("stamps rail-icon-<section> on every section button", () => {
    expect(railSrc).toMatch(/data-testid=\{`rail-icon-\$\{s\.id\}`\}/);
  });

  it("stamps rail-repo-<id>, rail-session-<id>, rail-new-chat", () => {
    expect(railSrc).toMatch(/data-testid=\{`rail-repo-\$\{r\.id\}`\}/);
    expect(railSrc).toMatch(/data-testid=\{`rail-session-\$\{s\.session_id\}`\}/);
    expect(railSrc).toMatch(/data-testid="rail-new-chat"/);
  });

  it("stamps rail-autohide-toggle, rail-logout-btn, rail-flyout-close, rail-peek-pill, ds2-sidebar-logo", () => {
    expect(railSrc).toMatch(/data-testid="rail-autohide-toggle"/);
    expect(railSrc).toMatch(/data-testid="rail-logout-btn"/);
    expect(railSrc).toMatch(/data-testid="rail-flyout-close"/);
    expect(railSrc).toMatch(/data-testid="rail-peek-pill"/);
    expect(railSrc).toMatch(/data-testid="ds2-sidebar-logo"/);
  });
});

describe("Bug 25 — Skip-to-content link repositioned + programmatic focus", () => {
  const appSrc = readFileSync(
    resolve(__dirname, "../../App.jsx"),
    "utf8",
  );

  it("uses position: fixed (was: absolute — landed under browser chrome)", () => {
    // Grab the skip-link block by its data-testid.
    const idx = appSrc.indexOf('data-testid="skip-to-content-link"');
    expect(idx).toBeGreaterThan(-1);
    // Look ~1400 chars around the anchor for the position declaration.
    const block = appSrc.slice(Math.max(0, idx - 200), idx + 1400);
    expect(block).toMatch(/position:\s*"fixed"/);
    // Belt-and-braces: not absolute anymore.
    expect(block).not.toMatch(/position:\s*"absolute"/);
  });

  it("has onClick that programmatically focuses #main-content", () => {
    const idx = appSrc.indexOf('data-testid="skip-to-content-link"');
    const block = appSrc.slice(Math.max(0, idx - 200), idx + 1400);
    expect(block).toMatch(/onClick=/);
    expect(block).toMatch(/getElementById\("main-content"\)/);
    expect(block).toMatch(/\.focus\(/);
  });

  it("targets #main-content anchor + landmark exists", () => {
    // The href must match the landmark id set on `<main>` below.
    expect(appSrc).toMatch(/href="#main-content"/);
    expect(appSrc).toMatch(/<main id="main-content" tabIndex=\{-1\}>/);
  });
});

describe("Bug 25 — main landmark has scroll-margin so jump is visible", () => {
  const cssPath = resolve(__dirname, "../../index.css");
  const css = readFileSync(cssPath, "utf8");

  it("#main-content has scroll-margin-top set", () => {
    expect(css).toMatch(/#main-content\s*\{[^}]*scroll-margin-top:\s*\d+px/);
  });
});
