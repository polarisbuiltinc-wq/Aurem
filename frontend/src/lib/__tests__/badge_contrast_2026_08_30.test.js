/**
 * badge_contrast_2026_08_30.test.js — P2 fix regression guard.
 *
 * Bug: ORA Chat inline `code` spans (used for both file-path
 * references AND tool/function-name references, e.g. `web_verify`,
 * `chromium_path`) rendered pale-on-pale on dark surfaces (the ORA
 * Chat drawer) because `.ora-md code` set an opaque light background
 * with no matching foreground color, so it silently inherited
 * whatever ambient text color the surface happened to use.
 *
 * Fix: a single --code-fg/--code-bg CSS var pair (index.css), one
 * per theme, applied to `.ora-md code`. `.ora-md pre code` (fenced
 * blocks) deliberately does NOT redeclare `color`, so it inherits
 * the exact same foreground — the two token types cannot diverge.
 *
 * These tests read the actual CSS source (jsdom can't reliably
 * compute rendered colors) so a future edit that reintroduces a
 * hardcoded color/background, or drops below 4.5:1, fails CI.
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { contrastRatio } from "../contrastRatio";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CSS = fs.readFileSync(
  path.resolve(__dirname, "../../index.css"),
  "utf8",
);

function extractBlock(re) {
  const m = re.exec(CSS);
  if (!m) return null;
  const start = m.index + m[0].length;
  const end = CSS.indexOf("}", start);
  return CSS.slice(start, end);
}

function extractVar(block, name) {
  const m = new RegExp(`${name}\\s*:\\s*(#[0-9a-fA-F]{3,8})`).exec(block);
  return m ? m[1] : null;
}

describe("contrastRatio util sanity", () => {
  test("known-passing pairs round-trip close to expected ratios", () => {
    expect(contrastRatio("#111827", "#f3f4f6")).toBeCloseTo(16.1, 0);
    expect(contrastRatio("#e6edf3", "#161b22")).toBeCloseTo(14.6, 0);
  });

  test("black on white is ~21:1, same color is 1:1", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 0);
    expect(contrastRatio("#888888", "#888888")).toBeCloseTo(1, 1);
  });
});

describe("t_badge_contrast_in_both_themes", () => {
  const darkBlock = extractBlock(/:root\s*\{/);
  const lightBlock = extractBlock(/html\[data-theme="light"\]\s*\{/);

  test("dark theme --code-fg/--code-bg pair is defined and >= 4.5:1", () => {
    const fg = extractVar(darkBlock, "--code-fg");
    const bg = extractVar(darkBlock, "--code-bg");
    expect(fg).toBeTruthy();
    expect(bg).toBeTruthy();
    expect(contrastRatio(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });

  test("light theme --code-fg/--code-bg pair is defined and >= 4.5:1", () => {
    const fg = extractVar(lightBlock, "--code-fg");
    const bg = extractVar(lightBlock, "--code-bg");
    expect(fg).toBeTruthy();
    expect(bg).toBeTruthy();
    expect(contrastRatio(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("t_badge_single_source", () => {
  test(".ora-md code binds color+background to the shared --code-fg/--code-bg vars", () => {
    const m = /\.ora-md code\s*\{([^}]*)\}/.exec(CSS);
    expect(m).toBeTruthy();
    expect(m[1]).toMatch(/color:\s*var\(--code-fg\)/);
    expect(m[1]).toMatch(/background:\s*var\(--code-bg\)/);
  });

  test(".ora-md pre code does NOT redeclare color — it must inherit the same foreground, never a second one", () => {
    const m = /\.ora-md pre code\s*\{([^}]*)\}/.exec(CSS);
    expect(m).toBeTruthy();
    expect(m[1]).not.toMatch(/color\s*:/);
  });
});
