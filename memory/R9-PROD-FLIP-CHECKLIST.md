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
3. The 48-hour legit-ship warn-window has been reviewed by the
   founder (Preview `ship_via_pr` stayed ON, R2's drill + any organic
   Preview ships during that window logged no unexpected WARN-mode
   `write_guard` trips).
4. **R1a (rollback-on-PR fix) — PARTIALLY SATISFIED (2026-08-30, T2
   round).** 3 of R10's 4 gaps closed + tested (SHA truth via live
   `merge_commit_sha`, no-false-success on PR-lookup failure,
   squash/rebase-safe revert via the real landed SHA + a bounded
   verify-landed poll) — see `/app/e2e-proof/T2/T2_SUMMARY.md`. **Gap
   #4 (ship-branch drift detection before auto-revert) is NOT built**
   — not in this round's literal scope, still open in
   `R10-ROLLBACK-PR-GAP.md` §3 item 4. Do not flip this flag until
   drift detection also lands (or the founder explicitly accepts the
   residual risk).
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
