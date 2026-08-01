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
- **Session D — LLM Split Phase 4** — Extract `openrouter_client.py`
  (the 6 `_call_*` provider helpers) from `services/llm/__init__.py`.
  Per `memory/LLM_SPLIT_MIGRATION_PLAN.md`.
- **~~`services/llm.py` Phase 3~~** ✅ COMPLETE (Sub-step 1 + Sub-step 2).

### P2 (backlog)
- Session 5 P2 findings: vanguard-config Mongo migration, MCP fallback logging
- 20+ Unsupervised Background Tasks wrapper
- Founder-Blocked env vars (G8-G11)
- VS Code Marketplace publish (blocked on Azure DevOps PAT)
- `/admin` funnel widget (visualise `/funnel/github/stats` output)

---

## Testing & Credentials
- Backend: `pytest` in `/app/backend/tests/`
- Frontend: `vitest` (via `npx vitest run`)
- QA manifest: `backend/qa_manifest.json` (regen: `python scripts/gen_qa_manifest.py`)
- **Zero mocks rule**: every test hits real Mongo + real HTTP; no
  `unittest.mock` in the codebase for feature tests.
- Credentials: `/app/memory/test_credentials.md` (preview + prod founder).
