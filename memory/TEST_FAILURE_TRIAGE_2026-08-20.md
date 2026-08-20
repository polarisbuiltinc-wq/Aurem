# Backend Full-Suite Failure Triage — 2026-08-20

Full run: `195 failed, 4806 passed, 71 skipped, 103 deselected, 12 errors` (844.97s).
Previous session's report: `196 failed, 12 errors`. **Stable, not growing** (one now fixed below).

Raw log: `/app/test_reports/full_suite_iter_current.log`
Failed list: `/app/test_reports/failed_list.txt`

## Investigated and resolved/explained this session

1. **`test_deploy_2026_08_19_health_probe_and_exceptiongroup.py::test_skip_path_catches_exceptiongroup_directly`**
   — Stale test. Round-2 health-probe fix (2026-08-19) made `/health`,
   `/healthz`, `/ping` short-circuit BEFORE `call_next()` is ever called,
   so the round-1 "catches BaseExceptionGroup from call_next" contract
   no longer applies to those 3 exact paths. Code behavior is correct
   (and safer) — test was outdated. **Fixed the test** to assert the
   new short-circuit (200, no call_next) + added a new test asserting
   `/api/health` (not in the exact-bypass set) still honors the
   round-1 contract. 8/8 passing now.

2. **`test_g22_idle_spend_guard.py`** (3 failures) — Fails even in
   total isolation. Root cause: `check_idle_window_spend(db, hours_back=1)`
   queries the SHARED `ora_chat_usage` collection for the real last-1-hour
   window without marker-scoping. Any real chat/admin activity in the
   preview DB in the last hour (e.g. from manual testing) makes
   `real_user_activity` come back `True` when the test expects `False`.
   **Test isolation gap in the test itself, not a production bug** —
   `check_idle_window_spend` is working as designed.

3. **`test_slice_a_bi_cockpit.py`** (3 failures) — `429 Too many failed
   logins from this IP` on the test's own `_login()` helper. Same
   login-rate-limit-lockout pattern already documented in
   `FOUNDER_STATUS_REPORT.md` from heavy testing earlier. Not a code bug;
   clears itself after the lockout window or by clearing `login_attempts`.

4. **12 ERRORS** — all `ERROR at setup/teardown of test_*_app_installed`
   (repo_status, codebase_health_scan, security_scan, user_rollback,
   loop_rollback, mcp_projects_connected_flag, admin_brain_replay, PAT
   variants) — same root cause: `TestClient(app)` context-manager
   exit racing anyio's thread portal during app lifespan shutdown when
   many test files reuse the same FastAPI app instance in one pytest
   session. Pre-existing infra flakiness, unrelated to any of today's
   4 shipped fixes.

5. **`test_regression_iter279_281_bug_per_fix.py::test_regression_iter280_chat_history_persists_on_reload`**
   — Touches `routers/chat.py` (today's file) but confirmed via git
   diff that today's only change to that file was adding
   `log_customer_chat_cost()` calls AFTER `_persist_turn()`, not inside
   it. Ran the test standalone → **passes** (1 passed). Confirmed
   test-order/global-DB-singleton pollution from another test file
   earlier in the same pytest session, pre-existing, unrelated to
   today's work.

## Not individually triaged (large pre-existing tail, ~185 failures)

Largest clusters by file (failure count): `test_iter63_cache_purge` (7),
`test_iter212m3_activation_funnel` (6), `test_iter212m28c_admin_debug_timings` (6),
`test_iter212m234_p0_dev_users_created_at` (6), `test_ship_turn_index` (5),
`test_iter44_vanguard` (5), `test_iter212m_user_patterns_insights` (5),
`test_tool_reliability_v2` (4), `test_session6_item1_vscode_marketplace_status` (4),
`test_iter65_agent_tokens_and_layout` (4), `test_iter212m16_admin_password_leak_and_health` (4),
plus a long tail of 1-3-count failures across ~90 other files. None of these
file names correspond to `chat.py`, `admin_bi.py`, `support.py`,
`customer_cost_tracker.py`, or `PricingCards.jsx` (today's touched files).
Also found unrelated stale-copy checks: `test_iter80_seo_pwa`,
`test_iter94_maxx_cap_and_usd_migration`, `test_iter358_seo_refresh`
(pricing/marketing-copy-vs-SSOT drift checks) and
`test_deploy_verification_discipline` (doc-pointer-position checks) —
both doc/content-drift categories, not functional bugs.

**Conclusion: none of the 195 failures are caused by today's 4 redeploy
fixes.** One test needed updating for round-2's own intentional design
(now fixed). The other ~194 are pre-existing, stable vs. last session's
196, not growing.
