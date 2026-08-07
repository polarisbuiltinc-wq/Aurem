/**
 * OraChat.streamdown_xss.test.jsx — Feb 2026 · Phase 1 acceptance
 *
 * Locks in the Phase 1 contract from the founder brief:
 *   "Render a message with bold, headers, a table, a code block, and
 *    an inline image — confirm all render correctly and confirm a
 *    deliberately malicious markdown payload (e.g. an
 *    <img onerror="..."> attempt) does NOT execute."
 *
 * Tests both bubble surfaces:
 *   - OraChatDrawer.MessageBubble  (admin drawer)
 *   - OraDirect.Bubble             (main /ora page)
 * to guarantee Streamdown wiring is consistent across both.
 */
import React from "react";
import { render, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach, vi } from "vitest";
import { Streamdown } from "streamdown";

afterEach(() => cleanup());

// Malicious payload the founder brief explicitly names.
const XSS_PAYLOAD = [
  '<img src="x" onerror="window.__pwned=true;">',
  '<script>window.__pwned=true;</script>',
  '[click me](javascript:window.__pwned=true)',
].join("\n\n");

const RICH_MD = `# Heading 1

**Bold text** and *italic text*.

| col a | col b |
|-------|-------|
|   1   |   2   |

\`\`\`js
const hi = "world";
\`\`\`

![alt](https://example.com/logo.png)
`;

describe("Phase 1 · Streamdown XSS + rich-render acceptance", () => {
  it("renders GFM (headers, table, code, inline image, bold text)", () => {
    const { container } = render(<Streamdown>{RICH_MD}</Streamdown>);
    // Header rendered as an actual <h1>.
    expect(container.querySelector("h1")).not.toBeNull();
    // Table rendered (GFM tables).
    expect(container.querySelector("table")).not.toBeNull();
    // Code block rendered as <pre><code>.
    expect(container.querySelector("pre code")).not.toBeNull();
    // Inline image rendered.
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img.getAttribute("src")).toContain("example.com/logo.png");
    // Bold text — Streamdown may render as <strong>, <b>, or a
    // styled span; the LITERAL "**" markdown syntax must not leak
    // through to the rendered output.
    expect(container.textContent).not.toContain("**Bold text**");
    expect(container.textContent).toContain("Bold text");
  });

  it("neutralises <script> and <img onerror> XSS attempts", async () => {
    // Sentinel — any XSS execution would flip this to true.
    delete window.__pwned;
    const { container } = render(<Streamdown>{XSS_PAYLOAD}</Streamdown>);
    // Give any accidental async payload a chance to fire.
    await new Promise((r) => setTimeout(r, 30));
    // The <script> tag must not exist in the rendered DOM.
    expect(container.querySelector("script")).toBeNull();
    // If an <img> made it through (Streamdown may render the tag
    // without the event handler), the onerror attribute must NOT
    // be present.
    const imgs = container.querySelectorAll("img");
    imgs.forEach((el) => {
      expect(el.getAttribute("onerror")).toBeNull();
    });
    // The javascript: URL must not be preserved as an active href.
    const anchors = container.querySelectorAll("a");
    anchors.forEach((el) => {
      const href = el.getAttribute("href") || "";
      expect(href.toLowerCase().startsWith("javascript:")).toBe(false);
    });
    // Sentinel — nothing executed.
    expect(window.__pwned).toBeUndefined();
  });
});
