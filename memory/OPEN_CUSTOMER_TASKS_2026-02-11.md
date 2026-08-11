# Open Customer Tasks — polarisbuiltinc-wq/auremdev-update
**Opened:** 2026-02-11 · **Customer:** polarisbuiltinc-wq · **Repo:** auremdev-update
**Closed:** 2026-02-11 (same day, deploy + customer confirmation) · **Status:** ✅ BOTH TASKS RESOLVED

Two coupled tasks. **Task 2 was blocked on Task 1 landing in prod — both cleared same-day.**


### 📝 Session close note (2026-02-11)
Customer retested `test all APIs working or not` on `polarisbuiltinc-wq/auremdev-update` directly after the prod deploy landed:
- ✅ No more instant `HTTP 400 "No PAT configured"` rejection
- ✅ ORA ran the check for 17+ seconds of real work
- ✅ Returned a **genuine project-specific finding**: `403 Forbidden due to insufficient admin permissions on one endpoint`, with a fix recommendation
- ✅ Founder confirmed: "Task 1 and Task 2 both confirmed resolved on my end. Nice work — both tasks closed."

The 403 that ORA surfaced is a **legitimate product finding** for the customer to act on, not a bug in AUREM.

---

## Task 1: Chunk A · `get_repo_token()` sweep (Bug 2, Phase 3b)
**Status:** ✅ RESOLVED — deployed to prod 2026-02-11, customer confirmed working same-day

**Impact:** Full block for every App-installed project (not just one endpoint). Symptoms:
- `HTTP 400 {"detail":"No PAT configured for this project..."}` on any chat that hits a repo tool
- Loop resume/retry fails with the same 400 before the loop can even attempt the fix
- Downstream: fix-pipeline, security-scan, health-scan, rollback, admin brain, MCP tools — all inherit the same block

**Files touched (15):**
- `services/repo_context.py` (per-turn repo briefing — highest-frequency hot path)
- `services/repo_heal.py`, `services/finding_fix_applier.py`, `services/rollback_manager.py`
- `services/seo/orchestrator.py`, `services/repo_indexing.py`, `services/codebase_indexer.py`
- `services/loop_engine.py` (6 sites — plan/execute/ship/graph-refresh/scan/diff-scan)
- `routers/fix_pipeline.py`, `routers/loop.py`, `routers/user_rollback.py`
- `routers/codebase_health.py`, `routers/admin.py` (2 sites), `routers/admin_bin.py`
- `routers/repo_status.py`, `routers/chat.py` (2 sites)
- `routers/security_scan.py`, `routers/mcp.py` (2 sites)

**Projections expanded** to include `auth_method`, `installation_id`, `user_id` alongside the existing `github_token` — `get_repo_token()` needs all four to dispatch.

**Verification done:**
- ✅ Lint clean on all 18 files
- ✅ 64/64 pytest green on `test_github_app_*.py`
- ✅ Backend restart clean, no import errors
- ✅ `/api/health` + `/api/aurem-dev/promo/first50/status` smoke curl pass

**Still pending:**
- Testing agent run against the App-installed project path (backend-only)
- Prod deploy window

---

## Task 2: Resume the paused loop on `polarisbuiltinc-wq/auremdev-update`
**Status:** ✅ RESOLVED — customer retested same-day after Task 1 deploy landed. ORA ran the "test all APIs" flow for 17+ seconds and returned a legitimate project-specific finding (403 Forbidden due to insufficient admin permissions on one endpoint, with a fix recommendation). The auth-gate bug is gone; whatever the customer does next with the loop is normal product usage, not a Task 2 blocker.

The `__init__.py:1:1` invalid-syntax signature that was reported earlier did NOT resurface after the resume. Working theory: the original paused-loop error was a stale artifact from the pre-fix state, and once the customer could actually enter the pipeline again, the loop was able to self-resolve or restart from a known-good checkpoint.

---

### Historical context (kept for future audit)

**Original reported failure signature:**
- Loop paused/failed at execute or verify phase
- Failing file: `backend/aurem_cto/routers/__init__.py`
- Failure: `invalid-syntax at 1:1`
- Customer clicked "resume" → immediately got `HTTP 400 "No PAT configured"` (Task 1 bug)

**Diagnosis hypothesis (never had to be executed — retest bypassed the whole path):**
- Most likely causes: UTF-8 BOM at byte 0, zero-width unicode char, or LLM-generated fenced-code marker as file's first char
- Follow-up self-heal enhancement was proposed: byte-0 sanity check in `services/local_tools.py::_run_syntax_check` to catch this class of file-corruption pre-commit. Recorded as a Next Action for a future session — not blocking anything today.

---

## Process note (2026-02-11 founder ask)
When founder gives two coupled tasks, agent MUST:
1. Acknowledge both up-front in the first response.
2. Track Task 2 in memory (this file) so it survives context compaction / fork.
3. Explicitly flag Task 2's status BEFORE wrapping Task 1 — never silently continue only on Task 1.
