"""services/_llm_state.py — Backward-compat shim (Session C, Sub-step 2).

The real module now lives at `services/llm/_state.py` — the LLM package
internal shared-state submodule. This file is a paper-thin re-export so
the ~7 legacy test files that reference `services._llm_state` continue
to import successfully without an atomic mass-edit.

DO NOT add new code here. If you're writing new code, import from
`services.llm._state` directly (or from `services.llm`, which re-
exports the public names).

TODO(session-Z): once the legacy tests are updated to the new path,
delete this shim.
"""
# ruff: noqa: F401 — this file exists purely to re-export names.
from services.llm._state import (
    _new_provenance_slot,
    _last_provider_ctx,
    _set_last_provider,
    get_last_provider,
    reset_last_provider,
    _LONGCAT_LAST_PROBE,
)
