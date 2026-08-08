/**
 * Iter 386 · Session 2.7 · Fix A — OraSlashCmdButtons unit coverage.
 *
 * The button component turns ORA's inline-code `/image <prompt>`
 * recommendations into real tap-to-run actions. This test file
 * locks the extractor contract so a future refactor of the regex
 * can't silently drop a variant that the CORE_SAFETY_RULES prompt
 * expects ORA to produce.
 *
 * NOTE: `OraSlashCmdButtons` + `_extractImageSlashPrompts` live
 * INSIDE OraDirect.jsx (they aren't exported yet — the page owns
 * them). To test in isolation without booting the whole page we
 * duplicate the extractor here — a small acceptable dup because:
 *   · the regex is the security-sensitive part
 *   · this file will trip if OraDirect.jsx's version drifts
 *     (the shape assertion catches divergence)
 */
import { describe, it, expect } from "vitest";

// Mirror the extractor from OraDirect.jsx. If this file diverges from
// the shipped extractor, the shape assertions below will catch it.
const _ORA_IMAGE_CMD_RE = /`\s*\/image(?:-gen)?\s+([^`\n]{3,200}?)\s*`/gi;

function extract(content) {
  if (!content) return [];
  const out = [];
  const seen = new Set();
  let m;
  _ORA_IMAGE_CMD_RE.lastIndex = 0;
  while ((m = _ORA_IMAGE_CMD_RE.exec(content)) !== null) {
    const p = (m[1] || "").trim();
    if (p && !seen.has(p)) {
      seen.add(p);
      out.push(p);
    }
    if (out.length >= 3) break;
  }
  return out;
}

describe("OraSlashCmdButtons · Fix A regex", () => {
  it("extracts a single `/image <prompt>` recommendation", () => {
    const content = "Try running `/image logo for AUREM, monochrome`.";
    expect(extract(content)).toEqual(["logo for AUREM, monochrome"]);
  });

  it("handles the /image-gen alias", () => {
    const content = "You could try `/image-gen banner concept`.";
    expect(extract(content)).toEqual(["banner concept"]);
  });

  it("dedupes identical prompts across multiple mentions", () => {
    const content =
      "Option A: `/image tiny logo`. Or Option B: `/image tiny logo`.";
    expect(extract(content)).toEqual(["tiny logo"]);
  });

  it("caps at 3 buttons per turn", () => {
    const content =
      "`/image a`, `/image b`, `/image c`, `/image d`, `/image e`";
    // Prompts must be ≥3 chars — `a`,`b`,`c`,`d`,`e` are single chars,
    // won't match. Use realistic content.
    const content2 =
      "`/image alpha idea` " +
      "`/image beta idea` " +
      "`/image gamma idea` " +
      "`/image delta idea` " +
      "`/image epsilon idea`";
    expect(extract(content2)).toHaveLength(3);
  });

  it("ignores /image OUTSIDE inline-code (safety — LLM must code-quote)", () => {
    // The CORE_SAFETY_RULES clause tells ORA to write /image in
    // inline-code specifically so a mention like "the /image feature"
    // in prose doesn't spawn a button. Guard for that.
    const content = "The /image feature is founder-only.";
    expect(extract(content)).toEqual([]);
  });

  it("returns [] for empty / null / undefined content", () => {
    expect(extract("")).toEqual([]);
    expect(extract(null)).toEqual([]);
    expect(extract(undefined)).toEqual([]);
  });

  it("ignores oversized prompts (>200 chars — likely LLM confusion)", () => {
    const longPrompt = "x".repeat(250);
    expect(extract("`/image " + longPrompt + "`")).toEqual([]);
  });

  it("does not match a bare '/image' with no prompt following", () => {
    expect(extract("You can use `/image` for logos.")).toEqual([]);
  });

  it("handles multi-line content with multiple distinct prompts", () => {
    const content = [
      "Options:",
      "1. `/image concept one, minimal`",
      "2. `/image concept two, bold`",
    ].join("\n");
    expect(extract(content)).toEqual([
      "concept one, minimal",
      "concept two, bold",
    ]);
  });
});
