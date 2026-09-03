/**
 * lib/shipStatus.js — R3 P1-5 (2026 overnight round).
 *
 * ONE canonical ship/rollback status vocabulary shared by every ship
 * surface (ShipPendingCard approve step, ShipConfirmModal, LoopLiveFeed's
 * inline Shipped row) so the same underlying state is always described
 * to the user with the exact same word and color everywhere.
 *
 * Each surface keeps its OWN internal phase state machine (they mount
 * in different contexts and drive different APIs) — this module is the
 * single source of truth for the LABEL + COLOR a phase maps to, so
 * "unify the status set" doesn't require merging the components
 * themselves (higher regression risk, deferred — see ROADMAP F17).
 */
export const SHIP_STATUS = {
  PENDING:      "pending",
  SHIPPING:     "shipping",
  LIVE:         "live",
  ROLLING_BACK: "rolling_back",
  ROLLED_BACK:  "rolled_back",
  FAILED:       "failed",
};

const _LABEL = {
  [SHIP_STATUS.PENDING]:      "Pending approval",
  [SHIP_STATUS.SHIPPING]:     "Shipping…",
  [SHIP_STATUS.LIVE]:         "Shipped",
  [SHIP_STATUS.ROLLING_BACK]: "Rolling back…",
  [SHIP_STATUS.ROLLED_BACK]:  "Rolled back",
  [SHIP_STATUS.FAILED]:       "Failed",
};

const _COLOR = {
  [SHIP_STATUS.PENDING]:      "#8A8A8A",
  [SHIP_STATUS.SHIPPING]:     "#FF6608",
  [SHIP_STATUS.LIVE]:         "#22C55E",
  [SHIP_STATUS.ROLLING_BACK]: "#FF6608",
  [SHIP_STATUS.ROLLED_BACK]:  "#8A8A8A",
  [SHIP_STATUS.FAILED]:       "#EF4444",
};

export function shipStatusLabel(status) {
  return _LABEL[status] || status;
}

export function shipStatusColor(status) {
  return _COLOR[status] || "#8A8A8A";
}
