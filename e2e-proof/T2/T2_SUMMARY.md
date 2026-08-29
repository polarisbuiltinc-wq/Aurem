# T2 — Rollback-on-Merged-PR Fix (2026-08-30, founder GO chain)

Scope: `memory/R10-ROLLBACK-PR-GAP.md`'s HIGH-severity gaps. `ship_via_pr`
stays Preview-only / prod flag OFF regardless — this fix makes the path
SAFE for whenever the founder decides to flip it, it does not itself
flip anything.

## What changed (agent-tested, not founder-confirmed)

1. **`services/loop_safety.py::get_pr_status`** — now returns `ok` (was
   collapsed into `merged: False` on any error, indistinguishable from
   a confirmed-unmerged PR) and `merge_commit_sha` (fetched from GitHub
   but previously discarded).
2. **`services/loop_safety.py::dispatch_pull_request_webhook`** — on a
   real `closed`+`merged` PR webhook, self-heals
   `loop_sessions.context.commit.sha/full_sha/merge_commit_sha` to the
   REAL landed commit, keyed by `pr_branch`. Fixes gap #3 (squash/rebase
   safety) at the source.
3. **`services/github_api_writer.py::verify_branch_head`** — new bounded
   poll (default 10 attempts × 6s ≈ 60s, matching the founder's exact
   spec) confirming a just-pushed commit is actually reachable at a
   branch's HEAD. Never raises; returns `{"verified", "attempts",
   "last_sha"}`.
4. **`services/loop_rollback.py::run_rollback`** — after the revert push
   succeeds, calls `verify_branch_head`. Verified → `rollback_status:
   "done"` + `rollback_verified: true` (unchanged UX). NOT verified →
   `rollback_status: "failed"` + `rollback_candidate_sha` +
   `rollback_verified: false` + an explicit, actionable error, and a new
   `ship_rollback_failed` trust event (`services/trust_surface_events.py`)
   — never a false "done".
5. **`routers/loop.py::rollback_loop`** —
   - PR-status lookup failure (`ok: false`) now returns an honest
     `rollback_status: "failed"` immediately (never silently falls into
     `close_and_retract()`, which could wrongly close/delete-branch an
     ALREADY-MERGED PR on a mere network blip).
   - Merged-PR path now reverts `pr_status["merge_commit_sha"]` (the
     REAL landed commit), not the stale pre-merge `full_sha` — this is
     the actual squash/rebase-safety fix.
   - Retry gate relaxed: a previously-failed rollback with NO
     unconfirmed candidate commit can retry immediately; one WITH an
     unconfirmed candidate requires an explicit `force: true` (new
     `LoopRollbackBody.force` field) to avoid a blind duplicate revert.
   - Rule 7 (unmerged PR → close+retract, `auremcto/`-namespace branch
     delete guard) is byte-identical, untouched.

## Explicitly NOT done this round (honest, matches founder's literal T2 ask)
- Ship-branch drift detection (R10 item #4 / R1a-READINESS.md's 4th
  fix — comparing ship-branch tip SHA/commit-count against what AUREM
  pushed at ship time before allowing an automatic revert). Founder's
  own T2 spec this round did not include it; flagged here so it isn't
  silently considered closed. Still open in `R10-ROLLBACK-PR-GAP.md`.
- Recommended merge strategy is NOT yet surfaced in-product (no UI
  copy added this round) — documented here instead: **merge commit**
  ("Create a merge commit") is the safest strategy for AUREM's revert
  model (first-parent assumption holds); squash/rebase are now
  correctly reverted too (via `merge_commit_sha`), but a true merge
  commit remains the simplest, most auditable choice for repos that
  can pick.

## Tests (all named per founder's exact list)
`backend/tests/test_t2_rollback_pr_gap_hardening_2026_08_30.py` (7/7 pass):
- `test_t_sha_updates_to_merge_commit_sha`
- `test_t_sha_no_heal_when_not_merged` (regression guard)
- `test_t_rollback_verifies_then_done`
- `test_t_rollback_blip_reports_failed`
- `test_t_squash_rollback_reverts_real_diff`
- `test_t_rollback_pr_status_unconfirmed_reports_failed_not_silent` (regression guard)
- `test_t_revert_reverse_path_alive`

Updated pre-existing suite for the new `get_pr_status` shape:
`backend/tests/test_rollback_pr_gap_fix.py` (6/6 pass, updated in place).
`backend/tests/test_loop_rollback.py` (5/5 pass, added `stub_verify_ok`
fixture so the pre-existing success-path tests don't need real network
I/O for the new verify step).

**17/17 pass** across all 3 files (`test_t2_rollback...` +
`test_rollback_pr_gap_fix.py` + `test_loop_rollback.py`).

## Regression
Targeted sweep `pytest tests/ -k "loop or rollback or ship"` (excluding
the one pre-existing, unrelated collection error in
`test_ora_chat_deep_research.py`, confirmed pre-existing via `git
stash` A/B): **667 passed, 22 failed, 11 skipped**. All 22 failures
confirmed pre-existing — present verbatim in `backend/test-baseline.txt`,
and the 2 most rollback-adjacent (`test_iter367_rollback_fake_success_fix.py`)
independently re-confirmed via `git stash` A/B (identical 2 failures on
unmodified code, root cause: unrelated `services.pat_vault._decrypt_pat`
API drift, not touched by T2). Zero new regressions.

## Live E2E drill (real GitHub, real revert, real verify — NOT a mock)
`polarisbuiltinc-wq/ora-grounding` (installation `152797252`) is
currently unreachable from this pod (`app_installation_missing` — a
pre-existing infra gap, matches Part-D-E-F's E7 finding, not caused by
this round). Used `TJSNDHU/Aurem` instead (installation `157161705`,
confirmed reachable + active via `GET /admin/github-app-diagnostics`,
the founder's own dev/test repo — same repo the prior T1/R8/X1 rounds
already made real test-ship commits against).

Script: `/app/e2e-proof/T2/drill_script.py`. Result: `/app/e2e-proof/T2/drill_result.json`.

1. Pre-drill HEAD: `6c0ef3fa48...` (`/app/e2e-proof/T2/pre_drill_head.json`),
   zero `auremcto/*` branches (`/app/e2e-proof/T2/branches_before.json`).
2. Real commit via the actual `github_api_writer.commit_files` (the
   same writer every ship path uses): `a317362b4df8...`.
3. `verify_branch_head` (the NEW T2 code) confirmed it landed:
   `{"verified": true, "attempts": 1}`.
4. Real revert via the actual `github_api_writer.revert_commit`:
   `4cb2c0bcb566...`.
5. `verify_branch_head` confirmed the revert landed:
   `{"verified": true, "attempts": 1}`.
6. Post-drill: zero orphan `auremcto/*` branches (this drill used the
   direct-commit path, not ship-via-PR, so none should ever exist —
   confirmed none snuck in). Final HEAD = the revert commit
   `4cb2c0bcb566...` — content-equivalent to the pre-drill state (a
   history-preserving revert, not a force-reset, matching this
   codebase's non-destructive rollback design throughout).

**The "blip → rollback_failed" behavior itself was proven via the unit
tests above (`test_t_rollback_blip_reports_failed`,
`test_t_rollback_pr_status_unconfirmed_reports_failed_not_silent`), not
by forcing an actual live network failure** — a real GitHub outage
can't be manufactured on demand; the unit tests exercise the exact same
production code path (`run_rollback`, `rollback_loop`) with a
deterministic simulated timeout/error, which is the standard, honest
way to prove a failure-handling branch.

## Status: T2 CLOSED (agent-tested, not founder-confirmed)
