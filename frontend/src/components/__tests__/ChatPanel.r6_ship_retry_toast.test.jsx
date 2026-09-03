/**
 * R6 (overnight round) — one-click Retry on the ship-failure toast +
 * ShipConfirmModal's error-phase Retry button.
 *
 * Source-scan style (matches this codebase's existing convention for
 * label/behavior fixes, e.g. ShipConfirmModal.p1b_honest_label.test.jsx)
 * — avoids a brittle full-DOM render of these very large components.
 */
import { readFileSync } from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const CHAT_PANEL_SRC = readFileSync(
  path.resolve(__dirname, "../ChatPanel.jsx"), "utf-8"
);
const SHIP_MODAL_SRC = readFileSync(
  path.resolve(__dirname, "../ShipConfirmModal.jsx"), "utf-8"
);

describe("ChatPanel — ship-failure toast has a Retry action (R6)", () => {
  it("handleShipConfirm's catch offers a Retry action that re-calls handleShipConfirm", () => {
    const idx = CHAT_PANEL_SRC.indexOf("Ship failed to start");
    expect(idx).toBeGreaterThan(-1);
    const nearby = CHAT_PANEL_SRC.slice(idx, idx + 400);
    expect(nearby).toContain('label: "Retry"');
    expect(nearby).toContain("handleShipConfirm()");
  });
});

describe("ShipConfirmModal — error phase has a Retry button (R6)", () => {
  it("renders data-testid=ship-modal-retry wired to handleShip", () => {
    expect(SHIP_MODAL_SRC).toContain('data-testid="ship-modal-retry"');
    const idx = SHIP_MODAL_SRC.indexOf('data-testid="ship-modal-retry"');
    expect(SHIP_MODAL_SRC.slice(idx, idx + 60)).toContain("onClick={handleShip}");
  });

  it("handleShip allows re-entry from the error phase (not just confirm)", () => {
    expect(SHIP_MODAL_SRC).toContain('phase !== "confirm" && phase !== "error"');
  });
});
