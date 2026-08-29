/**
 * First-Experience Wave · P2-B (2026-08-28) — unified rollback UI.
 *
 * Verifies the LAST native window.confirm() rollback dialogs are gone
 * from the 3 remaining ship/rollback surfaces (MessageBubble's
 * "Approve the fix" flow, ShipConfirmModal, Projects task-rollback)
 * and all three now mount the shared themed RollbackConfirmModal.
 * ShippedRow (LoopLiveFeed.jsx) and OperationHistory were already
 * themed at Iter 362 — not re-tested here.
 */
import { readFileSync } from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const read = (p) => readFileSync(path.resolve(__dirname, p), "utf-8");

describe("P2-B — MessageBubble Approve-the-fix rollback is themed", () => {
  const src = read("../MessageBubble.jsx");
  it("imports RollbackConfirmModal", () => {
    expect(src).toContain('import RollbackConfirmModal from "./RollbackConfirmModal"');
  });
  it("no longer calls window.confirm for rollback", () => {
    expect(src).not.toMatch(/window\.confirm\(\s*\n?\s*"Rollback this commit/);
    expect(src).not.toMatch(/window\.confirm\("Are you sure\? This pushes/);
  });
  it("still POSTs the real rollback endpoint on confirm", () => {
    expect(src).toMatch(/await api\.post\(`\/cto\/tasks\/\$\{tid\}\/rollback`, \{ confirm: "ROLLBACK" \}\)/);
  });
});

describe("P2-B — ShipConfirmModal rollback is themed", () => {
  const src = read("../ShipConfirmModal.jsx");
  it("imports RollbackConfirmModal and no longer calls window.confirm", () => {
    expect(src).toContain('import RollbackConfirmModal from "./RollbackConfirmModal"');
    expect(src).not.toMatch(/window\.confirm\("Rollback this ship/);
  });
});

describe("P2-B — Projects.jsx TaskRow rollback is themed", () => {
  const src = read("../../pages/Projects.jsx");
  it("imports RollbackConfirmModal and no longer calls window.confirm", () => {
    expect(src).toContain('import RollbackConfirmModal from "../components/RollbackConfirmModal"');
    expect(src).not.toMatch(/window\.confirm\(\s*\n\s*`Rollback commit/);
  });
});
