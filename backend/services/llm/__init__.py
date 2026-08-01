"""
services/llm.py — AUREM Dev LLM gateway

Iter 166 — Emergent SDK fully removed. ALL LLM traffic now goes
through OpenRouter (single key: OPENROUTER_API_KEY).

  chat / review / title  → DeepSeek via OpenRouter
  code / ship tasks      → Claude Sonnet 4.5 via OpenRouter
  maxx mode              → Claude + watchdog review pass (both OpenRouter)

Routing logic lives in call_llm_with_meta via `mode` param:
  mode="code"   → Claude Sonnet 4.5 (OpenRouter)
  mode="chat"   → DeepSeek V3 (OpenRouter)
  mode="review" → DeepSeek
  mode="title"  → DeepSeek
  mode="default"→ DeepSeek

Privacy:
  DeepSeek path: data_collection=deny (OpenRouter enforced)
  Claude path:   anthropic/claude-sonnet-4-5-20250929 via OpenRouter
"""
from __future__ import annotations
import os
import asyncio
import json
import logging
import random
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ─── Session D · D-2a — OpenRouter transport moved to sibling module ────
# `OPENROUTER_URL`, retry policy constants, `_FALLBACK_STATUSES`,
# `_DEFAULT_FREE_MODELS`, the 4 pure classifier helpers
# (`_free_fallback_models`, `_is_fallback_worthy`, `_retryable`,
# `_retry_delay`) and the unified `call_openrouter_model` entry-point
# now live in `services/llm/openrouter_client.py`. Re-imported here
# so every legacy call site inside this file (and any external caller
# doing `from services.llm import call_openrouter_model`) resolves
# unchanged.
from .openrouter_client import (
    OPENROUTER_URL,
    _RETRY_STATUS,
    _MAX_RETRIES,
    _BASE_DELAY_S,
    _FALLBACK_STATUSES,
    _DEFAULT_FREE_MODELS,
    _free_fallback_models,
    _is_fallback_worthy,
    _retryable,
    _retry_delay,
    call_openrouter_model,
)

# ─── Session D · D-2b — Groq transport moved to sibling module ────────
# `_GROQ_MODEL`, `_GROQ_HOUSE_RULES_PATH`, `_load_groq_house_rules`,
# `_groq_key`, and `_call_groq` now live in
# `services/llm/groq_client.py`. Re-imported here so legacy callers
# (`routers/chat.py`, `routers/suggestions.py`, existing tests, and
# the `_call_deepseek` fallback ladder below) resolve unchanged.
#
# MONKEYPATCH-CONTRACT NOTE: tests that monkeypatch
# `_GROQ_HOUSE_RULES_PATH` must target the canonical module
# (`services.llm.groq_client._GROQ_HOUSE_RULES_PATH`), NOT this
# re-export binding — patching the re-export leaves
# `_load_groq_house_rules()` reading the real path.
from .groq_client import (
    _GROQ_MODEL,
    _GROQ_HOUSE_RULES_PATH,
    _load_groq_house_rules,
    _groq_key,
    _call_groq,
)


# ─── Iter 212m-51 — DeepSeek direct API as second-hop fallback ────────
# Independent vendor (api.deepseek.com, separate billing account from
# OpenRouter) — covers the case where the user's OpenRouter credits
# are exhausted but they still want PAID quality before dropping to
# the free tier. Sits between OpenRouter primary and the OR :free
# chain so the priority order is:
#   1. OpenRouter primary (paid)        — best routing flexibility
#   2. DeepSeek direct (paid)           — independent vendor failover
#   3. OpenRouter :free chain           — free models, throttled
#   4. Groq emergency (free)            — true last resort
#
# Per the integration playbook (Feb 2026): model slug pinned to
# `deepseek-v4-flash` (the legacy `deepseek-chat` / `deepseek-coder`
# aliases hard-deprecate on 2026-07-24). DeepSeek's API is OpenAI-
# compatible so the existing payload shape passes straight through.
# ─── Session D · D-2d — DeepSeek direct-API config + _call_deepseek_direct
# moved to `openrouter_providers.py` (co-located with the OpenRouter-routed
# _call_deepseek main entry). See re-import block above.


# Iter 212m-50 — Groq-only house rules. The file is read once at
# module load (cheap, ~1 KB) and silently absent → defaults apply.
# These rules nudge Groq toward the same shape/voice that ORA uses
# on GLM-5.2 / Claude so the fallback feels seamless to the user.
# Iter 212m-49 · Session 5 Phase 0a — provenance stash + LongCat
# probe snapshot moved into `services/_llm_state.py`. The state
# lives in a single canonical place so the upcoming `probes.py` /
# `openrouter_client.py` extractions can read/write the same
# ContextVar without divergent shadow copies.
#
# Everything below is a RE-EXPORT at module scope so external
# callers (`from services.llm import get_last_provider`, tests
# doing `llm._LONGCAT_LAST_PROBE["error"]`, etc.) keep working
# byte-for-byte identically to pre-split behavior.
from ._state import (
    _new_provenance_slot,
    _last_provider_ctx,
    _set_last_provider,
    get_last_provider,
    reset_last_provider,
    _LONGCAT_LAST_PROBE,
)
from contextvars import ContextVar  # noqa: F401 — kept for existing `llm.ContextVar` external access





# ─── Session D · D-2b — _call_groq moved to groq_client.py ──────────────
# The Groq emergency transport lives in `services/llm/groq_client.py`.
# It's re-exported via the top-of-file `from .groq_client import ...`
# block so `_call_deepseek` (below) and external callers still find it
# on `services.llm`.


# ─── Session D · D-2a — pure classifier helpers moved to openrouter_client.py ──
# `_free_fallback_models`, `_is_fallback_worthy`, `_retryable`, and
# `_retry_delay` are now re-exported via the top-of-file
# `from .openrouter_client import ...` block. Their definitions live
# in one place so internal changes (e.g. adding a new HTTP status to
# the retry set) touch exactly one module.


# Token caps per mode.
# Iter 212m-26 · Session 5 Phase 1 — `MAX_TOKENS`, `TEMPERATURE`,
# `_DEEPSEEK_HOSTS`, `_CLAUDE_MODES` and the V2 council routing
# flags moved into `services/_llm_routing.py`. Re-exported unchanged
# at module scope so all 45 importers keep working:
#   `from services.llm import MAX_TOKENS`  →  still resolves.
#   `services.llm.LONGCAT_ENABLED`         →  still resolves.
# `cap_for()`, `temperature_for()`, `council_a_primary_model()`,
# `council_b_primary_model()` are defined below (re-imported near
# their previous location) so the ordering — `_call_groq` still
# references them — stays correct.
from ._routing import (
    MAX_TOKENS,
    TEMPERATURE,
    _DEEPSEEK_HOSTS,
    _CLAUDE_MODES,
    LONGCAT_ENABLED,
    COUNCIL_B_GLM_ENABLED,
    CEO_RESCUE_ENABLED,
    CEO_PRIMARY_TIMEOUT_S,
    CEO_RESCUE_MODEL,
    # Session 5 · Phase 1 — routing helpers.
    council_a_primary_model,
    council_b_primary_model,
    cap_for,
    temperature_for,
)

# OpenRouter model strings
# Iter 212m-193 — Swapped Council A primary from meituan/longcat-2.0
# (upstream dead — HTTP 400 "is not a valid model ID") to Claude
# Sonnet 4.5 after an A/B run against GPT-5.2 on two failing Ask
# Advisor prompts (README read + routers/ list). Sonnet 4.5:
#   • 2/2 clean fenced-JSON tool call emissions (parser's happy path)
#   • sensible 1-call-per-turn behaviour (GPT-5.2 emitted 300+ globs)
#   • ~1.85s average latency (GPT-5.2 was 100× slower)
# Full run in `backend/tests/manual_ab_model_swap.py` — re-run to
# compare against a future candidate before swapping again.
# Session D · D-2c — `_LONGCAT_MODEL` moved to `openrouter_providers.py`
# alongside `_CLAUDE_MODEL` and `_GLM_MODEL`. See the re-import block
# further down. The AB-eval commentary above stays for archaeology.


# Iter 212m-160 — LongCat live-availability flag.
# Session 5 · Phase 2 — `LONGCAT_LIVE` (canonical mutable bool),
# `probe_longcat_availability()`, `periodic_longcat_reprobe()`,
# and `_deepseek_model()` moved into `services/_llm_probes.py`.
#
# `LONGCAT_LIVE` reads route through the module `__getattr__` defined
# at the bottom of this file so external callers still resolve
# `services.llm.LONGCAT_LIVE`. Writes route through the custom
# `ModuleType.__setattr__` also installed at the bottom so
# `llm_mod.LONGCAT_LIVE = X` (tests, admin reprobe) keeps working
# and lands on the canonical probe module state.
#
# Everything else is re-exported for byte-for-byte external compat.
from ._probes import (
    probe_longcat_availability,
    periodic_longcat_reprobe,
    _deepseek_model,
)







# Session 5 · Phase 2 — `probe_longcat_availability`, `periodic_longcat_reprobe`,
# and `_deepseek_model` are re-exported via the `from ._probes import
# ...` block at the top of this file. No wrapper stubs here — a redefinition
# with the same name would shadow the import and break identity checks
# (`llm.probe_longcat_availability is _llm_probes.probe_longcat_availability`).


# Session 5 · Phase 2 — `_deepseek_model` now lives in
# `services/_llm_probes.py` and is imported at the top of this file
# alongside the two probe coroutines. The `def _deepseek_model()`
# that used to sit here has been removed to avoid shadowing the
# re-export.


def _openrouter_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


# ─── Session D · D-2c — provider wrappers moved to openrouter_providers.py ──
# `_CLAUDE_MODEL`, `_GLM_MODEL`, `_LONGCAT_MODEL`, and the three
# thin adapters (`_call_claude`, `_call_glm`, `_call_longcat`) that
# wrap `call_openrouter_model` now live in
# `services/llm/openrouter_providers.py`. Re-exported here so legacy
# call sites (routers/chat.py bulk import, admin dashboards, tests
# using `monkeypatch.setattr(llm_mod, "_call_glm", ...)`) resolve
# unchanged.
#
# MONKEYPATCH-CONTRACT NOTE (Session D · D-2c): patches applied to
# `services.llm._call_glm` etc. still take effect for CALL SITES in
# THIS module (`_call_deepseek`, `_call_llm_with_meta_inner`) because
# name resolution walks the function's `__globals__` (= this module's
# namespace) — the re-export IS the name they resolve. But patches
# do NOT reach into `openrouter_providers._call_glm` from within
# `_call_longcat`'s fallback branch (that block resolves via
# `openrouter_providers`'s OWN namespace). Tests exercising that
# fallback branch should patch the canonical module.
from .openrouter_providers import (
    _CLAUDE_MODEL,
    _GLM_MODEL,
    _LONGCAT_MODEL,
    _call_claude,
    _call_glm,
    _call_longcat,
    # Session D · D-2d — DeepSeek path.
    _DEEPSEEK_DIRECT_URL,
    _DEEPSEEK_DIRECT_MODEL,
    _deepseek_direct_key,
    _call_deepseek_direct,
    _call_deepseek,
)


# ── DeepSeek path (chat, review, title) ─────────────────────────────────────

# ─── Session D · D-2d — _call_deepseek moved to openrouter_providers.py ──
# The DeepSeek primary path (OpenRouter → DeepSeek-direct → OR :free →
# Groq emergency) now lives alongside the other OpenRouter-routed
# providers. Re-exported via the block earlier in this file so all
# call sites (`call_llm` below, `chat.py` bulk import, etc.) resolve
# unchanged.


# ─── Session D · D-2c — _call_claude / _call_glm / _call_longcat ──
# These 3 provider wrappers moved to `openrouter_providers.py`.
# Re-exported via the `from .openrouter_providers import ...` block
# earlier in this file so all call sites (chat.py bulk import,
# _call_deepseek fallback, _call_llm_with_meta_inner routing) resolve
# unchanged.


# ─── Session D · D-2a — call_openrouter_model moved to openrouter_client.py ──
# The unified OpenRouter entry-point is now re-exported via the
# top-of-file `from .openrouter_client import ...` block. External
# callers (`services.agents._call`, `_call_claude`/`_call_glm`/
# `_call_longcat` transports below) resolve unchanged.




async def call_llm(messages: list, system: str = "",
                   max_tokens: int = 4000,
                   temperature: float = 0.7) -> str:
    """Direct OpenRouter → DeepSeek call (backwards compat). Returns content."""
    return await _call_deepseek(messages, system, max_tokens, temperature)


# ─── Session D · D-part-2 — call_llm_with_meta + inner moved to _meta.py ──
# The Langfuse-traced public entry-point (`call_llm_with_meta`) and its
# real 472 LOC body (`_call_llm_with_meta_inner`) now live in the
# sibling `_meta.py`. Re-imported here so every legacy caller resolves
# unchanged:
#   `from services.llm import call_llm_with_meta`  → still works
#   `services.llm._call_llm_with_meta_inner`       → still works
#
# MONKEYPATCH-CONTRACT NOTE (D-part-2): tests that patch
# `services.llm._call_claude` / `._call_glm` / `._call_deepseek` /
# `._call_longcat` / `._openrouter_key` still take effect. The
# extracted `_call_llm_with_meta_inner` does a LAZY
# `from services.llm import ...` at its function-body top on every
# call, so runtime patches to the package namespace are re-read on
# each invocation.
from ._meta import call_llm_with_meta, _call_llm_with_meta_inner


# Original inline definitions removed — Session D · D-part-2.
# See `_meta.py` for the canonical implementation. The old inline
# bodies are preserved in git history (pre-D-part-2 commits).


# ── Watchdog (Maxx mode review pass) ─────────────────────────────────────────

async def call_emergent_watchdog(text_to_review: str) -> dict:
    """Maxx mode: ask Claude (via OpenRouter) to grade DeepSeek's output.

    Iter 166 — name kept for backwards-compat with existing imports, but
    the implementation now uses OpenRouter exclusively.

    Returns {ok, score, issues, review, error}. passed=True iff score >= 7.
    """
    if not _openrouter_key():
        return {
            "ok": False, "score": None, "issues": [], "review": "",
            "error": "OPENROUTER_API_KEY not set",
        }
    system = (
        "Strict reviewer. Score AI reply 0-10 for correctness, "
        "hallucinations, broken code. Reply exactly:\n"
        "SCORE: <0-10>\n"
        "ISSUES: <semicolon list; 'none' if perfect>\n"
        "VERDICT: <one sentence>"
    )
    try:
        review_txt = await call_openrouter_model(
            model=_CLAUDE_MODEL,
            system=system,
            user=f"Review (score 1-10):\n\n{text_to_review[:3000]}",
            max_tokens=cap_for("review"),
            temperature=temperature_for("review"),
        )
        review_txt = (review_txt or "").strip()
        if not review_txt:
            return {
                "ok": False, "score": None, "issues": [], "review": "",
                "error": "watchdog returned empty content",
            }

        score = None
        issues_str = ""
        verdict = ""
        for line in review_txt.splitlines():
            ls = line.strip()
            if ls.upper().startswith("SCORE:"):
                try:
                    score = int("".join(ch for ch in ls.split(":", 1)[1] if ch.isdigit())[:2] or "0")
                except Exception:
                    score = None
            elif ls.upper().startswith("ISSUES:"):
                issues_str = ls.split(":", 1)[1].strip()
            elif ls.upper().startswith("VERDICT:"):
                verdict = ls.split(":", 1)[1].strip()

        issues = []
        if issues_str and issues_str.lower() not in ("none", "n/a", "-"):
            issues = [s.strip() for s in issues_str.split(";") if s.strip()]

        return {
            "ok": True, "score": score, "issues": issues,
            "verdict": verdict, "review": review_txt,
            "passed": (score is not None and score >= 7),
        }
    except Exception as e:
        logger.warning(f"watchdog (OpenRouter) failed: {e!r}")
        return {
            "ok": False, "score": None, "issues": [], "review": "",
            "error": f"watchdog unavailable: {e}",
        }


# ═══ Session 5 · Phase 2 — LONGCAT_LIVE ModuleType hook ═══════════════
# `LONGCAT_LIVE` canonical mutable bool lives in
# `services/_llm_probes.py`. External callers historically read AND
# wrote `services.llm.LONGCAT_LIVE` directly (5+ tests write it, 3
# handlers read it via function-body imports). Preserve that surface
# byte-for-byte:
#
#   • Reads (`services.llm.LONGCAT_LIVE`, `from services.llm import
#     LONGCAT_LIVE`) → route to `_llm_probes.LONGCAT_LIVE` via module
#     `__getattr__` (PEP 562, Python 3.7+).
#   • Writes (`llm_mod.LONGCAT_LIVE = False`) → route to
#     `_llm_probes.LONGCAT_LIVE = False` via a `types.ModuleType`
#     subclass installed on `sys.modules[__name__].__class__`. This
#     is the same idiom Django + attrs use to make module-attr
#     assignment programmable.
def __getattr__(name):                                          # noqa: PLE0302
    if name == "LONGCAT_LIVE":
        from . import _probes as _p
        return _p.LONGCAT_LIVE
    raise AttributeError(f"module 'services.llm' has no attribute {name!r}")


import sys as _sys
import types as _types


class _LLMModule(_types.ModuleType):
    """Custom ModuleType so `services.llm.LONGCAT_LIVE = X` writes
    land on `_llm_probes.LONGCAT_LIVE` — the canonical location —
    instead of shadowing the getter with a local attribute."""

    def __setattr__(self, name, value):
        if name == "LONGCAT_LIVE":
            from . import _probes as _p
            _p.LONGCAT_LIVE = bool(value)
            return
        super().__setattr__(name, value)


_sys.modules[__name__].__class__ = _LLMModule
