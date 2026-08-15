/**
 * Iter 388-ag (2026-02-14) — Meta Pixel consent gate removal.
 *
 * Founder decision (documented risk): the pixel now fires for every
 * visitor regardless of consent state or jurisdiction. This is
 * KNOWN to be GDPR / DPDP / CCPA non-compliant if / when Aurem
 * scales into opt-in jurisdictions — restoration path lives in the
 * inline comment inside `frontend/index.html`.
 *
 * This test locks the runtime contract in place so a future refactor
 * that reintroduces the consent gate (accidentally or otherwise)
 * fails LOUDLY. If founder later decides to restore the gate, this
 * test file should be removed in the same PR.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const INDEX_HTML = path.resolve(__dirname, "..", "..", "..", "index.html");


describe("Meta Pixel — Iter 388-ag runtime contract", () => {
  const src = fs.readFileSync(INDEX_HTML, "utf-8");

  // Isolate just the Meta Pixel section (between markers) so we don't
  // accidentally match `canLoad` or similar names in unrelated blocks.
  const pixelBlockMatch = src.match(
    /<!-- Meta Pixel Code[\s\S]*?<!-- End Meta Pixel Code -->/,
  );

  it("pixel block is present in index.html", () => {
    expect(pixelBlockMatch, "Meta Pixel block missing from index.html").toBeTruthy();
  });

  const block = pixelBlockMatch ? pixelBlockMatch[0] : "";

  it("pixel ID 1571887197933821 is initialised", () => {
    expect(block).toMatch(/fbq\('init', '1571887197933821'\)/);
  });

  it("PageView is tracked at load time", () => {
    expect(block).toMatch(/fbq\('track', 'PageView'\)/);
  });

  it("runtime code has NO consent gate — `canLoad` is not referenced in executed code", () => {
    // Strip comments first (SGML/HTML comments and JS block comments)
    // so we're only looking at the executed script body.
    const executable = block
      .replace(/<!--[\s\S]*?-->/g, "")      // HTML comments
      .replace(/\/\*[\s\S]*?\*\//g, "")     // JS block comments
      .replace(/\/\/[^\n]*/g, "");          // JS line comments

    // If any of these tokens survive into executed code, the consent
    // gate is back — fail with a clear message.
    for (const banned of ["canLoad", "aurem_consent", "globalPrivacyControl"]) {
      expect(
        executable.includes(banned),
        `Iter 388-ag contract violation: '${banned}' present in executable ` +
        `pixel code. Founder chose (b) — no consent gate. If this is intentional ` +
        `(gate restored), delete this test in the same PR.`,
      ).toBe(false);
    }
  });

  it("<noscript> fallback still uses the correct pixel ID", () => {
    // Live in <body> per HTML5 spec — search whole file, not the pixel block.
    expect(src).toMatch(
      /noscript><img[^>]*facebook\.com\/tr\?id=1571887197933821/,
    );
  });
});
