# R1a gap#4 — ship-branch drift detection (2026-08-30)

Agent-tested, NOT founder-confirmed. Closes the last open R10 rollback
gap (`R10-ROLLBACK-PR-GAP.md` §3 item 4).

## What was built
- `services/loop_engine.py::confirm_ship` — records
  `expected_branch_head_sha` (== the ship's own `full_sha`, since that
  push just became the target branch's tip) onto `context.commit` at
  ship time, for BOTH the always-on direct-commit path and the
  ship-via-PR throwaway-branch path.
- `services/github_api_writer.py::check_branch_drift(owner, repo,
  branch, expected_sha, token)` — a single live head lookup (no
  poll/wait, unlike `verify_branch_head` which waits for a push to
  land — this runs BEFORE a rollback starts, nothing to wait for).
  Fail-closed: a lookup error is treated as drifted (can't prove no
  drift → must not proceed blind).
- `routers/loop.py::rollback_loop` — drift-checked in BOTH branches
  before doing anything destructive:
  1. **Unmerged-PR case** (close+delete a throwaway `auremcto/`
     branch): checked against `pr_branch`. Drift = someone pushed a
     NEW commit to that branch — auto-deleting it now would destroy
     that push.
  2. **Direct-commit case** (always-on revert path): checked against
     the project's base `branch`. Drift = the branch moved since ship
     (another commit landed) — revert of the SPECIFIC recorded sha
     still targets that commit's own diff (git revert, not a reset),
     but the user must consciously acknowledge the branch changed.
  3. Merged-PR fallthrough (real `merge_commit_sha`, already
     live-fetched by T2): explicitly OUT of this round's drift check —
     that path's staleness bug was already a different fix; a second,
     broader drift concept there wasn't requested and would be scope
     creep.
  Sessions shipped BEFORE this feature existed have no
  `expected_branch_head_sha` — drift is simply skipped (nothing to
  compare), never blocked on missing data.
- On a detected+unacknowledged drift: `rollback_status="drift_detected"`
  persisted (with `rollback_drift={branch, expected, current}`), a
  `ship_rollback_drift_detected` trust event logged, response body
  carries the founder's exact required copy ("Branch has changed
  since the fix was applied. Current: {sha}. Expected: {sha}...") plus
  a `drift` object. Resending with `acknowledge_drift=true` skips the
  check and proceeds exactly as before this feature existed — for the
  direct-commit path, `commit_sha` passed to the revert is unchanged
  (still the originally-recorded `full_sha`), so "acknowledge and
  proceed" always targets the EXPECTED commit, never the drifted head.

## Tests — 6/6 pass (`tests/test_drift_detection_2026_08_30.py`)
- `test_t_drift_detected_blocks_rollback` — direct-commit, branch
  moved → blocked, `rollback_status="drift_detected"`, zero background
  tasks queued.
- `test_t_drift_acknowledge_proceeds` — same drift, `acknowledge_drift=true`
  → proceeds WITHOUT even re-checking drift, `commit_sha` == the
  originally-recorded expected sha (not a drifted head).
- `test_t_drift_unmerged_branch` — someone pushed a NEW commit to the
  `auremcto/` ship branch itself → close+retract blocked, never called.
- `test_t_no_drift_normal_rollback` — expected == current → proceeds
  exactly as before, no warning.
- `test_drift_skipped_when_no_expected_sha_recorded` — old-shape
  session (pre-this-feature) → drift check skipped entirely, normal
  queued rollback (matches pre-existing `test_t_revert_reverse_path_alive`
  behavior, which still passes unmodified — confirmed together, 13/13
  green including all pre-existing T2 tests).
- `test_ship_rollback_drift_detected_is_registered_event_kind`.

## Live E2E drill (real GitHub, real drift, real revert — not a mock)
`TJSNDHU/Aurem`, installation `157161705` (same reachable repo T2's
own drill already validated; `ora-grounding`/`152797252` remains
unreachable, pre-existing infra gap, unrelated). Script:
`/app/e2e-proof/drift/drill_script.py`. Result:
`/app/e2e-proof/drift/drill_result.json`.

1. Shipped a real commit (`1e78fb1...`) → `expected_branch_head_sha`.
2. Simulated "someone else pushed a different commit" — a second real
   commit (`198408f...`) landed on `main`, authored as "Someone Else".
3. `check_branch_drift` (live, real GitHub) correctly detected
   `drifted: true, current_sha: 198408f...` against the recorded
   expected `1e78fb1...` — the exact scenario a real rollback attempt
   would now block on.
4. Acknowledged → reverted the EXPECTED commit specifically
   (`revert_commit(commit_sha=1e78fb1...)` → new commit `e32cf4d...`),
   NOT the drifted head — proven by step 5.
5. Post-revert re-check: `check_branch_drift` against the new head
   (`e32cf4d...`) → `drifted: false` — confirms the revert landed
   cleanly and the "someone else" file was untouched by it (still
   present at that point, confirmed via `fetch_file`).
6. Cleaned up the simulated drift-push marker with a separate,
   explicit cleanup commit (`de7fb19...`) — **not** part of the
   rollback logic itself, just drill hygiene. Post-drill live check:
   both `R1A_DRIFT_DRILL_SHIP.md` and `R1A_DRIFT_DRILL_SOMEONE_ELSE.md`
   confirmed absent from `main` HEAD. Repo left clean.

## R9 checklist updated
`/app/memory/R9-PROD-FLIP-CHECKLIST.md` item 4: **PARTIALLY →
FULLY SATISFIED**. Remaining R9 stop-gate items (unchanged, not this
round's scope): item 1 (R5e webhook — founder's own production
action) and item 3 (48h warn-window — founder review). **No R9 flip
performed or implied by this round.**

## STATUS: R1a gap#4 CLOSED (agent-tested, not founder-confirmed).
