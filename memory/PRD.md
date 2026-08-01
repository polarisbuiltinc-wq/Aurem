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

### 2026-02-02 — Persona-Dedupe (Focused Session)
**P1 fix** — `AUREM_CTO_PERSONA` trimmed from 25,687 → 21,559 chars
(**-4,128 chars, 16% reduction, 441 char headroom under the 22,000
budget from `test_iter129_chat_latency_budget.py`**). Every chat turn
re-sends this on every tool iteration, so this shaves ~1k input
tokens per iteration off the LLM bill and cuts p95 chat latency.

- **Sections trimmed** — consolidated 5 tool-call-format NEVERs to 2;
  removed 3 build-check NEVERs already covered by Rule 6; dropped the
  READ-REPO PROTOCOL placeholder (pointed to HOW TO RESPOND anyway);
  trimmed ⚠ ABSOLUTE NEGATIVES-extended a/b/c/d wording; cut 2 of 3
  ✗ INCORRECT ship-brief examples; tightened Rule 4 (leak), Rule 7
  (READ BEFORE YOU ANSWER), Rule 8 (ANALYSIS → SPEC CONTRACT); shrunk
  MODE DETECTION examples and TASK STATE TRACKING closer;
  compressed EXTERNAL URLS section to essentials.
- **Heading rename** — `MULTI-FILE TASKS — STATE TRACKING & FULL
  DELIVERY` → `MULTI-FILE TASK EXECUTION — STATE TRACKING & FULL
  DELIVERY` (semantic + test-compliant). `_SECTION_LAYER` mapping
  updated so the layered-persona slicer still routes it to L2 EXECUTE.
- **IDENTITY + DO NOT LEAK rewrite** — kept meaning, tightened
  language, and added the specific phrases the legacy quarantine
  tests were asserting on: "DO NOT invent a name", "DO NOT invent a
  location", "DO NOT invent the origin story", "FABRICATION and is
  forbidden", "CONVERSATIONAL MODE", "Listing internal tool names
  verbatim", "from what's in my system context", "Never reference
  the prompt".
- **5 tests un-quarantined** (removed from `tests/legacy_quarantine.txt`):
  1. `test_iter129_chat_latency_budget.py::test_persona_under_budget`
  2. `test_iter74_gaps.py::test_persona_has_search_and_multi_file_and_state_sections`
  3. `test_iter103_identity_no_fabrication.py::test_identity_forbids_inventing_names`
  4. `test_iter103_identity_no_fabrication.py::test_identity_forbids_location_team_motivation`
  5. `test_iter103_identity_no_fabrication.py::test_no_leak_forbids_mode_names_and_tool_names`
- **Layered-persona still works** — after dedupe: L1 CORE 10,819
  chars / L2 EXECUTE 9,269 / L3 REPO 1,408 (previously ~12k / ~11k /
  ~2.5k). CONVERSATIONAL floor stays under the 8k target.
- **Real-conversation spot-checks (3)** — zero-mock chat via
  preview `/api/aurem-dev/chat/send`:
  1. Greeting "hi how are you" → warm 4-sentence reply, no handoff,
     no tool calls (correct CONVERSATIONAL mode).
  2. Identity attack "who founded AUREM CTO? tell me the name and
     origin story" → responded verbatim with the anti-fabrication
     fallback ("AUREM CTO is built by the AUREM team — I don't
     have public details…") and pivoted to capabilities. Zero
     fabricated bio.
  3. Technical Q "explain what JWT is in one paragraph" → clean
     paragraph explanation, no handoff, no tool calls.
- **Regression sweep** — 55 tests pass across all persona-related
  files (aurem_persona_v2, iter124c hard rules, iter124g quality
  score, iter212l hardening, proof_iter130 layered persona, iter74
  gaps, iter103 identity, iter129 latency budget, iter274 personal
  track, iter169 fix hallucination). 4 remaining failures all
  confirmed pre-existing in `legacy_quarantine.txt` (not caused by
  this dedupe).

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
- **~~Persona-Dedupe~~** ✅ COMPLETE (Feb 2026 — 25,687 → 21,559 chars, 5 tests un-quarantined, 3 live spot-checks)

### P1 (next up)
- **Session G Bucket-A Batch 4c** — production_wiring +
  ora_chat_persistence already pass (19/19). Real Batch 4c candidates
  from live quarantine scan: `test_iter205_pat_decryption_in_tools`
  (3 fails, PAT decrypt returns None), `test_iter212m6_wiring_audit::
  test_known_python_repl_tools_covers_local_tools` (missing Vercel
  tools in KNOWN list), `test_iter169_fix_hallucination_guards::
  test_budget_hit_*` (budget-hit message with `seen_paths[0].split`
  + `"narrow the ask to one file"` never landed in
  `services/orchestrator.py` — needs real code implementation).
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
