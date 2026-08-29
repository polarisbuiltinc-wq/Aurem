# R9 — ship_via_pr Production Flip Checklist (copy-paste executable)

**STOP GATE — do not run any of this until ALL FIVE are true:**
1. R5e passed (real webhook delivered live, `ship_pr_merged`/`ship_pr_closed`
   written by the real route, not the replay fallback — see
   `R5e-VERIFY-PLAN.md`).
2. **R8 — NOW COMPLETE (2026-08-30, M1/M2 round).** Fence-emit rate
   2/5 raw / 67% effective (excl. 1 correct no-fix + 1 infra-timeout);
   low-confidence retest not-suppressed with honest infra caveat;
   final cost baseline $0.0191/msg for $9/Pro tiering. See
   `/app/e2e-proof/M1-M2/M1_M2_REPORT.md`. Real-model smoke +
   N1/K2-K9 spot-checks green per prior rounds + this one.
3. **The 48-hour legit-ship warn-window — data corrected & computed
   (2026-08-30, R9 unblock round), founder review pending.** Prior round mistakenly read production QA
   "Gate Parity" (loop-start denial rates) — the WRONG source. Correct
   source: `guard_config`/`guardrail_events` (Wave-1 path-guard,
   `services/write_guard.py`, `admin_ops_config.py::/admin/guardrails`)
   + actual ship-write volume through the guarded choke point
   (`services/github_api_writer.commit_files`), in Preview, since
   `ship_via_pr` was toggled ON.
   - `guard_config.path_guard` doc: absent → **mode defaults to
     `"warn"`** (safe default, confirmed by reading `write_guard.py`).
   - Organic legit ship writes in the last 48h: **1**
     (`loop_7014cd440aaf4c`, the pre-existing P6 drill,
     2026-08-27T20:32:40Z). Below the 5-write bar on its own.
   - Per instruction, filled the window with **4 more controlled,
     clean drill writes** through the SAME real choke point (real
     GitHub API, `TJSNDHU/Aurem`, cleaned up after) —
     `/app/e2e-proof/R9-unblock/warn-window/`.
   - Warn events (`GW_WARN_*`) in the 48h window: **0**, both before
     and after the 4 fill writes.
   - **Positive control** (to prove the 0-warn result is a true
     negative, not a broken detector): called `check_write_paths`
     directly with `.env` in the path list → correctly fired
     `GW_WARN_PATH` immediately, test event deleted after (not
     counted in the 48h stats).
   - **Verdict: CLEAN (5 total clean writes — 1 organic + 4
     drill-filled — AND 0 warn events; guard independently confirmed
     functional via positive control).** Full data:
     `/app/e2e-proof/R9-unblock/warn-window/WARN_WINDOW_SUMMARY.md`.
4. **R1a (rollback-on-PR fix) — FULLY SATISFIED (2026-08-30, drift
   detection round).** All 4 of R10's gaps now closed + tested:
   SHA truth via live `merge_commit_sha`, no-false-success on
   PR-lookup failure, squash/rebase-safe revert via the real landed
   SHA + a bounded verify-landed poll (T2 round), AND **gap #4 —
   ship-branch drift detection before auto-revert/auto-delete — now
   built and live-drilled.** `services/github_api_writer.check_branch_drift`
   compares the branch's LIVE head against `expected_branch_head_sha`
   (recorded at ship time, `services/loop_engine.py`) before every
   rollback (both the always-on direct-commit revert path AND the
   unmerged-PR close+delete path); a drift blocks with
   `rollback_status="drift_detected"` until the caller resends with
   `acknowledge_drift=true`. Tests: `test_t_drift_detected_blocks_rollback`,
   `test_t_drift_acknowledge_proceeds`, `test_t_drift_unmerged_branch`,
   `test_t_no_drift_normal_rollback` (+2 more) —
   `tests/test_drift_detection_2026_08_30.py`, 6/6 pass. Live drill
   against a real repo (TJSNDHU/Aurem): ship → simulated 3rd-party
   push → drift detected live → acknowledged → EXPECTED commit
   reverted (not the drifted head) → repo left clean. See
   `/app/e2e-proof/drift/DRIFT_SUMMARY.md`. **Admin visibility added
   (2026-08-30, follow-up round)**: `GET /admin/drift-alerts` +
   "Drift-Blocked Rollbacks" tile on `AdminSystemHealth`, read-only,
   last 24h count + expandable per-event detail. See
   `/app/e2e-proof/drift-alert/DRIFT_ALERT_SUMMARY.md`.
5. **H3 — loop repo pinning — SATISFIED (2026-08-30, founder follow-up
   GO).** Both write paths now pin `{owner, repo, branch,
   installation_id}` at start and re-assert the LIVE binding still
   matches right before the real GitHub write, aborting with an
   explicit user-visible error on mismatch (never silently re-target):
   `services/loop_engine.py::confirm_ship` (loop pipeline) and
   `routers/cto_projects.py::_run_task_via_api` (direct task-submit
   ship path). Tests: `t_loop_repos_pinned`,
   `t_loop_pin_blocks_stray_write`, `t_loop_pin_matches_context`
   (`tests/test_h3_loop_repo_pin_2026_08_30.py`, 3/3 pass);
   `t_direct_ship_pin_mismatch_aborts`,
   `t_direct_ship_pin_matches_context`,
   `t_direct_ship_clears_not_connected`
   (`tests/test_h3_b1_direct_task_pin_2026_08_30.py`, 3/3 pass). Proof:
   `/app/e2e-proof/H3/`. See `REPORT-x1-crossproject.md` (this
   round's addendum) for the full writeup.

If any of the 5 is missing, **do not proceed** — log
`R9 PENDING-FOUNDER` with the exact missing item and stop.

## Pre-flight (2 min)

1. `GET /admin/github-webhook-fence` (production) → `ok: true`.
2. `GET /admin/loop-metrics` (production) → confirm ship-via-PR unit
   tests are still green in the latest deploy
   (`tests/test_overnight_t7_ship_via_pr.py`, 12/12 — this is a code
   check, not a live check, but re-confirm nothing regressed it since
   R2).
3. Confirm no in-flight PRs are open on any user repo under the
   `auremcto/` branch prefix (`GET .../branches` per connected repo,
   or just check `ship_pr_events` for any `status: "open"` rows older
   than a few hours) — flipping mid-flight is safe by design (the
   flag only affects NEW ships), but confirm for a clean rollout
   story.

## The flip (1 config write, no code deploy, no migration)

4. On PRODUCTION, call the existing admin endpoint (same one R5's
   overnight round used in Preview):
   `POST /admin/feature-flags` with `{"flag": "ship_via_pr", "enabled": true}`
   — this is a single Mongo `feature_flags` collection row, no schema
   change, no deploy required (the flag is read live by
   `services/loop_engine.py` on every ship).
5. Confirm: `GET /admin/feature-flags` → `ship_via_pr.enabled == true`
   on production.

## Rollback (if anything looks wrong post-flip)

6. `POST /admin/feature-flags` with `{"flag": "ship_via_pr", "enabled": false}`.
   That's it — the next ship after this call reverts to direct-push
   (the pre-existing, always-available path). **No data migration is
   ever needed**: any PR already opened under the flag stays exactly
   as it is on GitHub (open/merged/closed) — AUREM does not need to
   touch it. An open-but-abandoned PR is harmless and can be closed
   manually on GitHub at any time; it was never "live" until merged.

## Post-flip verify (5-10 min, one real user)

7. Trigger one real ship (any connected repo, small low-risk fix) for
   ONE test user/project on production with the flag now on.
8. Confirm the PR lifecycle end-to-end on GitHub itself: **Open** →
   (review/approve on GitHub, or "Approve the fix" in-app if that
   path is wired) → **Merged** → the in-app status chip flips to
   **Live** (driven by the now-fixed webhook, R5e).
9. Confirm `ship_pr_events` on production shows all 3 rows
   (`ship_pr_opened`/`ship_pr_merged`) for that one real PR number.
10. Watch `GET /admin/github-webhook-fence` for the next 24h (or
    however long is practical) for `failing_count > 0` — if it
    regresses, roll back (step 6) and re-open R5 investigation.

## Report back (after flip)

- Flag state: `ship_via_pr` = ON (production), timestamp, who flipped
  (this agent, on founder's explicit "GO").
- The one real PR's URL/number + its Open→Merged→Live timeline.
- Any WARN-mode `write_guard` trips observed in the first real ship.
