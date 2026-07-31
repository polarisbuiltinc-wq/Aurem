# Session 2 · Deep Codebase Audit Report
**Date**: 2026-07-31 · post-Iter 367 deploy
**Discipline**: DISCOVERY ONLY — no fixes applied this session. Per user instruction.
**Scope**: Everything Session 1 did NOT deep-audit — loop engine family (18 modules), ORA chat plumbing (~20 modules), remaining scripts + workflows, real pytest run for real pass/fail, frontend Personal-Track wiring.

---

## 0. Real pytest run — actual numbers

Ran `pytest tests/` with `--timeout=15`, ignoring two known-hang test files.

```
3799 passed · 28 failed · 69 skipped · 233 deselected (legacy) · 2 collection errors
Runtime: 5m 19s
Pass rate: 97.5% (3799 / 3897 collected)
```

**28 failures grouped by root cause** (all pre-existing, none from Iter 367):

| Group | Files/tests | Likely cause | Recurring? |
|-------|------------|--------------|-----------|
| Signup rate-limit accumulated | `test_aurem_backend.py::test_health` + `test_signup_creates_user` (ERROR) + `test_signup_duplicate_returns_409`; `test_iter212m55::test_signup_endpoint_reachable`; `test_jwt_revocation.py::*` (2 tests) | Signup guards throttle at test time — accumulated IP-per-day counters from previous CI runs. Tests are not creating fresh IPs. | Y |
| Loop founder-gate | `test_iter212m130_loop_founder_gate::*` (2 tests) | The founder-gate copy string / status shape drifted since the test was written. | Y |
| Advisor tier split | `test_iter212m210_advisor_tier_split::test_non_founder_hides_infra`, `test_cross_user_ownership_still_404` | Response shape drifted; test asserts on specific keys. | Y |
| Deploy config write | `test_iter212m9_deploy_ui::test_save_config_with_project_id_writes_scoped_row` + `test_save_config_without_project_id_writes_user_level` | My Item B added new fields (`target`, `remote_dir`, `ftp_tls`) — the test asserts on the exact stored dict. **BROKEN BY ITER 367** — was passing before my Item B refactor. | Y |
| Automations (iter78) | `test_create_list_toggle_delete_automation`, `test_create_requires_all_fields`, `test_webhook_triggers_task_on_matching_project` | The `routers/automations.py` payload contract drifted from what the test asserts. | Y |
| Skills (iter78 + iter79 + iter123) | `test_iter78_code_surface::test_code_surface_requires_admin`, `test_iter79_web_skills::test_skills_status_endpoint_admin_only`, `test_iter123_dev_skills::test_e2b_run_code_skipped_or_runs` | Admin-gate shape drift OR external service unavailable. | Y |
| Architecture health | `test_iter86_architecture_health::test_architecture_health_endpoint_admin_only` | Same admin-gate shape drift pattern. | Y |
| OAuth PKCE | `test_iter182_oauth_pkce::test_5_full_flow_happy_path` + `test_6_pkce_failure_wrong_verifier` | OAuth callback response shape drifted. | Y |
| Langfuse tracing | `test_iter212m119_langfuse_tracing::test_call_llm_with_meta_wraps_inner_with_trace` | Test asserts the exact wrapper call order; my Iter 367 delete of `llm_router.py` collapsed a code branch this test walked. **POSSIBLY BROKEN BY ITER 367** — needs inspection. | N |
| Narration copy | `test_iter309_narration_backend::test_narration_text_word_budget_and_bans` | Fragile source-string assertion on the narrator vocabulary. | Y |
| Read-only loop guard | `test_iter331_readonly_loop::test_pipeline_still_guards_between_phases` | Source-structure assertion — pipeline was refactored in a later iteration. | Y |
| Integration cron writer | `test_integration_health_cron::test_probe_persist_writes_latest_and_history` | Persistence layout drifted. | Y |
| Billing cron real DB | `test_iter102_billing_cron_referral_reward::test_bill_maxx_overages_iterates_real_db` | Needs a real seeded Mongo state; test-isolation issue. | Y |
| Fix pipeline | `test_iter212m121_fix_pipeline::test_preview_rejects_empty_findings` (ERROR) | Collection-time import error — module import fails at test-time. | Y |
| P0 journey coverage | `test_regression_iter297_p0_journey_coverage::test_j005_loop_start_endpoint_runs_plan_phase_and_returns_awaiting_confirmation` | Loop-start response shape drifted. | Y |
| SSE playwright invariant | `test_invariants_continuous_quality::test_invariant_every_sse_event_reaches_frontend_playwright` | Requires a real running browser + backend; environment-dependent. | Y |
| Token enforcement | `test_token_enforcement::test_usage_me_shape` | `/usage/me` response shape drifted. | Y |

**Two failures I likely caused with Iter 367** (need fixing next session, NOT this one):
- `test_iter212m9_deploy_ui::*` (Item B added fields to the deploy config dict)
- `test_iter212m119_langfuse_tracing` (Iter 367 delete of `llm_router.py` collapsed a code branch)

---

## 1. Loop engine family — 18 modules

All imported ≥ 3 times. Deep-audit result:

| File | Status | Reason |
|------|--------|--------|
| `loop_audit_log.py` | ✅ FULLY BUILT | 9 imports; central sink for every check-event during a loop run. Real writes to `loop_audit_events`. Iter 272 F1.5. |
| `loop_beta.py` | ✅ FULLY BUILT | 8 imports; tiered rollout gates + kill-switch (Iter 364). Wired into `routers/loop.py`. |
| `loop_diff_classifier.py` | ✅ FULLY BUILT | 3 imports; pure classifier splitting `source` vs `test/fixture`. Used by ship-diff builder. |
| `loop_execute.py` | ✅ FULLY BUILT | 11 imports; real EXECUTE phase (parallel + per-file timeouts, Iter 212m-112). |
| `loop_full_scan.py` | ✅ FULLY BUILT | 5 imports; "glue layer" for full-scan mode (Directive Session 2 · Part B). |
| `loop_independent_verifier.py` | ✅ FULLY BUILT | 3 imports; runs after Vanguard for narrow re-verification (Iter 272 F1.3). |
| `loop_integrity_guard.py` | ✅ FULLY BUILT | 6 imports; pre-ship + verify-phase data-loss guards (Iter 318). |
| `loop_intent.py` | ✅ FULLY BUILT | 7 imports; read-only intent gate that prevents "what is CI status" from firing a full loop (Iter 349 · PROD P0 fix). |
| `loop_intent_stats.py` | ✅ FULLY BUILT | 5 imports; hourly-bucketed observability for the intent gate (Iter 350). |
| `loop_outcomes.py` | ✅ FULLY BUILT | Already audited Session 1 + Iter 367 STEP 0. |
| `loop_rollback.py` | ✅ FULLY BUILT | Audited Session 1 — the ONE real rollback path. |
| `loop_safety.py` | ✅ FULLY BUILT | **39 imports** — the most heavily used safety module; 5 production-safety primitives shared by Loop Mode + Finding Fix (Iter 212m-115). |
| `loop_ship_diff.py` | ✅ FULLY BUILT | 4 imports; per-file line diff for `ShipPendingCard`. Pure, no I/O. |
| `loop_speed_diagnostic.py` | ✅ FULLY BUILT | 4 imports; read-only aggregation for founder's speed-diagnostic prompt (Iter 309). |
| `loop_task_specs.py` | ✅ FULLY BUILT | 11 imports; frozen task spec at plan-approval time (Iter 272 F1.1). |
| `loop_token_ledger.py` | ✅ FULLY BUILT | 5 imports; per-loop LLM token accounting via `contextvars` (Iter 309). |
| `loop_verify.py` | ✅ FULLY BUILT | 9 imports; real static-analysis verifier running ruff (Iter 212m-62). |
| `loop_engine.py` | ⚠️ **HALF BUILT** | **4067 lines** — the giant central engine, refactored by this iter for Items C, D, E. Contains fresh Iter 367 wiring that is now live but not fully covered by regression: the correction-rule per-rule enforce filter, the risk_routing pre-write scoring, and the post-ship browser self-test. All three have their own new tests, but engine-level integration is only smoke-covered. |

---

## 2. ORA chat plumbing — 20 modules

All imported at least once. Deep-audit result:

| File | Status | Reason |
|------|--------|--------|
| `ora_client.py` | ❓ UNCLEAR | 179 lines. Only ONE caller (`routers/chat.py:919 → is_ora_available()`) — a founder-only reachability check. Circuit breaker + persistent state file in `/tmp/ora_breaker_state.json`. `ORA_BASE_URL=https://aurem.live` + `ORA_API_KEY=aurem_sk_live_...` ARE set in .env. **Unclear whether aurem.live is currently operational**; the client's circuit-breaker means we'd never notice a soft failure. Needs founder confirmation. |
| `ora_context.py` | ✅ FULLY BUILT | 24 imports; ORAContext hardening layer 0 (Iter 212m-170). |
| `ora_council_logger.py` | ✅ FULLY BUILT | 11 imports; fire-and-forget writes to `ora_council_logs` for future fine-tuning. Silent-failure by design (comment says "never blocks"). |
| `ora_council_retriever.py` | ⚠️ HALF BUILT | 4 imports; activates self-learning loop at N=165. Docstring says "ACTIVATES the ORA Council self-learning loop NOW" but no test proves it fires end-to-end in production. |
| `ora_fix_learning.py` | ✅ FULLY BUILT | 18 imports; Phase-1 learning foundation for scan + fix (Iter 212m-129). Live use. |
| `ora_learning.py` | ⚠️ HALF BUILT | 5 imports; Iter 145 silent shadow-logging pipeline. Feature-flag env var `ORA_LEARNING_DISABLED` was flagged Session 1 as a "double-negative confusing" flag — behavior is ON by default. |
| `ora_chat/adversarial_review.py` | ✅ FULLY BUILT | Draft (DeepSeek V3) + hostile review (GLM-5.2) on HIGH_STAKES turns (Iter 268). |
| `ora_chat/canary.py` | ✅ FULLY BUILT | Nightly grounding canary against `/message` endpoint (Iter 264 Fix D). Wired via `canary_cron` startup task in main.py:695 gated by `ORA_CANARY_ENABLED=1` (set in .env). |
| `ora_chat/codebase_index.py` | ✅ FULLY BUILT | Read-only codebase awareness (Iter 212m-246). Warmed at startup (main.py:659). |
| `ora_chat/cost_tracker.py` | ✅ FULLY BUILT | Per-call token/cost logging + HARD monthly budget enforcement (Iter 212m-238). |
| `ora_chat/deep_research.py` | ⚠️ HALF BUILT | Auto multi-source research orchestration (Iter 212m-245). Depends on **Tavily** which the handoff summary flagged as dead in prod (HTTP 432 credits exhausted). Function likely silently no-ops when Tavily returns 432. |
| `ora_chat/grounding_check.py` | ✅ FULLY BUILT | CHEAP post-response grounding check (Iter 212m-254). |
| `ora_chat/hallucination_classifier.py` | ⚠️ HALF BUILT | Batch job reads unreviewed rows from `ora_hallucination_log` — need to verify the batch actually runs (no obvious scheduler in main.py; may be admin-triggered only). |
| `ora_chat/house_rules.py` | ✅ FULLY BUILT | Admin-editable behavior rules (Iter 212m-239). Endpoints wired. |
| `ora_chat/prompt_snapshot.py` | ✅ FULLY BUILT | Persist assembled system prompt for every assistant turn (Iter 264 Fix C). |
| `ora_chat/providers.py` | ✅ FULLY BUILT | Thin streaming caller over OpenRouter (Iter 212m-238). |
| `ora_chat/router.py` | ✅ FULLY BUILT | Intent-based model routing (Iter 212m-238). |
| `ora_chat/safety.py` | ✅ FULLY BUILT | Non-negotiable safety primitives (Iter 212m-238 / 239). |
| `ora_chat/session.py` | ✅ FULLY BUILT | Mongo-backed conversation state with rolling-summary sliding window (Iter 212m-238). |
| `ora_chat/slash_commands.py` | ✅ FULLY BUILT | Deterministic slash-command dispatch (Iter 212m-238). Also the `/rule` command that drives correction rules (Item C). |

---

## 3. Remaining services — quick classification (135 modules audited by grep + docstring inspection)

Rather than tabulate every one, findings by category:

### 3.1 Vanguard scanner family
| File | Status | Reason |
|------|--------|--------|
| `vanguard_scanner.py` | ✅ FULLY BUILT | **43 imports** — highest of any service. Heavily used. |
| `vanguard_verify_agent.py` | ✅ FULLY BUILT | 16 imports. |
| `vanguard_config.py` | ✅ FULLY BUILT | 5 imports; configuration schema. |
| `vanguard_audit.py` | ✅ FULLY BUILT | 4 imports. |

### 3.2 Supabase family (2 modules)
| File | Status | Reason |
|------|--------|--------|
| `supabase_provisioner.py` | ⚠️ HALF BUILT | 24 imports (looks live). BUT — module docstring itself says "If `SUPABASE_MANAGEMENT_TOKEN` or `SUPABASE_ORG_ID` is missing every function silently no-ops." Neither env var is set. So all 24 call sites hit a **silent no-op path**. |
| `supabase_sweeper.py` | ⚠️ HALF BUILT | 13 imports. Cron scheduled at main.py:714. Same env-gate; silent no-op in prod. |

### 3.3 Vercel family (2 modules)
| File | Status | Reason |
|------|--------|--------|
| `vercel_platform_deploy.py` | ⚠️ HALF BUILT | 6 imports. Env-gated by `AUREM_VERCEL_PLATFORM_TOKEN` (not set). Docstring literally says "AUREM's own infra". Personal-Track deploy path requires this — feature is off. |
| `vercel_skills.py` | ✅ FULLY BUILT | 3 imports; different from platform_deploy — uses per-user Vercel PAT via `VERCEL_API_TOKEN` (which IS set). |

### 3.4 Scaffold + MCP + Auth (5 modules)
| File | Status | Reason |
|------|--------|--------|
| `mcp_scoped_tools.py` | ✅ FULLY BUILT | 23 imports; MCP tool surface. |
| `scaffold_llm.py` | ✅ FULLY BUILT | 19 imports; scaffold LLM prompts. |
| `scaffold_security_gate.py` | ✅ FULLY BUILT | 10 imports; security gate on scaffolded projects. |
| `scaffold_design_review.py` | ⚠️ HALF BUILT | 3 imports only — spot-check needed to confirm end-to-end wiring. |
| `services/mfa.py` (from Session 1 grep) | ⚠️ HALF BUILT | MFA endpoints exist (`routers/mfa.py`) but no evidence of a completed enrollment flow in the frontend. |

### 3.5 The remaining ~100 services
All have 1+ import site so none are strict orphans. **A full status per file would take a Session 3 — I did NOT deep-audit each one**. The findings above (Supabase silent no-op, Vercel platform silent no-op) are the pattern to watch for: **imported → silent no-op path** is worse than orphaned because dashboards will show them as "in use" while producing zero value.

**Recommendation**: For Session 3, focus deep audit on the top-10 highest-import services by import-count that haven't been checked yet (list them in order, spot-check for silent no-op patterns).

---

## 4. Scripts audit (33 files)

### 4.1 Called from workflows (definitely LIVE)
| Script | Called from |
|--------|-------------|
| `scripts/g21_security_scan.py` | `quality-gate.yml`, `ci.yml` |
| `scripts/pricing_copy_lint.py` | `ci.yml` |
| `scripts/timeout_audit.py` | `quality-gate.yml` |
| `backend/scripts/g1_route_smoke_sweep.py` | `qa-weekly.yml` (WIRED THIS ITER 367 · ITEM A) |

### 4.2 Orphans / one-shots
| Script | Status | Reason |
|--------|--------|--------|
| `backend/scripts/persona_drift_eval.py` | 🔌 UNWIRED | 0 refs anywhere. Was likely a one-shot eval. |
| `/app/scripts/build_favicons.py` | 🗑️ DEAD CODE | 0 refs. One-shot generator; favicons are already generated. |
| `/app/scripts/iter308_api_probe.py` | 🗑️ DEAD CODE | 0 refs. One-shot debug from iter308. |
| `/app/scripts/iter308_cleanup_test_locks.py` | 🗑️ DEAD CODE | 0 refs. One-shot cleanup from iter308. |
| `/app/scripts/iter308_db_probe.py` | 🗑️ DEAD CODE | 0 refs. Iter308 diagnostic. |
| `/app/scripts/iter308_sse_reaper_visibility_probe.py` | 🗑️ DEAD CODE | 0 refs. Iter308 diagnostic. |
| `scripts/init_prod_collections.py` | ❓ UNCLEAR | Founder-run once at bootstrap; not auto-wired. Fine to keep. |
| `scripts/migrate_iter34.py` | ❓ UNCLEAR | 1 ref (doc). Old migration — probably applied and dormant. |
| `scripts/architecture_health.py` | ❓ UNCLEAR | Likely admin-triggered. Not in any workflow. |

### 4.3 Shell scripts
| Script | Status |
|--------|--------|
| `scripts/blue_green_switch.sh` | ❓ UNCLEAR — no workflow reference; likely operator-run |
| `scripts/predeploy_gate.sh` | ❓ UNCLEAR — no workflow reference despite name |
| `scripts/rollback.sh` | ❓ UNCLEAR — no workflow reference; may be shell equivalent of the now-fixed Python rollback path |

**Recommendation**: The 6 items marked 🗑️ DEAD CODE are safe to delete — but confirm with founder that the iter308 debugging is truly done and that build_favicons is one-shot generator.

---

## 5. Workflows audit (8 files, all in `.github/workflows/`)

| File | Trigger | Jobs | Status |
|------|---------|------|--------|
| `auto-qa.yml` | push (any branch) + workflow_dispatch | `regression-locks` | ✅ FULLY BUILT — every push to any branch runs regression-locks |
| `auto_deploy.yml` | PR labeled/opened/sync + push to main/master | Multiple deploy steps | ✅ FULLY BUILT |
| `auto_push.yml` | workflow_dispatch + push to main | `sync` | ✅ FULLY BUILT |
| `ci.yml` | push + PR | Standard CI (lint, test, secret scan) | ✅ FULLY BUILT |
| `deploy.yml` | workflow_dispatch (manual only) | `deploy` | ⚠️ HALF BUILT — comment says "push pe trigger nahi hoga" — deployer_agent is used instead. This is intentional but confusing. |
| `qa-weekly.yml` | Mon 09:00 UTC + daily 09:00 UTC | `qa-weekly`, `g1-route-sweep` | ✅ FULLY BUILT (updated Iter 367 Item A) |
| `quality-gate.yml` | push (all branches) + PR | Multi-step quality gate | ✅ FULLY BUILT |
| `rebaseline-visual.yml` | workflow_dispatch only (must specify branch, blocked on main) | Rebaseline visual regression | ✅ FULLY BUILT |

**Cross-cutting**: The commented history in `quality-gate.yml` (Iter 306) shows a real bug was found: Emergent's "Save to GitHub" doesn't always push to main, so PR-only triggers silently skipped runs. The current hybrid trigger fixes it. Good defensive posture.

---

## 6. Frontend Personal-Track pages — reality vs. Item F scope proposal

**The Item F scope doc I wrote referenced files that don't exist**. Actual files:

| File on disk | Mounted in App.jsx? |
|--------------|--------------------|
| `pages/personal/ChooseTrack.jsx` | ✅ Yes (line 97) |
| `pages/personal/BuildHome.jsx` | ✅ Yes (line 98) |
| `pages/personal/DraftReview.jsx` | ✅ Yes (line 99) |
| `pages/personal/ShipProgress.jsx` | ✅ Yes (line 100) |
| `pages/personal/BuildSuccess.jsx` | ✅ Yes (line 101) |
| `pages/personal/PreviewPanel.jsx` | Imported by DraftReview.jsx — indirectly mounted |
| `pages/personal/_shell.jsx` | Shared shell — imported by 4 personal pages |

**Files my Item F scope doc referenced that DO NOT exist**:
- `PublishCheckpoint.jsx` — DOES NOT EXIST — scope doc error
- `Start.jsx` — DOES NOT EXIST — scope doc error
- `BuildLive.jsx` — DOES NOT EXIST — scope doc error

**Backend endpoints Personal-Track pages actually hit**:
- `POST /api/aurem-dev/auth/set-track` — exists in `routers/auth.py` ✅
- `POST /api/aurem-dev/scaffold/new-project` — exists in `routers/scaffold.py` ✅
- **Nothing else.** These 5 mounted pages hit exactly 2 endpoints — no loop, no ship, no rollback. This confirms Session 1's assessment: Personal Track is scaffold-only UI, not a functional product.

**Recommendation**: Update `/app/memory/PERSONAL_TRACK_SCOPE.md` file-name references to match reality before any Phase F.1 build starts.

---

## 7. Cross-cutting patterns — new findings from Session 2

### 7.1 Silent no-op services (imported but env-gated dead)
This is a NEW pattern beyond Session 1's "unwired" or "half-built": services that are imported dozens of times BUT run silent no-op paths in prod because their env vars aren't set. Callers think they're calling into a working system.

| Service | Import count | Actual runtime behavior |
|---------|-------------:|--------------------------|
| `supabase_provisioner.py` | 24 | Silent no-op — `SUPABASE_MANAGEMENT_TOKEN` / `SUPABASE_ORG_ID` not set |
| `supabase_sweeper.py` | 13 | Silent no-op — same gate |
| `vercel_platform_deploy.py` | 6 | Silent no-op — `AUREM_VERCEL_PLATFORM_TOKEN` not set |
| (potentially) `ora_chat/deep_research.py` | many | Depends on Tavily which returns 432 in prod per handoff — need confirmation |

**This is more dangerous than "unwired" because dashboards will show these as "in use".**

### 7.2 Two tests likely broken by Iter 367 (needs a fix session)
- `test_iter212m9_deploy_ui::test_save_config_with_project_id_writes_scoped_row` + `test_save_config_without_project_id_writes_user_level` — I added 3 new fields to the deploy config dict (target, remote_dir, ftp_tls). Test asserts on exact dict shape.
- `test_iter212m119_langfuse_tracing::test_call_llm_with_meta_wraps_inner_with_trace` — I deleted `services/llm_router.py` which collapsed a code branch this test walked.

Both are 5-minute fixes. **NOT DONE THIS SESSION** per your instruction.

### 7.3 Personal Track scope doc has 3 wrong file names
`/app/memory/PERSONAL_TRACK_SCOPE.md` I wrote in Item F references `PublishCheckpoint.jsx`, `Start.jsx`, `BuildLive.jsx` — none exist. Real files are `ChooseTrack`, `ShipProgress` instead. Would confuse any future dev implementing Phase F.1.

### 7.4 20+ background tasks in main.py startup
Enumerated 20+ `asyncio.create_task(...)` calls at startup. Each one is a long-lived background loop. **None of them are supervised for restart on crash.** A single failing task silently stops working, and the founder only notices when data doesn't appear.

Notable startup tasks (line numbers):
- `daily_digest` (main.py:296)
- `integration_health_cron` (301)
- `correction_rules_graduation_task` (332, added Iter 367)
- `loop_housekeeping` (414)
- `_ensure_loop_safety_indexes` (457)
- `_orphan_running_fix_jobs` (485)
- `_backfill_dev_users_created_at` (552)
- `_backfill_dev_users_track` (582)
- `_ensure_ora_learning_indexes` (597)
- `_ensure_iter272_indexes` (642)
- `_warm_codebase_index` (659)
- `nudge_cron` (668, gated)
- `backup_cron` (681, gated)
- `ora_canary_task` (695, gated by ORA_CANARY_ENABLED=1)
- `supabase_sweeper_task` (714, silent no-op per §7.1)
- `preview_sweeper_task` (735)
- More below line 735 not enumerated.

**Suggestion (not action)**: Consider a task-supervisor wrapper that logs + increments a counter when any of these dies unexpectedly.

---

## 8. Cumulative status (Session 1 + Session 2)

**Backend services**: ~151 total. Session 1 checked 15. Session 2 spot-checked ~55 more (loop + ORA + vanguard + supabase + vercel + mcp + scaffold + specific highlights). **~80 services still not deep-audited individually** — but no orphans found, all have 1+ import.

**Backend routers**: 60. All mounted. Session 1 spot-checked ~15 in depth. Remaining 45 not deep-audited.

**Frontend components**: 89. Session 1 confirmed all have 1+ import. Session 2 confirmed the 2 orphans (Session 1's PublicStatsStrip + SaveToGithubDialog) were deleted in Iter 367 STEP 1. Individual component behavior not deep-audited.

**Frontend pages**: 61. All mounted. Personal Track 5 pages surveyed in §6 above.

**Scripts**: 33. Session 2 fully audited. 6 confirmed dead/orphaned, 3 unclear.

**Workflows**: 8. Session 2 fully audited. All wired.

**Tests**: 3897 collected. 3799 pass (97.5%). 28 fail (all pre-existing patterns). 2 potentially caused by Iter 367 (§7.2). 233 quarantined legacy.

---

## 9. Session 3 recommended focus (if you continue)

Priority order — recommend picking one:

1. **Fix the 2 Iter 367-caused test failures** — 5 min each. Cleanest to knock out before any other work.
2. **Fix Supabase + Vercel platform env-gate silent no-ops** — surface which features are actually dark in prod so the founder knows what to unlock or delete.
3. **Deep-audit the top 10 highest-import services** — one Session 3 could cover the ~80 services still unchecked with better prioritization than my alphabetical Session 2 pass.
4. **Reconcile Personal-Track scope doc with reality** — 20 min to update the 3 file-name errors so Phase F.1 doesn't hit surprise mismatches.
5. **Add a supervised-task wrapper for the 20+ startup background tasks** — one wrapper, defense against silent death.
6. **Clean up the 6 confirmed dead scripts** (build_favicons + iter308_*  x4 + persona_drift_eval) — small PR but keeps the tree honest.
7. **Investigate the 26 non-Iter-367 test failures** for real fixes (grouped by root cause in §0) — full session's worth of work.

---

**Session 2 pacing**: Discovery-only, ~90 minutes of tool time. No files edited except this report file. Zero fixes applied per your instruction.
