# AUREM CTO — PRD (Product Requirements & Change Log)

## Original Problem Statement
AUREM CTO is a React SPA + FastAPI + MongoDB developer-productivity
platform ("aurem-dev" service). Focus: shipping features founders/devs
actually use, with a strict **Verify-First / Zero Mocks / Full Prod-Ready**
rule. Every fix must be reproducible on real data + tested via real APIs
before being called "done".

## Users
- Founder (Teji, `teji.ss1986@gmail.com`) — production account owner.
- Real developers (currently 30 on prod, 74 on preview).
- Founder-controlled admin dashboard at `/admin`.

## Core Product Areas
1. **Two-Agent Loop** — Plan → Approve → Execute → Ship flow (Council models).
2. **GitHub Integration** — OAuth-first signup, repo listing, push flow.
3. **Admin Dashboard** — Live LLM/dep status, topup alerts, revenue, funnel.
4. **QA / Vanguard** — Real-time diagnostic probes + scope-drift audits.
5. **Session-based bug fixes** — recurring Session N batches driven by
   real-user QA reports.

---

## Change Log

### 2026-02-02 — SSOT-Model-ID-Refactor (Focused Session)
**P0 fix** — Runtime files that duplicated Claude / GLM model slugs in
their env-fallback defaults now resolve through the canonical SSOT in
`services/llm/openrouter_providers.py` (`_CLAUDE_MODEL`, `_GLM_MODEL`).
Prevents future copy-paste drift when Council A swaps primary models.

- **8 runtime files refactored** — each now imports the SSOT constant
  (with a defensive literal fallback ONLY inside `except` blocks for
  circular-import safety):
  1. `services/vanguard_verify_agent.py` (Claude + DeepSeek)
  2. `services/loop_independent_verifier.py` (Claude)
  3. `services/reasoning_evals.py` (Anthropic-native — see below)
  4. `main.py` (GLM fallback in exception path)
  5. `services/ora_chat/session.py` (`SUMMARY_MODEL` — added
     `ORA_SUMMARY_MODEL` env override)
  6. `services/ora_chat/router.py` (`fallback` route)
  7. `services/scaffold_design_review.py` (`_DEFAULT_MODEL`)
  8. `routers/feature_window.py` (Council A fallback label)
- **Real bug fixed** — `reasoning_evals.py::llm_faithfulness_check`
  had `model="claude-sonnet-4-6"` (invented ID that returns 404 from
  Anthropic). Corrected to `claude-sonnet-4-5` (Anthropic-native
  format is required by the Emergent SDK path — distinct from
  OpenRouter's dotted slug).
- **Drift-guard tests** — `tests/test_ssot_model_id_no_drift.py`
  (9 cases, zero mocks): env-override propagation, SSOT re-export
  identity, anthropic-native format assertion, and a static-scan
  guard that fails if any listed file loses its SSOT import.
- **Verification** — 85 related tests pass (vanguard verify, loop
  verify, tier2 parliament scaffold, smart_router, advisor). Backend
  boots clean, `/api/health` returns `council_a_model:
  anthropic/claude-sonnet-4.5` and `dead_tasks: []`.

### 2026-02-01 — Session C · Sub-step 2 (LLM Package Modularization) + BUILD_HASH file-based fix
- **BUILD_HASH workaround** — `_resolve_build_hash()` ladder now has 2
  new file-based steps (priority 2: `backend/.build_info`, priority 4:
  raw `.git/HEAD` parse — zero git-binary dependency). On successful
  git resolution, `.build_info` is auto-persisted for subsequent
  starts. Pre-deploy helper: `backend/scripts/write_build_info.py`.
  `.build_info` added to `.gitignore`. Tests:
  `tests/test_build_info_workaround.py` (4 cases).
- **Session C · Sub-step 2** — moved `_llm_state.py`, `_llm_routing.py`,
  `_llm_probes.py` from `services/` into `services/llm/` package as
  `_state.py`, `_routing.py`, `_probes.py`. Old paths retained as
  backward-compat shims (~20 LOC each) so the ~10 legacy test files
  that import `services._llm_*` still resolve.
- **`services/llm/__init__.py`** — absolute → relative imports
  (`from ._state import ...`, `from ._routing import ...`,
  `from ._probes import ...`). All 3 lazy imports in `__getattr__` /
  `_LLMModule.__setattr__` also switched to `from . import _probes`.
- **Silent Sub-step 1 regression FIXED** —
  `_GROQ_HOUSE_RULES_PATH` used `dirname(dirname(__file__))` which,
  after `llm.py` → `llm/__init__.py`, resolved to
  `services/prompts/…` (non-existent) instead of `backend/prompts/…`.
  Added third `dirname` hop + regression guard test
  `tests/test_llm_package_paths.py`.
- **Test infra updates** — 5+ `importlib.reload(_routing)` sites now
  reload the real `services.llm._routing` (not just the shim).
  Stale `services/llm.py` path assertions across ~8 test files
  updated to `services/llm/__init__.py`.
- **Verification** — All LLM-domain tests green (192+ passing across
  Phase 0a/1/2 + Session C guards + no-contamination + build-hash).
  E2E endpoints: `/api/health` 200, `/api/aurem-dev/chat/agents/list`
  401, `/api/aurem-dev/admin/architecture` 401. Zero circular imports.
- **Deploy status**: LOCAL VERIFIED, awaiting founder go-ahead to deploy.

### 2026-08-01 — GitHub Connect Funnel Telemetry (revenue-item follow-up)
- **New router**: `backend/routers/github_funnel.py` at
  `/api/aurem-dev/funnel/github/{event,stats}`
- **New collection**: `github_funnel_events` (session_id + stage + source)
- **5 tracked stages**: `cta_click → oauth_redirect → callback_received →
  linked → repo_selected`. Client fires 1 + 5; server fires 2/3/4.
- **CTA wiring** at 4 entry points: Login, Signup, GitHubCard,
  NewUserWizard. `withFunnelParams()` appends `fs` + `fsrc` to the
  OAuth URL so client + server events share a session_id.
- **Silent-fail** — telemetry never blocks the OAuth flow.
- **Tests**: 8/8 backend pytest + 6/6 frontend vitest (real HTTP + real
  Mongo, zero mocks).
- **Deploy status**: Shipped to prod. Data collection window: 3-5 days
  of real new signups. Existing 41-user cohort NOT backfilled.

### 2026-07-31 (evening) — build_hash Fix + Session 7 shipped
- **Bug**: prod `/api/health` `build_hash` stayed at `m1c61197`
  (2026-07-31 04:07 UTC) even after Session 6+7 landed. Root cause:
  `_resolve_build_hash()` used only `main.py`'s mtime; Session 6/7
  didn't modify main.py so fingerprint never shifted.
- **Fix (Option A)**: `_resolve_build_hash()` now scans max mtime
  across all `backend/**/*.py` files (skips `__pycache__`, `.venv`,
  `node_modules`, `/tests`). Verified on prod: `m1c61197 → m1c615a4`.
- **Follow-up (Option B, pending)**: Founder to set `BUILD_HASH=$GIT_COMMIT`
  env var in Emergent deploy config so any future frontend/config-only
  deploy also updates the fingerprint.

### 2026-07-31 (day) — Session 7: Loop UI-State Reliability
- Item 1: Cancel loop UI stuck chip → async state sync fixed
- Item 2: Duplicate plan-bubble dedup
- Item 3: Approval Panel missing Cancel button safety fix
- Item 4: Rapid concurrent-send React race condition lock
- Tests: 17/17 vitest pass (Session7_Item1/2/3 files)

### 2026-07-31 (day) — Session 6: Real-User QA Batch
- Item 1: VS Code Marketplace real status API in `/admin`
- Item 2: Tavily `topup_alerts` cross-day dedup (was piling 1 row/day)
- Item 3: `minimal_edit.py` surgical diff path (avoids full LLM rewrites)
- Item 4: Live-feed vs Ship-panel state mismatch fix
- Item 5: "Developers: —" undefined value → reads `stats?.real_developers`
- Item 6: `qa_manifest.json` stale-data threshold warnings
- Tests: 47/47 pytest pass

### Earlier (pre-2026-07-31)
- `services/llm.py` split into `_llm_state.py`, `_llm_routing.py`,
  `_llm_probes.py` (Phase 0a, 1, 2 done; Phase 3 package conversion
  pending)
- Resend API key rotated + Cloudflare-1010 bypass fix
- Session 5: ORA-chat silent catch cleanup

---

## Prioritized Backlog

### P0 (needed for revenue / stability)
- **GitHub Connect funnel data collection** — wait 3-5 days for real
  new-signup data, then targeted fix (b/c/d — the specific drop-off
  point once data reveals it).

### P1 (soon)
- **Option B `BUILD_HASH` env var (platform-level)** — Deployer_agent
  should auto-inject commit SHA. **Workaround shipped**: `.build_info`
  file (tracked in git, ships in tarball) + raw `.git/HEAD` parse
  ladder (see 2026-02-01 changelog). Known 1-commit lag on prod
  because `.build_info` cannot be self-referential; see
  `/app/memory/emergent_support_ticket_option_B.md` for the pending
  Emergent platform feature request that would eliminate the lag.
- **~~Session D — LLM Split Phase 4~~** ✅ COMPLETE
- **~~Session D-part-2 — extract `_call_llm_with_meta_inner`~~** ✅ COMPLETE
- **~~Session F — Background-tasks supervisor~~** ✅ COMPLETE
- **~~Session E — 22 deferred CI-lane failures~~** ✅ COMPLETE (real fixes)
- **~~Session G Phase 3.1 — Legacy Bucket-A auth-fixture drift~~** ✅ PARTIAL
  (+12 tests unblocked; full Bucket-A completion needs PAT-mock + SSE-shape work)
- **~~`services/llm.py` Phase 3~~** ✅ COMPLETE (Sub-step 1 + Sub-step 2).

### P0 (current focus)
- **~~SSOT-Model-ID-Refactor~~** ✅ COMPLETE (Feb 2026 — 8 files + drift-guard test suite)

### P1 (next up)
- **Persona-Dedupe** — `AUREM_CTO_PERSONA` currently at 25,687 chars
  (+16% over the 22,000 char latency budget from
  `test_iter129_chat_latency_budget.py`). Needs a focused session
  (dedupe → real conversation spot-checks to verify quality holds).
- **Session G Bucket-A Batch 4c** — production_wiring,
  ora_chat_persistence, ora_dropdown, local_tools_project. Same
  discipline as 4b (test-only edits when real bugs surface, deploy
  after batch).
- Session 5 P2 findings: vanguard-config Mongo migration, MCP fallback logging
- **~~20+ Unsupervised Background Tasks wrapper~~** ✅ COMPLETE (Session F)
- Founder-Blocked env vars (G8-G11)
- VS Code Marketplace publish (blocked on Azure DevOps PAT)
- `/admin` funnel widget (visualise `/funnel/github/stats` output)
- Session G Phase 3.2+ — Bucket A remaining ~80 nodeids
  (needs PAT mocking + SSE shape update + individual fixture repair)
- Session G Phase 4 — Categorise the 78 UNCATEGORIZED legacy files

### P2 (backlog)
- Fix `test_iter80_seo_pwa.py` post-marketing-content refresh
  (blocked on canonical pricing $9/mo + brand "Aurem CTO" + current
  feature-list confirmation from founder)
- Re-investigate `test_iter267_url_fetch_retry.py` (SSRF pre-check
  timing issue)

### Overnight Session — Feb 2026 — LOC + Test Score Deltas
- `services/llm/__init__.py`: 1591 → 426 LOC (**−73%**)
- New sibling files: `_meta.py` (596), `_state.py`, `_routing.py`,
  `_probes.py`, `openrouter_client.py`, `groq_client.py`,
  `openrouter_providers.py`, plus `services/supervised_tasks.py`
- Backend pytest sweep: 4002 pass / 8 fail → **4014 pass / 0 fail /
  65 skipped** (Session E fixed all 8 pre-existing failures)
- Legacy quarantine: 216 fail / 30 pass → **180 fail / 42 pass**
  (Session G partial)
- Prod `/api/health` new field: `supervised_tasks:
  {supervised_count:13, alive[], dead[]}` — Guard 20 wired

---

## Testing & Credentials
- Backend: `pytest` in `/app/backend/tests/`
- Frontend: `vitest` (via `npx vitest run`)
- QA manifest: `backend/qa_manifest.json` (regen: `python scripts/gen_qa_manifest.py`)
- **Zero mocks rule**: every test hits real Mongo + real HTTP; no
  `unittest.mock` in the codebase for feature tests.
- Credentials: `/app/memory/test_credentials.md` (preview + prod founder).
