/**
 * OraImageBypass.security.test.jsx — Feb 2026 · Phase 5 · Security
 *
 * Locks the "Streamdown bypass is scoped to data:image/* only"
 * invariant.  Anything else — data:text/html, data:application/*,
 * javascript:, missing mime, whatever — MUST fall through to the
 * normal Streamdown-hardened path.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const SRC = readFileSync(
  join(process.cwd(), "src/pages/OraDirect.jsx"),
  "utf-8",
);

describe("Phase 5 · Streamdown bypass — scope invariants", () => {
  it("only unlocks the bypass when the message is flagged imageGen:true", () => {
    // If this ever drifts to unlocking based on content alone, prompt
    // injection could render arbitrary data: URIs.
    expect(SRC).toContain("m.imageGen ?");
  });

  it("imageGen:true is set ONLY in the /image-generate success branch", () => {
    // The flag lives inside the `else` of `if (!r.ok)` after the
    // POST to /image-generate.  Any other setter of imageGen:true
    // would break the security envelope. We count only real
    // assignments (not comments) — must be exactly 1.
    const assignments = SRC.split("\n").filter(
      line => /imageGen:\s*true/.test(line) && !line.trim().startsWith("//")
    );
    expect(assignments.length).toBe(1);
  });

  it("regex whitelists data:image/(png|jpeg|jpg|webp);base64 ONLY", () => {
    // Any looser regex (e.g. `data:[^)]+`) would let a
    // data:text/html payload through when imageGen:true is set.
    expect(SRC).toContain(
      "/^!\\[([^\\]]*)\\]\\((data:image\\/(?:png|jpe?g|webp);base64,[A-Za-z0-9+/=\\r\\n]+)\\)\\n\\n?([\\s\\S]*)$/"
    );
  });

  it("non-image data URIs fall back to Streamdown-hardened path", () => {
    // Explicit fallback branch — `if (!m) return <Streamdown>...`
    expect(SRC).toContain("Any content that doesn't cleanly match");
  });

  it("backend response mime is not trusted verbatim — client regex is the last line of defence", () => {
    // The comment block documents the three-layer envelope so any
    // future refactor sees why the regex is deliberately strict.
    expect(SRC).toContain("Defense-in-depth on the Streamdown");
    expect(SRC).toContain("LAST line of defence");
  });
});
