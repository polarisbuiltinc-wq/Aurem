# LEGACY TEST QUARANTINE — AUDIT & TRIAGE REPORT

**Generated**: 2026-07-30 · **Audit source**: `tests/legacy_quarantine.txt` (270 nodeids across 118 files, quarantined 2026-07-29 by iter 345 founder ruling).

## Executive summary

- **270 quarantined tests** currently excluded from the required CI gate (only reported non-blocking via the "Legacy lane" step in `ci.yml`).
- Live regression run against current codebase: **216 fail, 30 pass, 21 collection errors, 1 skipped** (out of 268 collectible; 2 files hit collection errors on missing `REACT_APP_BACKEND_URL`).
- ~30 tests already pass — some of them are protecting real invariants and can be re-enabled after light patching.

## Bucket categorisation (module-name heuristic pass)

| Bucket | Files | Nodeids | Action |
|---|---|---|---|
| **A — Live features** | 29 | 103 | **FIX + un-quarantine.** These cover chat persistence, auth, subscription_tiers, ship_turn_index, tool reliability, ORA circuit breaker, activation funnel, LLM provider fallback, security gate, OAuth signup, ship_wall, Vanguard 2-round, etc. — every one of these features is still live in prod. |
| **B — Dead / replaced** | 9 | 41 | **DELETE.** Modules: `iter212m9_deploy_http`, `iter212m21_ask_advisor_glm`, `iter212m212_advisor_screen_share`, `iter130_layered_persona`, `iter123g_seo_geo_consistency`, `iter36_anti_hallucination`, `iter107_ora_circuit_breaker` (superseded by Guard 17 breaker), `iter118_route_cache` (replaced). Each replaced by newer iter test files or the whole feature was removed. |
| **C — Env / network flaky** | 2 | 3 | **Fix harness, not code.** `test_llm_provider.py` mocks real HTTP; `test_integration_health_cron.py` needs a fake DB fixture. |
| **UNCATEGORIZED** | 78 | 123 | Needs one-by-one review — most are iter-numbered tests where the feature name doesn't clearly indicate whether the feature is alive today. |

### Bucket B — safe to delete now (post double-check)

```
tests/test_iter212m9_deploy_http.py         (7 nodeids) — old deploy HTTP surface, replaced by /hosted-deploy
tests/test_iter212m21_ask_advisor_glm.py    (5 nodeids) — GLM ask-advisor path deprecated
tests/test_iter212m212_advisor_screen_share (5 nodeids) — screen-share feature never shipped
tests/test_iter130_layered_persona.py       (5 nodeids) — persona layering removed
tests/test_iter123g_seo_geo_consistency.py  (5 nodeids) — SEO/GEO now covered by prerender tests
tests/test_iter36_anti_hallucination.py     (4 nodeids) — replaced by services/hallucination_guard.py tests
tests/test_iter107_ora_circuit_breaker.py   (4 nodeids) — superseded by Guard 17 (test_iter360)
tests/test_iter118_route_cache.py           (3 nodeids) — route cache mechanism replaced
tests/test_iter88_admin_and_wall.py         (partial) — 1 nodeid, `shipwall_imports_shell` — Shell import obsolete post-RailShell
```

**Recommendation**: delete these files (and their entries from `legacy_quarantine.txt`) in one commit with the deletion rationale in `CHANGELOG.md` and a check against `docs/DELETE_GATE.md` per the delete-gate policy.

### Bucket A — highest-priority fixes (files with ≥ 5 nodeids)

```
tests/test_aurem_p0_bugs.py                 (11) — project CRUD + chat/feedback surface
tests/test_iter212m32_onboarding_nudge.py    (6) — onboarding banner flow
tests/test_iter212m121_fix_pipeline.py       (6) — fix pipeline (still live)
tests/test_ship_turn_index.py                (5) — ship-to-turn indexing
tests/test_iter267_url_fetch_retry.py        (5) — URL fetcher with retries
tests/test_iter138_execute_bash_tool.py      (5) — execute_bash tool (founder-only)
tests/test_aurem_chat_persistence.py         (5) — chat/history round-trip
```

Most of these fail because the fixture no longer creates a valid JWT the same way (auth helper changed post-Iter 212m-104). One fixture fix per file will un-quarantine most nodeids.

## Known root cause of the mass quarantine

Iter 345 founder ruling (2026-07-29) recorded:

> "259 pre-existing failures (iter36–iter267 era) are quarantined via `@pytest.mark.legacy` (`tests/legacy_quarantine.txt`), tracked here, NOT fixed, NOT deleted, and NEVER block this required check."

CI ships this as a non-blocking artifact — `.emergent/legacy-test-report.md`. So the quarantine is a **known accepted risk**, not a blind spot. The remediation plan below reduces the risk without breaking CI.

## Remediation plan (multi-session, gated)

### Phase 1 — Delete bucket B (single session)
1. `git rm` the 9 files above.
2. Drop matching lines from `tests/legacy_quarantine.txt`.
3. Add rationale to `memory/CHANGELOG.md`.
4. Confirm CI still green (no imports pointed at deleted files).

### Phase 2 — Fix bucket C (single session)
1. Repoint `test_llm_provider.py` at the current `services/llm.py` behaviour (single stubbed provider chain, no live HTTP).
2. Give `test_integration_health_cron.py` an in-memory fake DB.
3. Un-quarantine both files.

### Phase 3 — Fix bucket A (**multi-session**, one file at a time)
1. Repair the auth-fixture drift (single helper change lands ~40 nodeids).
2. Re-run each file; fix any remaining fixture drift.
3. Un-quarantine only after 3 consecutive clean local runs + 1 clean CI run.
4. **STOP IF**: any bucket-A test failure surfaces a real prod bug — file a P0 issue and pause the audit until fixed.

### Phase 4 — Categorise UNCATEGORIZED
1. Grep each `test_iter*.py` for the `services/` module it imports and confirm the module still exists + is reachable from `main.py::include_router`.
2. Split into buckets A/B/C by hand — 78 files, ~5 min each = ~7 hours of focused work.

### Phase 5 — Remove the `legacy` marker + merge back into main gate
1. Once buckets A + C are green and B is gone, delete `tests/legacy_quarantine.txt`.
2. Remove the "Legacy lane" step from `.github/workflows/ci.yml`.
3. Remove the `pytest_collection_modifyitems` hook in `tests/conftest.py`.

## Estimated effort

| Phase | Effort | Deliverable |
|---|---|---|
| 1 (Bucket B delete) | 1 session | 9 files removed, ~41 tests off the list, CI still green |
| 2 (Bucket C harness fix) | 1 session | 3 tests re-enabled |
| 3 (Bucket A fixes) | 4-6 sessions | ~103 tests re-enabled |
| 4 (UNCATEGORIZED review) | 2-3 sessions | 123 tests bucketed |
| 5 (Cleanup) | 1 session | Legacy marker gone |
| **Total** | **9-12 focused sessions** | 270 tests back in the main gate |

## Status of this session

- **Triage only** — no test fixes applied yet.
- Reason: session budget is being spent on Loop Mode Phase 1-3, pricing copy fix, token hard-stop enforcement (all in-flight in same task). Fixing 270 quarantined tests properly requires its own dedicated multi-session effort per the plan above.
- Recommend running **Phase 1 (Bucket B delete)** in the next dedicated session — cheapest wins, no code changes needed, immediately reduces false-negative rate in the legacy lane report by 15%.
