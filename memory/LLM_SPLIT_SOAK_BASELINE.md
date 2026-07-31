# LLM.py 3-Way Split · Session 5 · Soak-Time Baseline

**Timestamp**: 2026-07-31 ~19:00 UTC (post Phase 2 deploy)
**Live URL**: https://auremcto.com
**Instruction from founder**: Pause between Phase 2 and Phase 3. Monitor prod
for a few hours before starting Phase 3 in a fresh session.

## Baseline signals to watch (no code changes during soak)

### `/api/health` — expected shape
```json
{
  "ok": true,
  "service": "aurem-dev",
  "db": true,
  "build_hash": "m1c61197",
  "env": "production",
  "council_a_model": "meituan/longcat-2.0",   ← MUST stay a LongCat slug
  "longcat_live": true,                        ← MUST stay true
  "longcat_enabled": true
}
```

**Red flag**: If `council_a_model` flips to `"z-ai/glm-5.2"` for more than
one probe window (~15 min) → the deferred-import chain broke somewhere.
Steps to diagnose:
1. Grep the pod's `/var/log/supervisor/backend.err.log` for
   `LongCat probe` and `LongCat unavailable` messages.
2. If a network / rate-limit error, no action — will self-heal on next probe.
3. If it says `services._llm_probes` is missing or import failed → hard bug.

### `/api/health/ora-breaker` — expected shape
```json
{
  "ok": true,
  "breaker": {
    "open": false,
    "api_key_configured": true
  }
}
```

**Red flag**: `open: true` for more than a few min → OpenRouter is down OR
the ModuleType hook broke somehow (unlikely — has direct regression coverage).

### `/api/aurem-dev/admin/qa/guard10-founder-alerts` — G10 health
Should still return `state: GREEN, enabled: true` post-Phase-2. The
Cloudflare-1010 fix + Resend key rotation are independent of the LLM split.

## Phase 3 pre-flight (for next session)

Do NOT start Phase 3 until:
- [ ] Prod baseline above stayed stable for at least a few hours.
- [ ] No new "LongCat unavailable" alerts in prod logs.
- [ ] G10 endpoint still GREEN.

Phase 3 scope (unchanged from `LLM_SPLIT_MIGRATION_PLAN.md`):
1. Convert `services/llm.py` → `services/llm/__init__.py` package.
2. Move `services/_llm_state.py` → `services/llm/_state.py`.
3. Move `services/_llm_routing.py` → `services/llm/routing.py`.
4. Move `services/_llm_probes.py` → `services/llm/probes.py`.
5. Update internal cross-imports (each file's `from services._llm_*` → `from services.llm.*`).
6. Verify the ModuleType hook still gets installed on the `__init__.py` module.
7. Test cumulative: 41+ Session-5 tests + all LLM/council/routing/CEO suites.

## Test coverage laid down so far (regression armor)

- `tests/test_session5_llm_split_phase0a.py`  — 7 tests
- `tests/test_session5_llm_split_phase1.py`   — 13 tests
- `tests/test_session5_llm_split_phase2.py`   — 12 tests
- `tests/test_session5_item3_llm_hygiene.py`  — 6 tests (updated for Phase 2)
- `tests/test_session5_item5_ora_chat_silent_catch.py` — 6 tests
- `tests/test_session5_item5_founder_alert_cf1010_regression.py` — 3 tests

Total NEW regression tests this session: **47**.
Broader legacy suite: **119/119** across all LLM-touched files.

## What NOT to touch during soak

- `services/llm.py`, `services/_llm_state.py`, `services/_llm_routing.py`, `services/_llm_probes.py`
- Anything in `routers/admin.py::council_health`, `routers/feature_window.py::feature_window_status`, or `main.py::health` (the 3 bare-import sites).
- The ModuleType hook installed at the bottom of `services/llm.py`.

## What IS safe during soak

- Product-level features unrelated to the LLM call path.
- Frontend changes.
- Any router NOT importing from `services.llm` symbols added in Phases 0a/1/2.
