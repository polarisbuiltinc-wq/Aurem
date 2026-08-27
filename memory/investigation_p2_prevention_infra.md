# P2 — Prevention Infrastructure (2026-08-27)

Founder approved "Show the Outcome, Never the Engine" P2 with 5 locked
decisions (Q1-Q5). All 4 P2 items + the MessageBubble copy fix are
DONE and testing_agent-verified. Report: `/app/test_reports/iteration_p2_prevention_infra_2026_08_27.json`.

## Item 1 — CI machinery-leak-copy linter
- `backend/scripts/ci_check_machinery_leak_copy.py` (AST-based for
  loop_engine.py narration + JSON catalog, regex-based for JSX —
  documented why JS has no ast stdlib equivalent).
- Fixture-fail + real-pass proof added via `main()` entrypoint (not
  just internal helpers): `TestCiLeakLinterScriptLevelFixtureProof`
  in `backend/tests/test_iter2026_08_27_ci_leak_linter.py` — 8/8 pass.
- Wired into `.github/workflows/ci.yml` (line 319).
- "Vanguard" deliberately NOT banned — public product feature name.

## Item 2 — Promptfoo scenarios
- 3 new scenarios (6a explain-plain, 6b fix-never-blames-user,
  6c/6d context-retention) added to `qa/simulated-user/promptfooconfig.yaml`.
- Actually RAN (not just YAML-validated): 18/19 passed. All 4 new
  P2 scenarios + the pre-existing P0i canary passed. The 1 failure
  ("1f deploy intent") is PRE-EXISTING, unrelated to P2/P1/P0 code —
  root cause looks like the QA-seeded fake `github_repo=probe-a`
  triggering a probe-path-only scoping quirk, not a real regression.
- Cost: not directly recorded (qa-probe path doesn't route through
  `customer_cost_tracker`); ~19 real LLM calls, run took 49s.

## Item 3 — Ship-E2E real push (BLOCKED, documented)
- `backend/tests/test_iter2026_08_27_ship_e2e_real_push.py` — real
  `get_repo_token_or_error()` + `resolve_git_identity()` +
  `commit_files()` path against drill repo
  `polarisbuiltinc-wq/aurem-rollback-testbed` (user `test_admin_001`).
- Structurally complete, wired into CI (`.github/workflows/ci.yml`
  line 341). Currently SKIPS with reason `app_installation_missing`.
- **Founder action to unblock**: install the AUREM GitHub App on
  `polarisbuiltinc-wq/aurem-rollback-testbed`. Zero further code
  work needed — the test will flip green automatically.

## Item 4 — Audit spine + 24h alert (reused existing infra, no new collection)
- `services/audit_log.py::record_turn()` (`ora_audit` collection) —
  `chat.py`'s existing call site (line ~3287) now passes
  `extra={leak_stripped, length_capped, recall_candidate, council_recalled_count}`.
- `services/loop_audit_log.py` — new `KIND_INTERNAL_FAULT` constant;
  `loop_engine.py::_fail_ship()` now logs a `loop_run_log` row when
  the classified error is `INTERNAL_CALL_ERROR` (AUREM's fault, not
  the user's). Verified negative case: `SCHEMA_MISMATCH` does NOT log.
- `services/leak_alert_cron.py` (new) — every 30 min (configurable
  `LEAK_ALERT_INTERVAL_SEC`), counts `ora_audit` rows with
  `extra.leak_stripped=True` in the trailing 24h; if count exceeds
  `LEAK_ALERT_THRESHOLD` (default 5), fires the existing G10
  founder-alert channel (`services/founder_alerts.py`, 6h dedup).
  Wired into `main.py` via `_supervise()`, same pattern as
  `slo_alert_cron`/`cost_revenue_alert_cron`.
- Query examples (no new endpoint — direct Mongo, admin/founder use):
  - Leaks this week: `db.ora_audit.count_documents({"extra.leak_stripped": True, "timestamp": {"$gte": <7d ago ISO>}})`
  - Mis-blames (internal faults): `db.loop_run_log.count_documents({"kind": "internal_fault_not_user", "created_at": {"$gte": <7d ago>}})`
  - Recalls: `db.ora_audit.count_documents({"extra.recall_candidate": True, "timestamp": {"$gte": <7d ago ISO>}})`
- Tests: `backend/tests/test_iter2026_08_27_p2_audit_spine.py` — 7/7 pass.

## MessageBubble copy fix (Q4)
- `frontend/src/components/MessageBubble.jsx` — "5-adviser council ·
  chairman verdict" → "· double-checked by a second reviewer".
  Verified via grep (old string gone, new string present) + linter
  passing on real codebase + `test_jsx_child_text_with_banned_token_is_caught`
  regression.

## Regression status
- All P2 new tests green (23 total across the 4 files: 8+7+1+ existing
  p1_egress/show_outcome already counted separately = 23 from earlier
  batch, +7 audit-spine +1 ship-e2e = 31 total new P2-era tests).
- No new regressions from P2 changes — confirmed via git-stash
  baseline comparison AND independent testing_agent re-verification.
- Pre-existing failures (unrelated to P2, confirmed by both main
  agent and testing_agent with matching signatures): chat padding
  test, 4 aggression-chat sub-tests, brain-replay context test,
  house-rules content test, phase2c happy-path test, response-
  confidence-gate-swallow test, ChatPanel AgentStatusBar import test,
  and a PAT-auth-deprecated cascade in test_aurem_p0_bugs.py (9 tests)
  + 2 admin-endpoint-registration tests in test_iter70 (same root
  cause family — endpoints/paths not wired, unrelated to any P0/P1/P2
  code touched in this initiative).

## Next: STOP. Do not start P3.
P3 ("ORA remembers this" indicator) requires founder review of P2 +
founder approval, per governing rules. Not started.
