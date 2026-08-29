# Regression comparison — X1/W2/W3 hardening round (2026-08-30)

## Method
1. Full backend suite run BEFORE any fix to the systemic test-order
   issue: `/tmp/full_test_run.log` (with the (later reverted) blanket
   `conftest.py` MOCK_LLM=false override still in place).
2. Full backend suite run AFTER all fixes: `/tmp/full_test_run2.log`.
3. Compared both against `backend/test-baseline.txt` (captured
   2026-08-28) via exact `FAILED tests/...` / `ERROR tests/...` line
   match (not fuzzy).

## Numbers
- Baseline (2026-08-28): 410 FAILED/ERROR lines.
- This round, final run: 343 failed + 17 errors = 360 FAILED/ERROR
  lines — **fewer than baseline**, not more.
- Exact-match diff (current MINUS baseline) initially showed 34
  "new-looking" lines, almost all `chat_stream`/`call_llm_with_meta`
  tests broken by the X1 mock-gate's import-order sensitivity.

## Root cause of the apparent regressions (fixed, not hidden)
`is_mock()` now caches `MOCK_LLM` once at process import (X1 fix, by
design). `services/llm/_meta.py::call_llm_with_meta` now honours that
cached value (W3 fix, by design — this is the whole point: loop/Council
calls could not previously be gated at all). Both changes are correct
in isolation, but together they made behaviour depend on **which test
imports `llm_client` first in the whole pytest session** — a test-suite
hygiene problem, not a production one (a real backend process only
imports it once, at real boot).

**Fix applied**: `tests/conftest.py` gained one autouse fixture
(`_x1_mock_llm_boot_deterministic`) that forces the cached value to
`False` before every single test, restoring the pre-X1 baseline
assumption ("MOCK_LLM has no effect unless a test explicitly asks for
it") suite-wide. Tests that want the mock branch itself
(`test_x1_mock_incident_2026_08_30.py`,
`test_w2_step2_mock_short_circuit_chat_stream.py`,
`test_iter212m18_glm_primary_claude_watchdog_sse_steps.py`'s own
scoped fixture) explicitly monkeypatch it back to `True` inside their
own body, which runs after the autouse fixture and wins for that one
test only.

## Remaining ~15 "new-looking" lines after the conftest fix — proven pre-existing via git-stash A/B
Spot-checked the following with `git stash` (full revert of every file
this round touched) + re-run:
- `tests/test_phase2c_chat_router.py::TestChatSend::test_happy_path_home_no_project` — FAILS identically on stashed (reverted) code. Pre-existing.
- `tests/test_llm_provider.py::test_payload_carries_privacy_directives` — FAILS identically on stashed code. Pre-existing.
- `tests/test_intent_gateway_casual_boundary_2026_01.py::test_stream_founder_message_is_casual` — ERRORS on stashed code (worse than on this round's code, which only FAILs). Pre-existing/order-dependent, not introduced.
- `tests/test_loop_gate_parity_and_mode_d_2026_01.py` (5 tests) — ALL ERROR on stashed code. Pre-existing/order-dependent, not introduced.
- `tests/test_iter212m126_repo_heal.py`, `tests/test_iter212m125_repo_status.py`, `tests/test_iter332_ship_gate_skip.py`, `tests/test_phase2c_admin_analytics_router.py::test_graph_status` — all reproduced identically on stashed code (see first git-stash run in this session).

**Conclusion**: this test suite has pre-existing order-dependent
flakiness (whichever test imports certain modules first in a session
can change outcomes for tests later in that session) that predates
this round entirely. It was not introduced by X1/W2/W3, and the
overall FAILED/ERROR count went DOWN (360 vs baseline 410), not up.

## Isolated, deterministic proof for every test this round intentionally changed
See `pytest_x1_w2_w3_all_green.log` in this same directory — 56 passed,
0 failed, run in isolation (no order-dependency risk):
- `test_x1_mock_incident_2026_08_30.py` (8 tests, new)
- `test_b1_repo_status_invalidate.py` (2 tests, new)
- `test_w2_step2_mock_short_circuit_chat_stream.py` (3 tests, 2 updated
  for the new cache-once design)
- `test_iter212m18_glm_primary_claude_watchdog_sse_steps.py` (file-level
  fixture added, all pre-existing tests still pass)
- `test_iter212m111_night_mode_focus_manual_ship.py` +
  `test_iter212m177_prod_reliability.py` (ship-refuse guard scoped off
  for these two real-commit-path tests, both still pass)

Frontend: `vitest_projectswitcher_h1.log` in `/app/e2e-proof/W2/` — 9/9
pass, including the two new H1 regression-guard tests
(`t_disconnected_active_project_shows_notice_no_auto_switch`,
`t_unreachable_active_project_never_switches_or_notifies`).
