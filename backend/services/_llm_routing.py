"""services/_llm_routing.py — Backward-compat shim (Session C, Sub-step 2).

The real module now lives at `services/llm/_routing.py`. This shim
preserves the ~10 legacy test paths that import `services._llm_routing`
directly.

DO NOT add new code here. Import from `services.llm._routing` (or from
`services.llm` for re-exported public names).

TODO(session-Z): delete once legacy tests migrate.
"""
# ruff: noqa: F401 — re-export module only.
from services.llm._routing import (
    MAX_TOKENS,
    TEMPERATURE,
    _DEEPSEEK_HOSTS,
    _CLAUDE_MODES,
    LONGCAT_ENABLED,
    COUNCIL_B_GLM_ENABLED,
    CEO_RESCUE_ENABLED,
    CEO_PRIMARY_TIMEOUT_S,
    CEO_RESCUE_MODEL,
    council_a_primary_model,
    council_b_primary_model,
    cap_for,
    temperature_for,
)
