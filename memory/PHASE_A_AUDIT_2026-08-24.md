# Phase A — Testing-Trust Audit (2026-08-24)

Static/targeted analysis only. No full-suite run (disk-pressure history). No code changes made — investigation/report only, per founder's explicit instruction this session.

## 0. Pre-step: disk was at 100% (blocking everything, incl. git)
`/app`, `/root`, `/var/log`, `/data/db` share one 9.8GB ext4 device (`/dev/nvme0n3`). Found it at 100% used; `git status` was throwing `index.lock write error: Out of diskspace`. Reclaimed via the existing documented runbook (`memory/DISK_PRESSURE.md`) + extra safe items: deleted `/root/.npm`, rotated mongo logs, `frontend/dist`, `.vite` cache, `.ruff_cache`, yarn cache (1.2G), pip cache, `__pycache__`/`.pytest_cache`, `/app/vscode-extension/node_modules` (126M, rebuildable), and ~1.4G of stale `/tmp/restore_restore_scratch_*.gz` + old mongo backup `.gz` scratch files. Result: 100%→95% used, 498M free. Still tight (Mongo data 6.8G is the real long-term driver, needs founder OK to touch) — do not run full-suite pytest without rechecking `df -h /app` first.

## 1. Is the suite coherent or scattered? — quantified

- **596** files matching `test_*.py` under `backend/tests/` (flat dir, no subdirectory hierarchy) + small extra dirs: `backend/tests/live/` (2), `backend/tests/reasoning/` (4), `frontend/tests/` (1), `tests-aurem/` (1).
- **5,357** test functions counted via real AST walk (services/test_style_analyzer.py's `analyze_suite()`, run directly — not an estimate).
- File-naming convention is overwhelmingly `test_iter<N>...py` / `test_iter<N>m<M>...py` / `test_regression_iter<N>...py`, with iteration numbers observed from single digits up past 390+. This is strong, direct evidence of **organic accumulation across hundreds of individual work sessions**, not a designed test architecture with planned modules/suites.
- Existing self-governance already exists (not zero-governance): `backend/tests/conftest.py` maintains 4 quarantine lists —
  - `legacy_quarantine.txt`: 11 active nodeids (contract-drift, deferred)
  - `legacy_removed_features.txt`: 81 active nodeids (asserts on deleted surface)
  - `legacy_deferred_db_fixtures.txt`: 8 active nodeids
  - `live_env_quarantine.txt`: 39 active nodeids (live-HTTP tests, auto-skip in CI when `{BASE}/api/health` unreachable)
  - **CONFIRMED gap in that mechanism:** `test_revoked_repo_banner_2026_08_20.py` IS listed in `live_env_quarantine.txt`, but it reads `os.environ["REACT_APP_BACKEND_URL"]` directly at **module level** (not inside a test function). That raises `KeyError` during **collection**, before `pytest_collection_modifyitems` ever runs — so the quarantine skip-marker can't apply in time, and when this file is collected alongside others, it aborts the **entire pytest run** with `Interrupted: 1 error during collection`. This is a real, reproduced mechanism for how a single bad file can make a "broad run" look totally broken/unrunnable, independent of the actual health of the other 595 files.

**Verdict on coherence:** LIKELY → practically CONFIRMED **scattered/accumulated**, not a designed suite — but not ungoverned either; prior sessions already built real (if patchwork) quarantine/classification tooling on top of the accumulation.

## 2. Live-request-style vs in-process style — quantified (static grep, file-level)

| Style | Files | % of 596 |
|---|---:|---:|
| Uses `TestClient` (in-process, FastAPI app instance) | 38 | 6.4% |
| References `REACT_APP_BACKEND_URL` / live-base-URL / `live_env` marker | 60 | 10.1% |
| Uses `pymongo`/`MongoClient` directly (real Mongo, not fake) | 12 | 2.0% |
| Uses `unittest.TestCase` | 11 | 1.8% |
| Neither `TestClient` nor `requests`/`httpx` calls (direct-import/unit or source-grep) | 444 | 74.5% |

Separately, running the founder's own AST-based classifier (`services/test_style_analyzer.py::analyze_suite()`, deterministic, no LLM) across all 5,357 tests gives the **real** execution-evidence split:

| Kind | Count | % |
|---|---:|---:|
| BEHAVIOURAL (awaits / asyncio.run / calls an imported prod symbol) | 2,788 | 52.0% |
| STATIC_GREP (reads a source file, asserts on the string) | 1,087 | 20.3% |
| UNKNOWN (no clear signal, e.g. asserts a hardcoded constant) | 1,464 | 27.3% |
| HYBRID (both) | 18 | 0.3% |
| **weak_p0** (STATIC_GREP test whose name mentions a P0-security concern — e.g. `verifier_verdict`, `pi_shield`, `bulkhead`, `pat_leak`) | 14 | — |

**Coverage-attribution rule (CONFIRMED by direct test):** a test that only makes HTTP calls to a separately-running process (the live `TestClient`-free / `requests`/`httpx`-to-URL style, ~60 files) does **not** feed `pytest-cov`'s source-line tracer, because that tracer only instruments the same Python process pytest is running in. This is exactly why Phase 2c had to write in-process `TestClient`/direct-import tests to move coverage numbers — a live-request test can be a completely valid behavioral/integration check while contributing 0% to the coverage percentage. **This is a coverage-attribution fact, not a test-quality judgment** — a live test passing is still real evidence the feature works end-to-end; it just can't be cited as "N% covered."

## 3. `testing_agent` — what is mechanically confirmable

From this agent's vantage point only (no platform-internals access):
- It is invoked as a separate tool/agent call from the main session and returns a JSON file to `/app/test_reports/iteration_*.json` with a consistent schema (`summary`, `backend_issues`, `frontend_issues`, `test_report_links`, `success_rate`, `retest_needed`, `rca_of_the_issue`, etc.).
- It demonstrably **creates its own new test artifacts** on disk (e.g. `backend/tests/live/test_phase2c_chat_live_smoke.py`) and references concrete, checkable evidence (exact commit SHAs, exact task IDs, exact test counts) rather than generic assurances — i.e., its claims are falsifiable and I have verified several after the fact.
- It is **not infallible / not purely independent ground truth**: in the `cto_projects.py` wave it made an incorrect attribution (blamed the new in-memory-DB test suite for live-Mongo pollution actually caused by a pre-existing, untouched file). I caught this via `grep` after the fact. This proves it does form its own conclusions (not just echoing the main agent's framing) but those conclusions still require the same evidence-based verification as any other output.
- **UNCERTAIN / not determinable from here:** whether it runs on a different model/instance than the main agent, whether it has any shared context/bias with the main agent's session, or the exact isolation boundary. I cannot confirm or deny platform-level independence beyond the behavioral evidence above.

## 4. Root cause — QA dashboard "0 tests analysed" — CONFIRMED

Two independent, compounding causes, both located and reproduced in code (not run against production, but the mechanism is deterministic given `.dockerignore`):

1. **CONFIRMED via `.dockerignore` (line 96): `backend/tests` is explicitly excluded from the production Docker build context.** So in Production, `backend/tests/` does not exist on disk.
2. **CONFIRMED via code read (`backend/services/test_style_analyzer.py::analyze_suite()` + `backend/routers/admin_qa.py::_harvest_test_style_ratio()`):**
   - `analyze_suite()` checks `if not os.path.isdir(tests_dir): return {"ok": False, "reason": "tests_dir_missing"}` — no exception raised, just an `ok: False` dict.
   - `_harvest_test_style_ratio()` wraps the call in `try/except` but **never checks `r.get("ok")`** — it just does `counts = r.get("counts") or {}` (empty) and `total = r.get("total_tests") or 0` (0), then unconditionally returns `{"available": True, "total_tests": 0, "ratio_pct": 0.0, "passes_threshold": True, ...}`.
   - The frontend (`AdminQADashboard.jsx` L204) renders `style.available ? "${style.total_tests} tests analysed..." : "analyser unavailable"` — since `available` is `True` (the bug), it shows **"0 tests analysed"** instead of the honest "analyser unavailable" the code clearly intends for a real failure.
3. **This is a real, fixable bug** (the `ok` flag from `analyze_suite()` is silently dropped) — not touched this session per the "report first, fix after sign-off" instruction.

## 5. Root cause — 5 CI statuses "UNKNOWN" — CONFIRMED with a live GitHub API call

- `backend/services/qa_matrix.py::_JOB_NAMES_WE_CARE_ABOUT` is a hardcoded tuple of 5 **job-id** strings: `bug-fix-discipline`, `invariants`, `test-style-guard`, `frontend-vitest`, `visual-regression`.
- `_harvest_ci_status()` fetches the real GitHub Actions API and matches `j.get("name")` (the API's job **display name**) against that tuple. If a job isn't found in the fetched run, it's defaulted to `{"status": "unknown", ...}` — exactly 5 keys, matching the founder's observed "five CI statuses UNKNOWN" 1:1.
- **Live-verified** (real call, using the already-configured `GITHUB_ACTIONS_TOKEN`/`GITHUB_REPO` in `backend/.env`, repo `polarisbuiltinc-wq/Aurem`, latest `quality-gate` run `32615685645`, 2026-08-23, real conclusion=`failure`): the API returns job **names**, not ids — e.g. `'Fitness-function invariants (always green on main)'`, `'PR must include a test change (or explicit override)'`, `'Static-grep threshold on new tests'` — because every job in `.github/workflows/quality-gate.yml` sets an explicit `name:` field that differs from its job id. **None of the 5 hardcoded id-strings can ever match a real API response** — this is a deterministic, 100%-reproduction-rate bug, not intermittent.
- **Bonus real finding (Phase B-relevant, not acted on):** that same live run shows real conclusions: `invariants` → **failure**, `visual-regression` → **failure**, `bug-fix-discipline`/`test-style-guard` → **skipped**, `frontend-vitest`/`new-bloat-guard` → **success**. This is genuine, current GitHub Actions evidence for whatever Phase B wants to do with "confirm actual CI guardrail status" — separate from the UNKNOWN-mapping bug itself.

## 6. Reassessing the prior ~500 / ~460 CI-failure triage

Found two prior triage efforts in `memory/PRD.md`:
- "~460 failures = ~171 env + ~107 stale + ~230 uncertain needing per-test inspection" (PRD.md L4914)
- "158 of 500 CI failures @ ab791b8 ... 69×ingress-404 + 79×conn-refused + 3×env-key" (PRD.md L4943) — this is exactly what became `live_env_quarantine.txt`'s 39 entries today.

**Assessment:** the "env" bucket (live-URL tests failing because CI has no live server) is **CONFIRMED real and correctly labeled** — I independently re-derived the same mechanism from `conftest.py`. It is not a misclassification.

However, **today I directly reproduced a distinct, third failure mode that does not map cleanly to "env", "stale", or a to-be-inspected "uncertain" item on its own terms — cross-test-file global-state pollution**:
- `test_phase2c_cto_projects_router.py` + `test_phase2c_local_tools.py` + `test_phase2c_loop_engine.py` run **together, alone**: **251 passed, 0 failed** (clean).
- The same 3 files run **together with ~18 other legacy PAT/GitHub-App test files** in one pytest session: **153 failed** (many in those same, otherwise-100%-clean Phase 2c files) + **11 errors** (`TestClient` lifespan-teardown race, exact pattern already flagged once before in `memory/TEST_FAILURE_TRIAGE_2026-08-20.md` as "12 ERRORS... anyio thread portal racing during app lifespan shutdown when many test files reuse the same FastAPI app instance in one pytest session").
- **This means a plain "run everything, count reds" triage cannot reliably distinguish "this test/feature is broken" from "this test fails only because of which other files happened to run in the same process."** The prior ~460-failure triage's "~230 uncertain needing per-test inspection" bucket is the most likely place this got absorbed, but I only have direct reproduced evidence for this specific ~21-file PAT/GitHub-App subset today, not the full 230 — so: **LIKELY** (not fully CONFIRMED) that pollution-type failures make up a meaningful share of that "uncertain" bucket; **CONFIRMED** that the phenomenon itself is real and reproducible.
- **Net verdict:** the prior triage was not "wrong" in what it labeled env/stale — those labels hold up — but its 3-bucket model (env / stale / uncertain) has no explicit category for test-order/global-singleton pollution, and that gap is a real, evidenced blind spot worth naming, not just re-running.

## 7. Overall professional assessment (plain, as founder requested)

- **Trustworthy day-to-day, not structurally rebuild-worthy, but not "just run it and trust the number" either.** The suite has real behavioral coverage (52% of tests execute real code paths per the AST classifier) and real, working governance tooling (quarantine lists, a style analyzer, a coverage ratchet) that prior sessions built specifically because the raw accumulation was already recognized as a problem.
- **The two dashboard numbers the founder flagged (0 tests analysed, 5×UNKNOWN) are both concrete, narrow, one-function bugs** (a dropped `ok` flag; a job-id-vs-job-name string mismatch) — not evidence of a deeper rot. Both are cheap, low-risk fixes once approved.
- **The real structural risk is composability**, not architecture: individual files/waves are solid in isolation (Phase 2c's 251/251), but the full-suite number has never been proven safe to read literally because of reproducible cross-file pollution. A full "N failed" count from a single monolithic `pytest` invocation should be treated as **directionally useful, not a precise ground truth**, until pollution sources are isolated (separate task, not attempted here).
- None of this invalidates Phase 2c's accepted coverage work — those figures were measured with the specific narrow file-sets Phase 2c documented, not the full suite, and are unaffected by the pollution finding.

## PAT Migration Cleanup — root-cause findings (investigation only, no fixes)

21 candidate files identified (grep for `decrypt(s)_pat|app_installation_missing|installation_not_found|get_repo_token|GithubAppAuthError|pat_vault`), reproduced in 2 targeted runs (not full suite):

**Category A — Legacy API-surface assertions (STATIC_GREP-style, source-string checks on deleted symbols).** Reproduced 5/5 failures, both files:
- `test_iter205_pat_decryption_in_tools.py` (3 failed, 1 passed): asserts `services.pat_vault` has attribute `decrypts_pat` — never existed under that name; current module exports only `get_repo_token`, `get_repo_token_or_error`, `GithubAppAuthError` (confirmed via `__all__`).
- `test_iter212m230_phase7.py` (2 failed, 11 passed): asserts source string `"async def _decrypt_pat"` exists in `pat_vault.py` and `"from services.pat_vault import _decrypt_pat"` exists in `cto_projects.py` — both intentionally removed in the App-only PAT-removal migration.
- **Root cause: tests assert a pre-migration internal API surface that was deliberately deleted.** Not a regression. Fixing = rewriting/retiring these specific assertions to match the current App-only surface — does **not** require restoring any PAT code.

**Category B — Stale test fixtures build legacy-schema project docs; real code correctly rejects them.** Reproduced 13/13 + 7/7 failures (behavioral, `TestClient`, not string-grep):
- `test_github_app_project_add.py` (7 failed) — fixtures set `auth_method: "pat"` / legacy token fields; `get_repo_token` now only understands App-installation fields, so it correctly returns `None`/403, which the old assertions don't expect.
- `test_iter170_codebase_browse.py` (6 failed) — same pattern: fixture project has no real App installation, `/tree` and `/file` endpoints correctly 403 with `app_installation_missing`.
- **Root cause: same as Category A in spirit (fixtures assume removed auth path) but the failures are genuinely behavioral (real endpoint execution), not string assertions** — worth keeping as a separate bucket since the fix shape differs (update fixture setup to seed a fake App installation, not just rename an assertion).

**Category C — Cross-file pollution (see Phase A §6 above)** — when the 3 Phase 2c files run together with ~18 other PAT/GitHub-App files, 153 failures + 11 errors appear that do **not** occur when the Phase 2c files run alone (251/251 clean). At least some of the "30+ legacy fixtures" figure the founder was told about is very likely this mechanism amplifying Category A/B failures, or introducing new ones, rather than 30+ independently-broken tests. Not yet isolated to a precise count — would need bisection (one extra file at a time) to attribute exactly, not done this session (avoids more broad runs under disk pressure).

**Category D — Collection-time crash, unrelated to PAT but discovered in this batch:** `test_revoked_repo_banner_2026_08_20.py` reads `os.environ["REACT_APP_BACKEND_URL"]` at module level; when run in a batch (not solo) this raises `KeyError` **during collection**, aborting the whole batch with `Interrupted: 1 error during collection` — even though the file is already correctly listed in `live_env_quarantine.txt` (that mechanism can't intervene before module-level code executes). This is an independent, pre-existing fragility, surfaced only because this session's batch run included that file.

**Not yet done / explicitly not claimed:**
- Have not isolated the full "30+" figure to an exact reconciled count — only 20 files reproduced today (12 Cat-A/legacy-name + 7 Cat-B/fixture, with 1 overlap) plus the pollution multiplier effect in Cat-C.
- No code or test file was changed. No `decrypts_pat`/PAT-decrypt shim was restored.
- Have not touched `test_aurem_p0_bugs.py` or other files confirmed to hit the live Preview Mongo directly — separate, pre-existing category, out of scope for this pass.

---

## 2026-08-24, round 2 — fixes applied (founder-approved) + Category C root cause CONFIRMED

### Dashboard bugs — both fixed, both live-reproduced broken→fixed
1. **"0 tests analysed"** — `backend/routers/admin_qa.py::_harvest_test_style_ratio()` now checks `r.get("ok")` before trusting `total_tests`/`counts`; returns `{"available": False, "reason": ...}` when the analyzer reports `tests_dir_missing` (as it does in Production, per `.dockerignore`). **Reproduced broken** (patched `analyze_suite` to return `{"ok": False, "reason": "tests_dir_missing"}`, old logic would have shown `available:true, total_tests:0`) **→ fixed** (now correctly returns `available:false`) — verified via direct call, both scenarios, real dir still works too (`total_tests: 5357` unaffected).
2. **"5× CI UNKNOWN"** — `backend/services/qa_matrix.py::_harvest_ci_status()` now maps GitHub Actions' returned job *display name* to our internal job id via a `_JOB_DISPLAY_NAMES` dict (the API never returns the id itself). **Reproduced broken** (live call before fix: 0/5 jobs matched, all "unknown") **→ fixed** (live call after fix, same real run `32615685645`): `frontend-vitest: success`, `invariants: failure`, `visual-regression: failure`, `bug-fix-discipline: skipped`, `test-style-guard: skipped`. Real, live-verified, not simulated.

### PAT cleanup — Categories A, B, D fixed and reproduced; C confirmed root cause (not fixed, per founder's hold)
- **Category A (2 files, 5 tests)** — `test_iter205_pat_decryption_in_tools.py` + `test_iter212m230_phase7.py` rewritten to assert the CURRENT App-only contract (`pat_vault.get_repo_token_or_error`, `GithubAppAuthError` with typed `.code`) instead of the deleted `decrypts_pat`/`_decrypt_pat` names. Behavior/intent preserved (still covers: App-token mint success, not-connected → `None` token, `_decrypt_pat`-string assertions replaced with `get_repo_token`-string assertions matching the real current import lines).
- **Category B (2 files, 13 tests)** — `test_github_app_project_add.py`'s `TestGetRepoToken` class rewritten from "returns None on failure" to `pytest.raises(GithubAppAuthError)` with the correct `.code` per branch (matches `get_repo_token`'s real fail-closed contract); its one gate-branch test rewritten from "PAT path still works" to "PAT path now correctly rejected with `pat_not_supported`". `test_iter170_codebase_browse.py`'s shared `_client_with_seeded_project()` helper now seeds `auth_method: "github_app"` + `installation_id` and stubs `pat_vault.get_repo_token_or_error` so the endpoints reach their real code (tree/file/traversal/truncation logic) instead of dying at the auth gate.
- **Category D (1 file)** — `test_revoked_repo_banner_2026_08_20.py` now does `os.environ.get(...)` + `pytest.skip(..., allow_module_level=True)` instead of a bare `os.environ[...]` `KeyError`, so it degrades to a clean skip instead of aborting the whole collection when batched with other files.
- **All 6 files reproduced together**: `37 passed, 1 skipped` (the skip is the now-clean live_env behavior, correct).
- **Category C explicitly left untouched, and reproduced UNCHANGED after the above fixes** — reran the original adversarial ordering (17 other files, then the 4 Phase 2c files) and got the exact same phase2c failure set as before (`TestEnqueueCtoTask`×6, `TestRollbackWorkers`×8, `TestMiscHelpers`×2, `TestResolveProject`×2, `TestGetRepoInfo`×1, `TestRunSecurityScan`×3 — 22 tests, `116 failed, 312 passed, 13 skipped, 5 deselected, 11 errors` overall). Confirms the A/B/D fixes didn't accidentally mask or worsen C.

### Category C — ROOT CAUSE CONFIRMED (not fixed — founder wants this investigated first, reported here)

**Mechanism:** `tests/test_github_app_project_add.py`'s `client` fixture (and 7 other files, see below) does:
```python
router_mod.get_db = lambda: fake_db
router_mod.require_db = lambda: fake_db
router_mod.current_dev = _fake_current_dev
router_mod._run_project_indexing = _noop_indexing
```
directly on the **imported module object** (`from routers import cto_projects as router_mod`) — a `return TestClient(app)`, **not** `yield` + restore. Since `routers/cto_projects.py` does `from cto_services.db import get_db, require_db` and `from cto_services.auth import current_dev` at import time, these are *its own* module-global names — `cto_projects.py`'s internal code resolves `get_db()`/`require_db()`/`current_dev(...)` via its own `__dict__` at call time. Because `sys.modules` caches the module object for the whole pytest process, this fixture's assignment **permanently overwrites those names for every test file that runs afterward** in the same process — there is no teardown to undo it. The very last test in that class to run "wins" and its fixture's stub becomes the permanent state.

**Isolated, reproduced proof:**
- The 4 Phase 2c files alone: **251 passed, 0 failed** (clean).
- Just `test_github_app_project_add.py` run BEFORE the 4 Phase 2c files: **the same Phase 2c tests fail** (`TestRollbackWorkers`, `TestMiscHelpers::test_run_project_indexing_*`, `TestResolveProject`, `TestGetRepoInfo`, `TestRunSecurityScan` — all of which call, directly or via an imported helper, `get_db()`/`require_db()`/`current_dev()` inside `cto_projects.py`'s real code path and get this fixture's stale `fake_db`/no-op stub instead).
- Reversing the order (Phase 2c files run BEFORE `test_github_app_project_add.py`) — **0 Phase 2c failures**, confirming direction: the leak flows forward from whichever leaking file runs first, not backward.
- This is **deterministic, not a race** — same exact test names fail every time regardless of which/how-many other files are added around it (confirmed by contrast with the real intermittent race, which IS present separately: the 11 `ERROR`s in `test_iter363_phase3b_github_app_dispatch.py::test_*_app_installed` are a documented, different `TestClient`-lifespan/anyio-thread-portal race — already flagged in `memory/TEST_FAILURE_TRIAGE_2026-08-20.md` — not the same mechanism as the deterministic leak above).

**Scope — same anti-pattern (direct module-attribute overwrite, `return` not `yield`, no restore) found in 8 files total across the WHOLE 596-file suite** (not just this 21-file PAT batch): `test_github_app_project_add.py`, `test_github_app_router.py`, `test_iter170_codebase_browse.py`, `test_iter172_shell_handoff_guard.py`, `test_iter173_mcp_server.py`, `test_iter174_mcp_apikey.py`, `test_iter212m175_mcp_scoped.py`, `test_session4_p0_ora_breaker_surface.py`. Verified all 8 use `return` (not `yield`) in their `client` fixture — i.e. none of them can restore state even in principle. Only `test_github_app_project_add.py` was isolated as the specific trigger in *this* batch; the other 7 were not individually bisected this session (would need the same isolation treatment) but share the identical bug shape and are very likely equally capable of leaking, given they mutate the same shared `routers.cto_projects` (and sibling router) module objects.

**By contrast, Phase 2c's own fixtures do this correctly** — `test_phase2c_cto_projects_router.py`/`test_phase2c_chat_router.py` save `old_current_dev = router_mod.current_dev` before overwriting and restore it via `yield` + teardown; `test_phase2c_codebase_health_router.py` saves and restores 4 names the same way. Two in-test reassignments inside `test_phase2c_cto_projects_router.py` itself (`router_mod.current_dev = _founder` / `= _personal`, lines ~1100/~1229, no local restore) are safe *only* because the file's own `client` fixture unconditionally resets `current_dev` at the start of every test and restores the pre-test value at teardown regardless of what happened mid-test — verified this pattern is self-neutralizing, not a leak.

**What this means for the ~230-"uncertain" bucket (Phase A §6):** confirmed the pollution mechanism is real, deterministic, and traced to a specific, nameable code anti-pattern repeated in 8 files — not vague "test flakiness". Not yet quantified how many of the ~230 uncertain failures this specific mechanism explains; that would require repeating this bisection process against the full uncertain list, not attempted this round.

**Recommended fix shape (not applied — awaiting founder decision):** convert each of the 8 files' `client` fixture from `return TestClient(app)` to `yield TestClient(app)` + capture/restore the pre-overwrite values (`old_get_db = router_mod.get_db` etc.) after the yield — exactly the pattern Phase 2c's own fixtures already use. Mechanical, low-risk, same shape in all 8 files; still 8 separate edits + a full-suite-adjacent regression check once approved.
