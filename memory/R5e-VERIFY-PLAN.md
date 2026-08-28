# R5e — Verify Plan (ready to execute the moment the founder says "webhook done")

Trigger: founder has completed `/app/memory/R5-WEBHOOK-FIX.md`'s Step
0-4 (fresh webhook secret set on BOTH GitHub's App settings and
production's Admin → GitHub App Config, `pull_request` event
subscribed, "Redeliver" test showed `OK`).

**Do not run this until that trigger is confirmed** (ask the founder
directly, or check `GET /admin/github-webhook-fence` on PRODUCTION
shows `"ok": true` with `pull_request` in `subscribed_events` and
`failing_count: 0`). Running it earlier just reproduces R2's known
gap for no new information.

## Pre-flight (30s)

1. `GET /admin/github-webhook-fence` (production) → confirm `ok: true`.
2. Confirm `polarisbuiltinc-wq/ora-grounding` is still clean:
   `GET /repos/polarisbuiltinc-wq/ora-grounding/branches` → exactly
   `["main"]`, no `.aurem/` files in the root listing (same check R5
   already ran and confirmed clean as of 2026-08-28).

## Drill (reuses R2's exact harness, unchanged)

3. Re-run `/app/e2e-proof/T7-live/drill_script.py` as-is (same repo,
   same installation `152797252`) — it already performs the full
   open→merge / open→close→delete / no-orphans sequence AND already
   has the "capture real webhook delivery via App Deliveries API +
   replay through `dispatch_pull_request_webhook`" logic built in
   (previously produced empty results only because there was nothing
   to capture — the code path itself needs no changes).
4. This time, ALSO capture a DIRECT delivery proof (not just via the
   Deliveries API, which only proves GitHub sent it): confirm the
   `pull_request` webhook actually reached the app in real time by
   checking THIS pod's own `ship_pr_events` collection immediately
   after the merge step — if `dispatch_pull_request_webhook` was
   invoked by the real HTTP POST /github/app/webhook route (not the
   replay fallback), there will be a row with `status: "merged"` for
   that PR number within a few seconds of the merge, with no manual
   replay needed. THIS is the actual pass condition for R5e (real
   webhook, delivered live, not the R2 fallback-replay path).

## Pass criteria (all 6, this time for real)

- `pr_open.json`, `pr_merge.json` (`merged: true`), `pr_close.json`,
  `branch_delete.json` (404-confirmed), `no_orphans.json` — same as
  R2 (already proven to work, not expected to regress).
- `webhook_payload.json` — THIS time must show a REAL delivery with
  `status_code` in 200-299 (not 401), for both the merge PR and the
  close PR, captured via `GET /admin/github-webhook-fence`'s
  `recent_deliveries` immediately after the drill.
- `ship_pr_events.json` — must show `ship_pr_merged` and
  `ship_pr_closed` rows written by the REAL webhook route (not the
  R2 manual-replay fallback) for the two drilled PR numbers.

## Cleanup (mandatory, same as R2)

5. Delete the marker file(s) from `main` if the merge landed one
   (same one-line `DELETE /repos/.../contents/{path}` call R2 used).
6. Confirm final branch list = `["main"]` only.
7. Confirm SHA of `main` before vs after — if it differs from the
   pre-drill SHA by anything other than "add marker, then revert
   marker" (i.e., if anything besides the harness's own no-op commits
   landed), STOP and open a revert PR instead of force-pushing.

## If it still fails after the founder's fix

Re-run R5a's forensics (`GET /admin/github-webhook-fence` +
`GET /app/hook/deliveries`) against PRODUCTION specifically — if
`failing_count` is still >0, the secret still doesn't match; do not
retry the drill again until the fence tile itself shows green,
since the drill's only NEW information over the fence tile is the
label-dispatch write, not the delivery success/fail itself.

## Output

Write results to `/app/e2e-proof/T7-live-R5e/` (new folder, do not
overwrite R2's original `/app/e2e-proof/T7-live/` proof) + update
`LOOP-STATE.md`.
