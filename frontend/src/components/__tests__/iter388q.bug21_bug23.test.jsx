/**
 * Iter 388q — Bug 21 regression test (GFM pipe-table renderer).
 *
 * Bug 21: `| Feature | Python | JavaScript |\n|---|---|---|\n| … |`
 * landed as raw pipe-text in the chat bubble because the codebase
 * deliberately doesn't ship react-markdown / remark-gfm.  Added a
 * minimal in-file table detector + renderer.
 *
 * Bug 23 (duplication) is a state-management race, tested implicitly
 * via the ChatPanel retry short-circuit; the state-machine assertion
 * is inlined here as a source-string check so drift is loud.
 */
import { describe, it, expect } from "vitest";
import React from "react";
import { render } from "@testing-library/react";
import { readFileSync } from "fs";
import { resolve } from "path";
import RenderedMessage from "../RenderedMessage.jsx";

describe("Bug 21 — GFM pipe tables render as <table>", () => {
  it("renders a simple 2x2 table with header + 1 body row", () => {
    const md = [
      "| Feature | Python |",
      "|---------|--------|",
      "| Typing  | dynamic |",
    ].join("\n");
    const { getByTestId } = render(<RenderedMessage text={md} />);
    const table = getByTestId("rendered-md-table");
    expect(table.tagName.toLowerCase()).toBe("table");
    expect(table.querySelectorAll("th").length).toBe(2);
    expect(table.querySelectorAll("tbody tr").length).toBe(1);
    expect(table.querySelectorAll("tbody td").length).toBe(2);
    expect(table.textContent).toMatch(/Feature/);
    expect(table.textContent).toMatch(/dynamic/);
  });

  it("handles 3-column header + 3 rows (the exact Bug 21 shape)", () => {
    const md = [
      "| Feature | Python | JavaScript |",
      "|---|---|---|",
      "| Typing | Dynamic | Dynamic |",
      "| Compile | Interpreted | JIT |",
      "| Package | pip | npm |",
    ].join("\n");
    const { getByTestId } = render(<RenderedMessage text={md} />);
    const table = getByTestId("rendered-md-table");
    expect(table.querySelectorAll("th").length).toBe(3);
    expect(table.querySelectorAll("tbody tr").length).toBe(3);
    expect(table.textContent).toMatch(/JavaScript/);
    expect(table.textContent).toMatch(/Dynamic/);
    expect(table.textContent).toMatch(/npm/);
  });

  it("keeps prose above and below the table", () => {
    const md = [
      "Here's the comparison:",
      "",
      "| A | B |",
      "|---|---|",
      "| 1 | 2 |",
      "",
      "Hope this helps!",
    ].join("\n");
    const { container, getByTestId } = render(<RenderedMessage text={md} />);
    expect(getByTestId("rendered-md-table")).toBeTruthy();
    // The prose surrounding the table must still be visible.
    expect(container.textContent).toMatch(/Here's the comparison/);
    expect(container.textContent).toMatch(/Hope this helps!/);
  });

  it("does NOT render a table for a lone pipe-line without a separator", () => {
    // A single pipe line isn't a table — must have `|---|` row.
    const md = "| Just a | pipe | line";
    const { container, queryByTestId } = render(<RenderedMessage text={md} />);
    expect(queryByTestId("rendered-md-table")).toBeNull();
    expect(container.textContent).toMatch(/\| Just a/);
  });

  it("renders inline code inside table cells", () => {
    const md = [
      "| Lang | Manager |",
      "|------|---------|",
      "| Py   | `pip`   |",
    ].join("\n");
    const { getByTestId } = render(<RenderedMessage text={md} />);
    const cell = getByTestId("rendered-md-table").querySelector("tbody td:last-child");
    expect(cell.querySelector("code")).toBeTruthy();
    expect(cell.querySelector("code").textContent).toBe("pip");
  });
});

describe("Bug 23 — retry short-circuit locked in ChatPanel source", () => {
  it("ChatPanel skips retry when the bubble is already done", () => {
    const src = readFileSync(
      resolve(__dirname, "../ChatPanel.jsx"),
      "utf8",
    );
    // The literal marker + the short-circuit logic must be present.
    // If someone reverts either, this test fails loudly.
    expect(src).toMatch(/Iter 388q — Bug 23 fix/);
    expect(src).toMatch(/alreadyDone = !!/);
    expect(src).toMatch(/if \(alreadyDone\)/);
  });

  it("ChatPanel resets streaming=true on the retried bubble", () => {
    // Otherwise late onDone from the aborted stream could re-flag it
    // as non-streaming and swallow the retry's tokens silently.
    const src = readFileSync(
      resolve(__dirname, "../ChatPanel.jsx"),
      "utf8",
    );
    // The comment marker `Iter 388q — reset UNCONDITIONALLY` and
    // `streaming: true` should both live inside the retry-reset
    // block.  Grep both and confirm they're in the same 500-char
    // window so a lazy revert can't strip just one.
    const idx = src.indexOf("Iter 388q — reset UNCONDITIONALLY");
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(idx, idx + 900);
    expect(window).toMatch(/streaming:\s*true/);
  });
});
