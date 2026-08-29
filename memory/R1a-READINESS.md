# R1a — Rollback-on-PR Fix: Readiness Brief (queued, NOT built this session)

Per founder's explicit instruction (2026-08-28, First-Experience Wave
message): R1a is **not** this session's deliverable. It is the first
task of the **next** session, its own PR, separate from anything in
this wave. This file confirms scope is ready to start immediately —
no further analysis needed, `R10-ROLLBACK-PR-GAP.md` already did that.

## The 4 fixes (from R10-ROLLBACK-PR-GAP.md §3)

1. **SHA truth** — at rollback time, re-fetch live PR state including
   `merge_commit_sha` (not just `merged`/`state`), and revert THAT SHA,
   not the stale pre-merge `full_sha` currently stored at ship time.
   `get_pr_status()` already calls the right endpoint; it just drops
   the one field that matters — pull it through.
2. **No false success** — `rollback_loop` must not treat "couldn't
   confirm PR state" (network/API failure) the same as "confirmed
   unmerged". A lookup failure must return an honest
   `rollback_status: "rollback_failed"` (not silently run
   `close_and_retract()` and report `"done"`). This is the exact "told
   me it worked but nothing happened" P0 symptom class, one layer up.
3. **Squash/rebase-safe revert** — self-heal the stored SHA via the
   merge webhook (`dispatch_pull_request_webhook` on `action=="closed"
   and merged` should write the real `merge_commit_sha` onto
   `loop_sessions.context.commit.sha`/`full_sha`), and detect ship-
   branch drift (compare current tip SHA / commit count against what
   AUREM pushed at ship time) before reverting — refuse + require
   manual review on drift rather than reverting a partial diff.
4. **Unmerged-PR path unchanged** — `close_and_retract()` for a
   genuinely-unmerged PR (close + delete branch) is already correct;
   do not touch it. Rule 7 (branch auto-delete idempotency) stays as-is.

## The 5 named tests (write these, one per fix + one integration)

- `test_r1a_rollback_uses_live_merge_commit_sha` — merged PR with a
  `merge_commit_sha` DIFFERENT from the pre-merge `full_sha` (the gap
  R10 found existing tests don't cover) → asserts the revert targets
  the live SHA, not the stale one.
- `test_r1a_pr_status_lookup_failure_returns_rollback_failed` — mock
  `get_pr_status()` raising/timing out → asserts response is
  `rollback_status: "rollback_failed"`, NOT `"done"`, and no
  close/retract call is made.
- `test_r1a_merge_webhook_self_heals_stored_sha` — simulate the
  `pull_request` `closed`+`merged` webhook → assert
  `loop_sessions.context.commit.sha/full_sha` gets updated to the real
  `merge_commit_sha`.
- `test_r1a_ship_branch_drift_blocks_auto_rollback` — ship branch tip
  SHA differs from what AUREM recorded at ship time → assert rollback
  refuses automatic revert and surfaces a manual-review state instead
  of reverting a partial diff.
- `test_r1a_unmerged_pr_path_unchanged` — regression guard: unmerged
  PR rollback still does exactly `close_and_retract()`, same as today
  (rule 7 untouched).

## The E2E test (ora-grounding, real GitHub, pre-drilled)

Re-run the same `ora-grounding` merged-PR rollback drill used in R2
(34/34), leaving the repo clean afterward (pre-drill the target SHA,
verify revert lands, verify no dangling branches/PRs). This is the
acceptance gate for R1a itself, separate from R9's own post-flip
verify step.

## R9 checklist updated

`R9-PROD-FLIP-CHECKLIST.md`'s stop-gate now lists R1a as prerequisite
#4, alongside R5e/R8/the 48h warn-window review. `ship_via_pr` stays
Preview-only (Mongo flag OFF in prod) regardless — this file does not
change that; it just documents what "R1a done" needs to mean before
R9 can proceed.

## Status

Scope confirmed ready 2026-08-28. Not started. Next session's first
job, own PR, then stop for founder review before touching anything
else.
