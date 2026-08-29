# Drift-Blocked Rollbacks admin tile (2026-08-30)

Agent-tested, NOT founder-confirmed. Live E2E proof (real running pod,
real admin login, real Mongo data) that the new tile renders and
expands correctly.

## Steps
1. Seeded 1 real `ship_rollback_drift_detected` trust event directly
   in this pod's Mongo (`trust_surface_events` collection):
   `{loop_id: "loop_drift_demo_screenshot", branch: "main",
   expected: "d1234567890fulla", actual: "e9876543210fullb"}`.
2. Logged in live as `test@aurem.dev` (admin), navigated to
   `/admin/system-health`.
3. Screenshot (1920×800) confirms: a new **"DRIFT-BLOCKED ROLLBACKS"**
   card renders immediately next to the **"GITHUB WEBHOOK FENCE"**
   card, status badge "WARMING" (amber, since count > 0), showing
   "Drift-blocked rollbacks (24h): **1**".
4. Clicked the `▼▶ 1 drift event` expand summary → revealed the
   individual row: `loop: loop_drift_demo_screenshot · branch: main` /
   `expected d123456789 → current e987654321` /
   `2026-08-29T18:53:03.816915+00:00` — exactly the 5 fields the spec
   asked for (`loop_id, branch, expected_sha, current_sha, timestamp`).
5. Cleaned up: seeded event deleted from Mongo after the screenshot.

## Tests — 4 backend + 3 frontend, all pass
- `tests/test_drift_alerts_admin_2026_08_30.py`: `test_t_drift_alert_shows_count`,
  `test_t_drift_alert_empty`, `test_t_drift_alert_expands_data_shape`,
  `test_drift_alerts_requires_admin` (fail-closed for non-admins,
  same pattern as every other admin tile).
- `frontend/src/pages/__tests__/AdminSystemHealth.driftAlerts.test.jsx`:
  `t_drift_alert_shows_count`, `t_drift_alert_empty` (shows "0", no
  expand summary rendered), `t_drift_alert_expands` (click reveals the
  individual row with loop_id/branch/expected/current visible).

## Design notes
- **READ-ONLY**: the tile has no action buttons — the admin can see a
  drift block happened, but only the loop owner (in their own chat)
  can acknowledge and proceed. Matches the founder's explicit
  constraint.
- Data source: the EXISTING `ship_rollback_drift_detected` trust
  event (no new writer needed — the drift-detection fix already logs
  it at block time).
- New endpoint `GET /admin/drift-alerts` (same `_require_admin` +
  `require_db` pattern as every other admin tile, zero new
  dependencies, zero LLM calls).

## STATUS: CLOSED (agent-tested, not founder-confirmed).
