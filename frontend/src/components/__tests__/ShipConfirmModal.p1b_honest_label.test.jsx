/**
 * Overnight T6/P1b (2026-08-28) — "Run in background" honesty fix.
 *
 * The old label implied the app would keep tracking the ship and
 * surface it later. In reality `closeAll()` clears the local poll
 * timer — only the SERVER-side task keeps running, this dialog loses
 * visibility into it. Relabeled to state that honestly (safe default:
 * relabel only, no new tracking infra).
 */
import { readFileSync } from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const SRC = readFileSync(
  path.resolve(__dirname, "../ShipConfirmModal.jsx"),
  "utf-8"
);

describe("ShipConfirmModal — P1b honest background-close label", () => {
  it("no longer claims 'Run in background'", () => {
    expect(SRC).not.toContain("Run in background");
  });

  it("uses the honest 'task keeps running' label", () => {
    expect(SRC).toContain("Close (task keeps running)");
  });

  it("keeps the same data-testid so existing flows aren't broken", () => {
    expect(SRC).toContain('data-testid="ship-modal-minimize"');
  });
});
