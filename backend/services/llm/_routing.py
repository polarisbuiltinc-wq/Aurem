"""
services/_llm_routing.py — LLM.py 3-Way Split · Phase 1

Pure-function/pure-constant surface pulled out of `services/llm.py`.

Scope (moved from llm.py):
  - `MAX_TOKENS` and `TEMPERATURE` per-mode budget dicts
  - `_DEEPSEEK_HOSTS`, `_CLAUDE_MODES`
  - V2 council routing flags: `LONGCAT_ENABLED`, `COUNCIL_B_GLM_ENABLED`,
    `CEO_RESCUE_ENABLED`, `CEO_PRIMARY_TIMEOUT_S`, `CEO_RESCUE_MODEL`
  - `cap_for(mode)`, `temperature_for(mode)` — trivial dict lookups
  - `council_a_primary_model()`, `council_b_primary_model()` — pure
    functions with the caveat that they DEFERRED-import `services.llm`
    inside the function body to read `LONGCAT_LIVE` (still owned by
    llm.py during Phase 0b) + `_LONGCAT_MODEL`, `_GLM_MODEL`, and
    `_deepseek_model()` (owned by llm.py until Phase 4).

That deferred import is deliberate: importing `services.llm` at
this file's top level would create a circular import
(`llm.py` → `_llm_routing.py` → `llm.py`). Python handles that
cycle if the reference happens inside a function body — the
module is fully bootstrapped by first call time.

Nothing in this module has runtime-mutable state. Every symbol
is either an env-derived constant (set once at import) or a pure
function. Contrast with `_llm_state.py` which holds the mutable
ContextVar + probe-snapshot dict.

Re-exported unchanged from `services/llm.py` so external callers
(`from services.llm import cap_for`, `services.llm.MAX_TOKENS`,
etc.) see identical behaviour.
"""
from __future__ import annotations

import os


# ═══ Per-mode budgets ═══════════════════════════════════════════
# Iter 212m-26 — Production fix: raised `chat` from 1500 → 4000.
# A 1500-token ceiling on chat replies meant GLM-5.2 was truncating
# multi-paragraph answers mid-sentence, surfacing as the "ORA only
# replies one line then stops" bug. Allow an env override so the
# value can be tuned in production without redeploying.
MAX_TOKENS = {
    "chat":    int(os.getenv("LLM_CHAT_MAX_TOKENS", "4000")),
    "code":    int(os.getenv("LLM_CODE_MAX_TOKENS", "3500")),
    "review":  int(os.getenv("LLM_REVIEW_MAX_TOKENS", "4096")),
    "analysis": int(os.getenv("LLM_ANALYSIS_MAX_TOKENS", "2000")),
    # Iter 212m-161 — Ask Advisor dedicated budget (was hard-coded
    # in routers/chat.py before the P1 advisor-fallback refactor).
    "advisor": int(os.getenv("LLM_ADVISOR_MAX_TOKENS", "2500")),
    # Iter 212m-165 — Council C dedicated mode (writing tasks).
    # DeepSeek-only, lighter budget than chat because writing
    # outputs tend to be tighter (emails, copy, drafts).
    "write":   int(os.getenv("LLM_WRITE_MAX_TOKENS", "2500")),
    "title":     30,
    "default": int(os.getenv("LLM_DEFAULT_MAX_TOKENS", "1500")),
}

# Temperature per mode
TEMPERATURE = {
    "code":    0.0,
    "review":  0.0,
    "title":   0.0,
    "chat":    0.7,
    "analysis": 0.4,
    # Iter 212m-161 — Ask Advisor (slightly creative but factual).
    "advisor": 0.2,
    # Iter 212m-165 — Council C writing tasks (slightly more creative
    # than chat — readers expect personality in marketing copy).
    "write":   0.8,
    "default": 0.3,
}

_DEEPSEEK_HOSTS = ["deepseek", "streamlake", "deepinfra", "novita"]

# Modes that use Claude for better code quality
_CLAUDE_MODES = {"code", "review"}


# ═══ V2 council routing flags ═══════════════════════════════════
# Per /app/memory/V2_ROUTING_ROADMAP.md. All flags default False so
# existing callers see zero behaviour change until env enables them.
#
#   LONGCAT_ENABLED       → Council A primary swaps GLM-5.2 → LongCat-2.0
#                           Claude Sonnet 4.5 remains the rescue for pro/maxx.
#   COUNCIL_B_GLM_ENABLED → mode="analysis" primary uses GLM-5.2 with DeepSeek
#                           rescue.  When False, mode="analysis" behaves like
#                           mode="chat" (DeepSeek only) — Council B unchanged.
#   CEO_RESCUE_ENABLED    → CEO judge wraps its GLM-5.2 call in a
#                           2 s timeout; on timeout falls back to DeepSeek.
LONGCAT_ENABLED       = os.getenv("LONGCAT_ENABLED", "false").lower() == "true"
COUNCIL_B_GLM_ENABLED = os.getenv("COUNCIL_B_GLM_ENABLED", "false").lower() == "true"
CEO_RESCUE_ENABLED    = os.getenv("CEO_RESCUE_ENABLED", "false").lower() == "true"

# CEO rescue config (used when CEO_RESCUE_ENABLED=True — see core/loop hub)
CEO_PRIMARY_TIMEOUT_S = float(os.getenv("CEO_PRIMARY_TIMEOUT_S", "2.0"))
CEO_RESCUE_MODEL      = os.getenv("CEO_RESCUE_MODEL", "deepseek/deepseek-chat")


# ═══ Pure helpers ═══════════════════════════════════════════════
def cap_for(mode: str) -> int:
    return MAX_TOKENS.get(mode, MAX_TOKENS["default"])


def temperature_for(mode: str) -> float:
    return TEMPERATURE.get(mode, TEMPERATURE["default"])


def council_a_primary_model() -> str:
    """Returns the Council A primary model id.

    V2: LongCat-2.0 when LONGCAT_ENABLED=True AND the live-probe flag
    `LONGCAT_LIVE` is True; otherwise legacy GLM-5.2.  The flag is
    refreshed on every supervisor restart by `probe_longcat_availability()`,
    so the moment LongCat publishes upstream the next boot picks it up
    without a code change.

    Deferred `services.llm` import — see module docstring.
    """
    # Late binding so probe mutations to LONGCAT_LIVE propagate here,
    # AND so we don't create an import cycle at module load time.
    from services import llm as _llm
    if LONGCAT_ENABLED and _llm.LONGCAT_LIVE:
        return _llm._LONGCAT_MODEL
    return _llm._GLM_MODEL


def council_b_primary_model() -> str:
    """Returns the Council B primary model id.

    V2: GLM-5.2 when COUNCIL_B_GLM_ENABLED=True, else DeepSeek V3 (legacy).
    Council C is unchanged (always DeepSeek) and routed via mode="chat".
    """
    from services import llm as _llm
    if COUNCIL_B_GLM_ENABLED:
        return _llm._GLM_MODEL
    return _llm._deepseek_model()
