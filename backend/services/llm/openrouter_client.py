"""services/llm/openrouter_client.py — OpenRouter transport layer.

Session D · D-2a (LLM Split Phase 4, 2026-02) — extracted from
`services/llm/__init__.py`. Owns:

  • The `OPENROUTER_URL` endpoint constant.
  • Retry + fallback policy constants (`_RETRY_STATUS`, `_MAX_RETRIES`,
    `_BASE_DELAY_S`, `_FALLBACK_STATUSES`, `_DEFAULT_FREE_MODELS`).
  • Pure classifier / helper functions (`_free_fallback_models`,
    `_is_fallback_worthy`, `_retryable`, `_retry_delay`).
  • The unified `call_openrouter_model` entry-point used by
    `services.agents._call` and internal `_call_claude`/`_call_glm`/
    `_call_longcat` transports.

The provider-specific helpers (`_call_deepseek_direct`, `_call_groq`)
remain in `services/llm/__init__.py` for D-2a — they'll move in
D-2b / D-2d respectively. To avoid a circular import,
`call_openrouter_model` uses LAZY imports for those two — the imports
resolve at call time when both modules are fully loaded.
"""
from __future__ import annotations

import logging
import os
import random

import httpx

logger = logging.getLogger(__name__)


# ─── Endpoint + retry policy ─────────────────────────────────────────────
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Iter 124 — retry policy for transient upstream failures (rate limit / 5xx).
# Exponential backoff with full jitter so concurrent callers don't sync up.
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
_MAX_RETRIES = 1      # Iter 129 — was 3. 4 attempts × 5.6s of backoff
                      # was killing chat latency under load (every 429
                      # cascade burned ~10 s of wall time for no
                      # behavioural win — LLM rate limits are STICKY,
                      # repeated retries hit the same wall). 1 retry
                      # catches the flapping-network case and surfaces
                      # genuine outages to the user fast.
_BASE_DELAY_S = 0.4   # Iter 129 — was 0.8. Halves the worst-case
                      # delay window. With _MAX_RETRIES=1 this is the
                      # ONLY backoff that fires.


# ─── Iter 212m-47 — Free-model fallback chain ─────────────────────────────
# When OpenRouter rejects a paid-model call with one of these statuses
# (most commonly 402 = insufficient credits, also 429 / 5xx / network),
# we retry the SAME prompt against OpenRouter's `:free` models. These
# don't consume credits and live on independent infra so a credit-
# exhaustion or paid-model outage still gets the user a response.
#
# Order matters: best quality → smallest. Env override:
#   OPENROUTER_FREE_MODELS=model1,model2,model3
_FALLBACK_STATUSES = {402, 404, 408, 425, 429, 500, 502, 503, 504}
# 404 → primary model slug isn't routable on OpenRouter (rare config
# drift). Treat as "this candidate is broken — try the next" rather
# than a hard fail; the legitimate "your prompt is broken" 4xx codes
# (400, 422) still abort the chain.
_DEFAULT_FREE_MODELS = [
    # Verified-live slugs via GET /api/v1/models (Feb 2026). Order:
    # best quality → smallest. The chain stops at the first model that
    # produces non-empty content.
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-coder:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
]


# ─── Pure helpers ────────────────────────────────────────────────────────

def _free_fallback_models() -> list[str]:
    raw = os.getenv("OPENROUTER_FREE_MODELS", "").strip()
    if not raw:
        return list(_DEFAULT_FREE_MODELS)
    return [m.strip() for m in raw.split(",") if m.strip()]


def _is_fallback_worthy(exc: Exception) -> bool:
    """Return True when the exception means the PRIMARY model is the
    problem (credit issue, rate limit, upstream 5xx, network blip) and
    we should retry the same prompt against a free model. False when
    the prompt itself is broken (4xx other than 402/429) — retrying
    would just burn another model's quota for no win."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _FALLBACK_STATUSES
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                        httpx.ReadError, httpx.RemoteProtocolError)):
        return True
    return False


def _retryable(exc: Exception) -> tuple[bool, int | None]:
    """Return (should_retry, http_status). Status is None for non-HTTP errors."""
    if isinstance(exc, httpx.HTTPStatusError):
        return (exc.response.status_code in _RETRY_STATUS,
                exc.response.status_code)
    # Network / timeout — transient by default
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                        httpx.ReadError, httpx.RemoteProtocolError)):
        return (True, None)
    return (False, None)


def _retry_delay(attempt: int) -> float:
    """Full-jitter exponential backoff. attempt is 1-indexed."""
    cap = _BASE_DELAY_S * (2 ** (attempt - 1))
    return random.uniform(0, cap)


# ─── Unified entry-point ─────────────────────────────────────────────────

async def call_openrouter_model(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1000,
    temperature: float = 0.2,
) -> str:
    """Iter 165 — generic OpenRouter caller for the agents pipeline.

    Used by services.agents._call for ANY OpenRouter model (Kimi K2,
    Kimi K2.7 Code, Kimi K2.5, Kimi Thinking, Claude via OpenRouter,
    DeepSeek). The legacy `_call_claude` path goes through the
    Emergent LLM key instead — this function is the unified
    OpenRouter-only path so smart_router can pick any provider.

    Iter 212m-47 — Free-model fallback chain. If the requested paid
    model returns 402 (insufficient credits) / 429 / 5xx / network
    error, we transparently retry the same prompt against OpenRouter's
    `:free` tier models. The caller never has to know — they still get
    a usable completion string, with a log line marking which model
    actually answered.

    Returns "" on any failure; the agents layer handles fallback so we
    keep this function thin and predictable.
    """
    # Session D · D-2a — lazy imports for the provider-specific
    # helpers still living in `services/llm/__init__.py`. Module-level
    # imports here would create a circular dependency (the parent
    # `__init__.py` imports THIS module at load time). Late-binding
    # via function-scope imports is Python's canonical fix.
    from services.llm import (
        _openrouter_key, _deepseek_direct_key, _groq_key,
        _DEEPSEEK_DIRECT_MODEL, _GROQ_MODEL,
        _call_deepseek_direct, _call_groq,
    )
    from ._state import _set_last_provider

    api_key = _openrouter_key()
    if not api_key:
        logger.warning("OPENROUTER_API_KEY missing — call_openrouter_model returning empty")
        return ""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": os.getenv("APP_URL", "https://auremcto.com"),
        "X-Title": "AUREM",
        "Content-Type": "application/json",
    }
    base_messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]
    _timeout_s = float(os.getenv("LLM_HTTP_TIMEOUT_S", "25.0"))

    # Try the requested model first, then each free fallback in turn.
    candidates = [model] + [m for m in _free_fallback_models() if m != model]
    last_exc: Exception | None = None
    for i, candidate in enumerate(candidates):
        payload = {
            "model": candidate,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": base_messages,
        }
        try:
            async with httpx.AsyncClient(timeout=_timeout_s) as c:
                r = await c.post(OPENROUTER_URL, headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
            content = (data["choices"][0]["message"].get("content") or "").strip()
            # Iter 309 · Pre-Phase-1 — loop-token accounting.
            # No-op unless a loop context is active (see
            # loop_token_ledger.loop_call_context).
            try:
                from services.loop_token_ledger import log_llm_usage
                await log_llm_usage(
                    candidate,
                    (data or {}).get("usage") or {},
                    temperature=temperature,
                )
            except Exception as _e:
                # Same rationale as _call_deepseek:738 — non-fatal
                # fail-open, but log at debug for grep-ability.
                logger.debug(
                    "[silent-catch] llm.py:1104 in call_openrouter_model "
                    "— loop_token_ledger.log_llm_usage failed: %r", _e,
                )
            if i > 0:
                logger.warning(
                    "call_openrouter_model: primary %r failed, served by free fallback %r",
                    model, candidate,
                )
                _set_last_provider("openrouter", candidate)
            else:
                _set_last_provider("openrouter", candidate)
            return content
        except Exception as e:
            last_exc = e
            if not _is_fallback_worthy(e):
                # Prompt-level bug (4xx other than 402/429). Don't burn
                # extra free quota — surface the failure.
                logger.warning("call_openrouter_model(%s) non-retryable: %r", candidate, e)
                return ""
            logger.warning(
                "call_openrouter_model(%s) fallback-worthy failure (%d/%d): %r",
                candidate, i + 1, len(candidates), e,
            )
            # Iter 212m-51 — after the PRIMARY OpenRouter model fails
            # (and before walking the OpenRouter :free chain), try
            # DeepSeek direct. Same priority order as `_call_deepseek`
            # so all paths (chat / agents / Vanguard / Mode D) share
            # the same vendor-independent failover.
            if i == 0 and _deepseek_direct_key():
                try:
                    logger.warning(
                        "call_openrouter_model: primary %r failed, "
                        "trying DeepSeek direct (model=%s)…",
                        model, _DEEPSEEK_DIRECT_MODEL,
                    )
                    ds_content = await _call_deepseek_direct(
                        messages=[{"role": "user", "content": user}],
                        system=system,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    if ds_content:
                        logger.warning(
                            "call_openrouter_model: served by DeepSeek-direct model=%s",
                            _DEEPSEEK_DIRECT_MODEL,
                        )
                        _set_last_provider("deepseek_direct", _DEEPSEEK_DIRECT_MODEL)
                        return ds_content
                except Exception as dse:
                    if isinstance(dse, httpx.HTTPStatusError) and \
                       dse.response.status_code in (400, 422):
                        logger.warning(
                            "DeepSeek direct rejected agent prompt (%d) — aborting chain",
                            dse.response.status_code,
                        )
                        return ""
                    # 401/402/429/5xx → bad key / balance / vendor
                    # issue. Walk forward instead of failing the
                    # whole agent call.
                    logger.warning(
                        "DeepSeek direct failed inside call_openrouter_model (%r) — "
                        "walking OR free chain", dse,
                    )
    # Iter 212m-49 — All OpenRouter candidates exhausted. Try the Groq
    # emergency net before giving up. Vendor-independent infra so an
    # OpenRouter-wide outage / global-free-tier 429 storm still gets
    # the user a usable answer.
    if _groq_key():
        try:
            logger.warning(
                "call_openrouter_model: chain exhausted, trying Groq emergency "
                "(model=%s) for primary=%r",
                _GROQ_MODEL, model,
            )
            content = await _call_groq(
                messages=[{"role": "user", "content": user}],
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if content:
                logger.warning(
                    "call_openrouter_model: served by Groq fallback model=%s",
                    _GROQ_MODEL,
                )
                _set_last_provider("groq", _GROQ_MODEL)
                return content
        except Exception as ge:
            logger.error(
                "Groq emergency fallback ALSO failed inside call_openrouter_model: %r",
                ge,
            )
    logger.error(
        "call_openrouter_model: all %d candidates exhausted (primary=%r). Last error: %r",
        len(candidates), model, last_exc,
    )
    return ""
