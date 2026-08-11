# Open Customer Tasks — polarisbuiltinc-wq/auremdev-update
**Opened:** 2026-02-11 · **Customer:** polarisbuiltinc-wq · **Repo:** auremdev-update

Two coupled tasks. **Task 2 is blocked on Task 1 landing in prod.**

---

## Task 1: Chunk A · `get_repo_token()` sweep (Bug 2, Phase 3b)
**Status:** ✅ CODE COMPLETE (this session) — ⏳ awaiting prod redeploy

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
**Status:** 🔴 BLOCKED on Task 1 prod redeploy

**Reported failure signature:**
- Loop paused/failed at execute or verify phase
- Failing file: `backend/aurem_cto/routers/__init__.py`
- Failure: `invalid-syntax at 1:1`
- Customer clicked "resume" → immediately got `HTTP 400 "No PAT configured"` (Task 1 bug)

**Why blocked:** Every resume attempt currently 400s on the PAT gate BEFORE the loop can re-enter. Even if the loop code is fine, we can't observe the real syntax error until the gate passes.

**Next steps after Task 1 lands in prod:**
1. **Retry the resume** — either from the customer's dashboard OR via direct API call:
   ```
   POST /api/aurem-dev/loop/{loop_id}/resume
   Authorization: Bearer <customer_jwt>
   ```
   Expected: no more PAT 400. Loop should re-enter execute/verify phase.
2. **Diagnose the `__init__.py:1:1` invalid-syntax** — most likely causes in decreasing order:
   - a. Empty file with UTF-8 BOM (`\ufeff` at byte 0) — `python -m py_compile` says "invalid syntax" on the BOM
   - b. File contains a non-ASCII zero-width char at start
   - c. LLM generated a fenced-code marker (```` ``` ````) as the file's first char
   - d. Legitimate syntax error (unexpected but possible)
3. **Determine self-heal capability:**
   - If (a) or (b): the fix-loop's syntax gate (`_run_syntax_check` in `services/local_tools.py:38-`) should catch it on the next `write_repo_file` — the loop can self-heal.
   - If (c): may need a one-shot patch to strip the marker before re-running.
   - If (d): let the loop handle it via normal retry.
4. **If self-heal fails after one retry:** patch the file directly (empty `__init__.py` is valid Python — just write zero bytes) and re-ship.

**Files to review during diagnosis:**
- `/app/backend/services/loop_engine.py` — resume logic
- `/app/backend/services/local_tools.py:38-155` — `_run_syntax_check`, `_run_repo_syntax_check`
- Loop session doc for `polarisbuiltinc-wq/auremdev-update` in `loop_sessions` collection

**Customer communication:** Once Task 1 is live, the customer can retry from the UI. If Task 2 needs manual intervention (case c/d above), coordinate with the customer via founder rather than direct patch.

---

## Process note (2026-02-11 founder ask)
When founder gives two coupled tasks, agent MUST:
1. Acknowledge both up-front in the first response.
2. Track Task 2 in memory (this file) so it survives context compaction / fork.
3. Explicitly flag Task 2's status BEFORE wrapping Task 1 — never silently continue only on Task 1.
