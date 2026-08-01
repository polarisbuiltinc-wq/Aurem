"""services/llm/openrouter_providers.py — provider-specific wrappers.

Session D · D-2c (LLM Split Phase 4, 2026-02) — extracted from
`services/llm/__init__.py`. Concern boundary vs. `openrouter_client.py`:

  • `openrouter_client.py`  — TRANSPORT MECHANICS
      (retry/backoff policy, fallback ladder, HTTP call). Provider-
      agnostic — could serve any OpenAI-compatible endpoint.
  • `openrouter_providers.py` (this file) — PROVIDER-SPECIFIC CONFIGS
      (model slugs, per-model default max_tokens/temperature, empty-
      response fallback strategy). Depends on the transport layer.

The 3 helpers here (`_call_claude`, `_call_glm`, `_call_longcat`) are
all thin adapters over `call_openrouter_model` — they set a specific
`model=` slug, apply provider-defensive fallbacks (e.g. empty Claude
response → DeepSeek retry, empty LongCat mid-session → GLM), and
own the provider-specific tuning knobs.

`_openrouter_key()` STAYS in `services/llm/__init__.py` because
`_call_deepseek` (still living there) also uses it — moving it here
would create bi-directional coupling for a one-line env-var read.

Monkeypatch contract (Session D · D-2c):
    Tests that patch `_call_claude` / `_call_glm` / `_call_longcat`
    to force specific fallback branches should target THIS module
    (`services.llm.openrouter_providers`) — patching the re-export
    on `services.llm` still works for lookups routed through the
    module's `__globals__` (Python name resolution walks to the
    parent namespace), but the canonical target is more robust
    against future refactors that inline references.
"""
from __future__ import annotations

import logging
import os


logger = logging.getLogger(__name__)


# ─── Claude — Anthropic sonnet, code tasks ────────────────────────────
# Iter 212g — OpenRouter accepts dotted version IDs
# (`anthropic/claude-sonnet-4.5`) NOT the dash-date Anthropic-native
# format (`claude-sonnet-4-5-20250929`) which we were sending until
# prod logs showed 400 Bad Request from OpenRouter on every Claude
# call. Verified against `GET https://openrouter.ai/api/v1/models`.
_CLAUDE_MODEL = os.getenv(
    "CLAUDE_MODEL", "anthropic/claude-sonnet-4.5"
)


# ─── GLM — Zhipu AI flagship, Swift/Pro/Maxx primary ────────────────
# Iter 212m-18 — GLM-5.2 (Zhipu AI's flagship via OpenRouter) is the
# new primary model for Swift/Pro/Maxx review modes:
#   Swift → GLM only (fastest path)
#   Pro   → GLM, fall back to Claude on empty / error (resilience)
#   Maxx  → GLM first, then Claude reviews+improves the GLM output
# Override per-deploy via env so we can pin a specific revision.
_GLM_MODEL = os.getenv("GLM_MODEL", "z-ai/glm-5.2")


# ─── LongCat — Meituan, Council A primary (probe-gated) ─────────────
# Iter 212m-159 — Council A now defaults to LongCat-2.0 when live.
# Env override lets us swap for eval without a code change.
_LONGCAT_MODEL = os.getenv("LONGCAT_MODEL", "anthropic/claude-sonnet-4.5")


# ── Claude path (code tasks) ─────────────────────────────────────────────────

async def _call_claude(system: str, user: str,
                       max_tokens: int = 3500,
                       temperature: float = 0.0) -> str:
    """Call Claude Sonnet 4.5 via OpenRouter for code tasks.

    Iter 166 — Migrated from Emergent SDK to OpenRouter. Single key
    (OPENROUTER_API_KEY) now serves DeepSeek + Claude + all agents.
    Falls back to DeepSeek if Claude call returns empty (network /
    upstream failure) so code tasks never hard-fail.
    """
    # Session D · D-2c — lazy imports for helpers that stay in
    # `services/llm/__init__.py` (`_openrouter_key`, `_call_deepseek`).
    # Module-level imports would create a circular chain because
    # `__init__.py` imports THIS module at load time.
    from services.llm import _openrouter_key, _call_deepseek
    from .openrouter_client import call_openrouter_model

    if not _openrouter_key():
        logger.info("OPENROUTER_API_KEY not set — falling back to DeepSeek for code task")
        return await _call_deepseek(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    content = await call_openrouter_model(
        model=_CLAUDE_MODEL,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if content:
        return content

    # Empty content → fall back to DeepSeek so we never silently 500 a code task.
    logger.warning("Claude (OpenRouter) returned empty — falling back to DeepSeek")
    return await _call_deepseek(
        messages=[{"role": "user", "content": user}],
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
    )


# ── GLM path (Swift/Pro/Maxx primary — z-ai/glm-5.2) ───────────────────────

async def _call_glm(system: str, user: str,
                    max_tokens: int = 3500,
                    temperature: float = 0.0) -> str:
    """Iter 212m-18 — Call GLM-5.2 (`z-ai/glm-5.2`) via OpenRouter.

    Mirrors `_call_claude`'s shape so the orchestrator and review-mode
    router can swap models without code branching downstream. Returns
    the assistant content string (may be empty on upstream failure —
    callers in pro/maxx mode use that as the trigger to fall back to
    Claude).
    """
    from services.llm import _openrouter_key, _call_deepseek
    from .openrouter_client import call_openrouter_model

    if not _openrouter_key():
        logger.info(
            "OPENROUTER_API_KEY not set — GLM call falling back to DeepSeek"
        )
        return await _call_deepseek(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    content = await call_openrouter_model(
        model=_GLM_MODEL,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return content or ""


# ── LongCat-2.0 path (Iter 212m-159 — Council A primary) ─────

async def _call_longcat(system: str, user: str,
                        max_tokens: int = 3500,
                        temperature: float = 0.0) -> str:
    """Call LongCat-2.0 (`meituan/longcat-2.0`) via OpenRouter.

    Only invoked when `LONGCAT_ENABLED=true` and the caller routes via
    Council A (`mode="code"` + Swift/Pro/Maxx).  Mirrors `_call_glm`'s
    shape so the routing block can swap models with a single conditional.

    Iter 212m-160 — fast-path: if the boot-time probe already marked
    LongCat unavailable (`LONGCAT_LIVE=False`), skip the wasted
    OpenRouter round-trip and go straight to GLM-5.2. Saves ~200 ms
    + an OR rate-limit slot per Council A call until LongCat is live.

    Session 5 · Phase 2 — `LONGCAT_LIVE` now lives in
    `services/_llm_probes.py`. Read directly from there so we always
    see the latest value even if a background probe just flipped it,
    and write via `set_longcat_live()` so the mutation is
    single-sourced.
    """
    from services.llm import _openrouter_key, _call_deepseek
    from .openrouter_client import call_openrouter_model
    from . import _probes as _p

    if not _p.LONGCAT_LIVE:
        # Boot probe already detected LongCat is dead — straight to GLM.
        return await _call_glm(
            system=system, user=user,
            max_tokens=max_tokens, temperature=temperature,
        )
    if not _openrouter_key():
        logger.info(
            "OPENROUTER_API_KEY not set — LongCat call falling back to DeepSeek"
        )
        return await _call_deepseek(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    content = await call_openrouter_model(
        model=_LONGCAT_MODEL,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if not (content or "").strip():
        # LongCat suddenly unreachable mid-flight (probe said it was
        # live, but this call returned empty). Update the live flag
        # so subsequent calls take the fast-path, then fall back to GLM.
        # Session D · D-2c — pre-existing latent-bug fix: original
        # code referenced `_probes.LONGCAT_LIVE` (unbound name in the
        # old `__init__.py` scope; would have raised `NameError` if
        # this path were exercised). Now uses the local `_p` alias
        # consistently.
        if _p.LONGCAT_LIVE:
            _p.set_longcat_live(False)
            logger.warning(
                "_call_longcat: %s returned empty mid-session — "
                "flipping LONGCAT_LIVE=False, Council A on GLM-5.2 "
                "until next restart.",
                _LONGCAT_MODEL,
            )
        try:
            return await _call_glm(
                system=system, user=user,
                max_tokens=max_tokens, temperature=temperature,
            )
        except Exception as e:
            logger.error("_call_longcat: GLM-5.2 fallback also failed: %r", e)
            return ""
    return content
