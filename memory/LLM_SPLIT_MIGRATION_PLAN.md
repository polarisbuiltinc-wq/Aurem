# LLM.py 3-Way Split — Migration Plan

**Status**: DEFERRED from Session 5 Item 3 · **Owner**: TBD (dedicated session)
**Reason for deferral**: Shared-state entanglement makes the "just move functions to files" naive approach unsafe on a 45-importer prod-critical module.

---

## Why this needs its own session

`services/llm.py` is 1,805 LOC. On a naive read the split looks trivial (extract 3 concerns into 3 files), but forensic analysis reveals three shared-state coupling points that must be handled first:

1. **`LONGCAT_LIVE` (module global, mutable)** — mutated by `probe_longcat_availability()` and `_call_longcat()`, READ by `call_a_primary_model()` and the mode-routing block in `_call_llm_with_meta_inner()`. If probes moves to `probes.py` and callers stay in `openrouter_client.py`, both files need to see the same live/dead flag.
2. **`_LONGCAT_LAST_PROBE` (module dict, mutable)** — snapshot state read by `routers/admin.py::council_health` (an EXTERNAL consumer).
3. **`_last_provider_ctx` (ContextVar)** — request-scoped provenance stash mutated by every HTTP client function (`_call_deepseek`, `_call_groq`, `call_openrouter_model`, `_call_deepseek_direct`). ContextVar is safe across async tasks but MUST be a single instance — if two modules each define their own copy, we'd get two shadows and lose all provenance.

The move-in-place approach breaks these. The safe pattern is a **`_state.py` shared submodule** that owns the mutable state, imported by all three target modules.

---

## Target architecture

```
services/
├── llm.py              # KEEP — re-export shim only, matches existing 45-importer surface
├── llm/                # NEW package (rename llm.py → llm/__init__.py under the hood)
│   ├── __init__.py     # public surface re-exports (call_llm, call_llm_with_meta, etc.)
│   ├── _state.py       # LONGCAT_LIVE, _LONGCAT_LAST_PROBE, _last_provider_ctx,
│   │                   # _new_provenance_slot, _set_last_provider,
│   │                   # get_last_provider, reset_last_provider
│   ├── probes.py       # probe_longcat_availability, periodic_longcat_reprobe,
│   │                   # call_emergent_watchdog
│   ├── routing.py      # cap_for, temperature_for, council_a_primary_model,
│   │                   # council_b_primary_model, MAX_TOKENS, TEMPERATURE,
│   │                   # LONGCAT_ENABLED, COUNCIL_B_GLM_ENABLED, CEO_RESCUE_ENABLED,
│   │                   # _CLAUDE_MODES, _DEEPSEEK_HOSTS
│   └── openrouter_client.py  # _call_deepseek, _call_deepseek_direct, _call_claude,
│                             # _call_glm, _call_longcat, _call_groq,
│                             # _load_groq_house_rules, _free_fallback_models,
│                             # _is_fallback_worthy, _retryable, _retry_delay,
│                             # _openrouter_key, _deepseek_model, _groq_key,
│                             # _deepseek_direct_key, call_openrouter_model,
│                             # OPENROUTER_URL, _RETRY_STATUS, _MAX_RETRIES,
│                             # _BASE_DELAY_S, _FALLBACK_STATUSES,
│                             # _DEFAULT_FREE_MODELS, _GROQ_MODEL,
│                             # _DEEPSEEK_DIRECT_URL, _DEEPSEEK_DIRECT_MODEL,
│                             # _GROQ_HOUSE_RULES_PATH, _CLAUDE_MODEL, _GLM_MODEL,
│                             # _LONGCAT_MODEL, CEO_PRIMARY_TIMEOUT_S, CEO_RESCUE_MODEL
```

`call_llm` and `call_llm_with_meta` stay in `__init__.py` (they're the entry points).
`_call_llm_with_meta_inner` — the 400-LOC routing state machine — also stays in `__init__.py` because it touches every module. It's the LAST candidate for a further split.

---

## External surface to preserve (verified via grep across 45 importers on 2026-07-31)

**Public** (imported by 2+ callers — MUST re-export from `__init__.py`):

| Symbol | Callers | Where it lives post-split |
|---|---:|---|
| `call_llm_with_meta` | 19 | `__init__.py` |
| `call_llm` | 9 | `__init__.py` |
| `call_openrouter_model` | 4 | `openrouter_client.py` |
| `cap_for` | 2 | `routing.py` |
| `temperature_for` | 2 | `routing.py` |
| `probe_longcat_availability` | 2 | `probes.py` |
| `_BASE_DELAY_S` | 2 | `openrouter_client.py` |
| `_CLAUDE_MODEL` | 2 | `openrouter_client.py` |
| `_GLM_MODEL` | 2 | `openrouter_client.py` |

**Private-but-external** (single imports — likely tests):

| Symbol | Callers | Where it lives |
|---|---:|---|
| `_MAX_RETRIES`, `_retry_delay` | 1 each | `openrouter_client.py` |
| `_call_groq` | 1 | `openrouter_client.py` |
| `_call_llm`, `_call_llm_adv` | 1 each | `openrouter_client.py` (aliases if needed) |
| `_deepseek_model` | 1 | `openrouter_client.py` |
| `_LONGCAT_LAST_PROBE` | 1 | `_state.py` (re-exported) |
| `MAX_TOKENS`, `CEO_RESCUE_ENABLED`, `CEO_PRIMARY_TIMEOUT_S` | 1 each | `routing.py` |
| `get_last_provider`, `reset_last_provider` | 1 each | `_state.py` |
| `call_emergent_watchdog`, `periodic_longcat_reprobe` | 1 each | `probes.py` |

Total re-exports from `__init__.py` for backward compat: ~22 symbols. Zero code churn in the 45 importers.

---

## Recommended session sequence (safe order)

1. **Prep**: Snapshot public API to a fixture (dict of every attr from `services.llm`). Any deviation trips a regression test.
2. **Phase 0 — Create `_state.py`**: Move `_last_provider_ctx`, `_new_provenance_slot`, `_set_last_provider`, `get_last_provider`, `reset_last_provider`, `LONGCAT_LIVE`, `_LONGCAT_LAST_PROBE`. Original `llm.py` imports from this new file. Run pytest — verify zero breakage. **Deploy nothing yet.**
3. **Phase 1 — Extract `routing.py`**: 100% pure functions (cap_for, temperature_for, council_*_primary_model, MAX_TOKENS, TEMPERATURE). Lowest risk. Test.
4. **Phase 2 — Extract `probes.py`**: LongCat probe + reprobe + emergent watchdog. All reads/writes shared state via `_state.py`. Test.
5. **Phase 3 — Convert `llm.py` → `llm/` package**: `git mv llm.py llm/__init__.py` (or manually via `mkdir + mv`). `__init__.py` still contains `_call_deepseek`, `_call_claude`, `_call_glm`, `_call_longcat`, `_call_groq`, `call_openrouter_model` + all their helper functions + `call_llm` + `call_llm_with_meta` + `_call_llm_with_meta_inner`. **Deploy after this checkpoint** — this is the biggest structural change and needs prod-verify.
6. **Phase 4 — Extract `openrouter_client.py`**: Move the 6 `_call_*` functions + helpers. `__init__.py` re-exports.
7. **Phase 5 — Optional**: Extract `_call_llm_with_meta_inner()` into its own `_router.py`. Highest value, highest risk (400 LOC of branching). Only if Phase 4 lands clean.

Each phase = 1 commit + 1 pytest full-suite run + 1 self-verify curl. **Do NOT batch phases**.

---

## Regression contract

- `services.llm.<any-existing-symbol>` must resolve identically before and after the split.
- Behavioural regression pytest: `tests/test_session5_item3_llm_hygiene.py::test_llm_module_still_imports_cleanly` locks 20+ symbols. Extend to cover more before starting.
- Full-suite pytest must be at or below the current baseline (**3854 pass / 22 deferred**) after every phase.
- Prod build_hash bump after each deploy.

---

## What NOT to do

- **Do not** batch this with any other feature work.
- **Do not** skip Phase 0 (`_state.py`). Every subsequent phase depends on it.
- **Do not** silently rename internal helpers. Keep names identical so grep-history still works.
- **Do not** attempt to fix behaviour bugs during the split. If you find one, log it in `SESSION_5_DEEP_AUDIT.md` and defer.

---

**End of plan. Total estimated effort: 4-6 hours of focused engineering time, with a fresh context window and no unrelated work stacked.**
