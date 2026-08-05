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


# ═══════════════════════════════════════════════════════════════════
# Session D · D-2d — DeepSeek (primary OpenRouter route + direct fallback)
# ═══════════════════════════════════════════════════════════════════
# Founder direction (Feb 2026): DeepSeek's PRIMARY path is also OpenRouter,
# so `_call_deepseek` belongs in this file alongside Claude/GLM/LongCat.
# `_call_deepseek_direct` is a fallback-only bypass — co-located because
# `_call_deepseek`'s ladder invokes it as an inner hop.

import asyncio
import json
import httpx

# ─── DeepSeek direct API config ──────────────────────────────────────
# Iter 212m-51 — DeepSeek direct API as second-hop fallback.
# Independent vendor (api.deepseek.com, separate billing account from
# OpenRouter) — covers the case where the user's OpenRouter credits
# are exhausted but they still want PAID quality before dropping to
# the free tier.
_DEEPSEEK_DIRECT_URL   = "https://api.deepseek.com/chat/completions"
_DEEPSEEK_DIRECT_MODEL = os.getenv("DEEPSEEK_DIRECT_MODEL", "deepseek-v4-flash")


def _deepseek_direct_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "")


async def _call_deepseek_direct(
    messages: list,
    system: str = "",
    max_tokens: int = 1500,
    temperature: float = 0.7,
) -> str:
    """Call DeepSeek's own API directly (NOT via OpenRouter).

    Used as the second hop in the fallback chain. Raises on any error
    so the caller (`_call_deepseek` / `call_openrouter_model`) can
    decide whether to walk forward to the next hop. Returns "" only
    if the API responds 200 with empty content (treat as soft fail
    so we walk forward).
    """
    key = _deepseek_direct_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not set — direct fallback unavailable")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    msgs = ([{"role": "system", "content": system}] + messages) if system else messages
    payload = {
        "model": _DEEPSEEK_DIRECT_MODEL,
        "messages": msgs,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # Playbook-prescribed timeout (30s standard; 120s only for reasoning).
    timeout_s = float(os.getenv("DEEPSEEK_DIRECT_TIMEOUT_S", "30.0"))
    async with httpx.AsyncClient(timeout=timeout_s) as c:
        r = await c.post(_DEEPSEEK_DIRECT_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    return (data["choices"][0]["message"].get("content") or "").strip()


async def _call_deepseek(messages: list, system: str = "",
                         max_tokens: int = 1500,
                         temperature: float = 0.7) -> str:
    # Session D · D-2d — lazy imports for sibling-module symbols.
    # Module-level imports here would create a circular chain because
    # the parent `services/llm/__init__.py` imports THIS module at
    # load time (see the D-2d re-export block there).
    from services.llm import _openrouter_key
    from ._state import _set_last_provider
    from ._probes import _deepseek_model
    from ._routing import _DEEPSEEK_HOSTS
    from .openrouter_client import (
        OPENROUTER_URL, _MAX_RETRIES,
        _free_fallback_models, _retryable, _retry_delay, _is_fallback_worthy,
    )
    from .groq_client import _call_groq, _groq_key, _GROQ_MODEL

    api_key = _openrouter_key()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": os.getenv("APP_URL", "https://auremcto.com"),
        "X-Title": "AUREM",
        "X-No-Cache": "true",
    }
    msgs = ([{"role": "system", "content": system}] + messages) if system else messages

    def _build_payload(model: str, with_provider_block: bool) -> dict:
        p: dict = {
            "model": model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if with_provider_block:
            # The host-routing block is OpenRouter-paid-tier specific; the
            # `:free` models live on different infra and 400-out on unknown
            # provider hosts. Only attach it for the primary call.
            p["provider"] = {
                "data_collection": "deny",
                "order": _DEEPSEEK_HOSTS,
                "allow_fallbacks": False,
            }
        return p

    _LLM_TIMEOUT_S = float(os.getenv("LLM_HTTP_TIMEOUT_S", "25.0"))

    # Iter 212m-47 — Try primary DeepSeek model first; if that fails on
    # 402/429/5xx/network, walk the free-model chain. Returns content
    # from whichever model succeeded.
    candidates: list[tuple[str, bool]] = [(_deepseek_model(), True)]
    for fm in _free_fallback_models():
        candidates.append((fm, False))

    # Iter 360 · Guard 17 — central breaker for the OpenRouter chain.
    # When OPEN we skip the whole candidates walk (no hammering) and
    # drop straight into the vendor-independent fallbacks below.
    from services.retry_guard import get_breaker as _rg_breaker
    _or_br = _rg_breaker("openrouter")
    _or_attempted = _or_br.allow()
    if not _or_attempted:
        logger.warning(
            "[G17] openrouter breaker OPEN (retry in ~%.0fs) — skipping "
            "OpenRouter chain, going straight to fallbacks",
            _or_br.retry_after_s(),
        )
        candidates = []

    last_exc: Exception | None = None
    data: dict | None = None
    served_by: str | None = None
    for ci, (cand_model, with_provider) in enumerate(candidates):
        payload = _build_payload(cand_model, with_provider)
        try:
            async with httpx.AsyncClient(timeout=_LLM_TIMEOUT_S) as c:
                # Per-model: 1 retry on transient errors (unchanged
                # legacy behaviour for the primary).
                for attempt in range(1, _MAX_RETRIES + 2):
                    try:
                        r = await c.post(OPENROUTER_URL, headers=headers, json=payload)
                        r.raise_for_status()
                        data = r.json()
                        served_by = cand_model
                        # Iter 309 · Pre-Phase-1 — loop-token accounting.
                        # If this call is happening inside a loop (see
                        # loop_engine._with_budget's contextvars scope),
                        # tag the token usage against loop_id + phase.
                        # No-op for regular chat/scaffold callers.
                        try:
                            from services.loop_token_ledger import log_llm_usage
                            await log_llm_usage(
                                cand_model,
                                (data or {}).get("usage") or {},
                                temperature=temperature,
                            )
                        except Exception as _e:
                            # Ledger failure is non-fatal for chat/scaffold
                            # (fail-open), but should surface at debug so
                            # ops can diagnose a broken loop-token track.
                            logger.debug(
                                "[silent-catch] llm.py:738 in _call_deepseek "
                                "— loop_token_ledger.log_llm_usage failed: %r",
                                _e,
                            )
                        break
                    except Exception as e:
                        retryable, status = _retryable(e)
                        if not retryable or attempt > _MAX_RETRIES:
                            raise
                        delay = _retry_delay(attempt)
                        logger.warning(
                            "OpenRouter transient failure on %s (status=%s, attempt %d/%d) — "
                            "retrying in %.2fs: %r",
                            cand_model, status, attempt, _MAX_RETRIES + 1, delay, e,
                        )
                        await asyncio.sleep(delay)
            # Success — stop walking the fallback chain.
            if ci > 0:
                logger.warning(
                    "DeepSeek primary %r failed, served by free fallback %r",
                    _deepseek_model(), cand_model,
                )
            break
        except Exception as e:
            last_exc = e
            if not _is_fallback_worthy(e):
                logger.error(
                    "OpenRouter call (%s) failed non-retryably: %r", cand_model, e,
                )
                raise
            logger.warning(
                "OpenRouter %s failed (fallback-worthy, %d/%d): %r — walking chain",
                cand_model, ci + 1, len(candidates), e,
            )
            # Iter 212m-51 — after the PRIMARY OpenRouter model fails
            # (and before we walk the OpenRouter free chain), try the
            # DeepSeek direct API. Independent vendor / billing →
            # bypasses OpenRouter credit exhaustion entirely while
            # still delivering paid-tier quality. Only attempted once
            # per call; if it ALSO fails we silently continue down
            # the free chain.
            if ci == 0 and _deepseek_direct_key():
                try:
                    logger.warning(
                        "OpenRouter primary failed, trying DeepSeek direct (model=%s)…",
                        _DEEPSEEK_DIRECT_MODEL,
                    )
                    ds_content = await _call_deepseek_direct(
                        messages=messages,
                        system=system,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    if ds_content:
                        logger.warning(
                            "DeepSeek call served by DeepSeek-direct model=%s",
                            _DEEPSEEK_DIRECT_MODEL,
                        )
                        _set_last_provider("deepseek_direct", _DEEPSEEK_DIRECT_MODEL)
                        return ds_content
                    logger.warning(
                        "DeepSeek direct returned empty content — walking free chain"
                    )
                except Exception as dse:
                    if isinstance(dse, httpx.HTTPStatusError) and \
                       dse.response.status_code in (400, 422):
                        # GENUINE prompt-level error from DeepSeek
                        # (request shape / parameters bad). Burning
                        # the free chain on the same broken prompt
                        # is pointless — abort.
                        logger.warning(
                            "DeepSeek direct rejected request (%d) — aborting chain",
                            dse.response.status_code,
                        )
                        raise
                    # 401 = bad key (config drift), 402 = balance,
                    # 429 = throttle, 5xx = vendor issue. None of
                    # these are the user's fault — keep walking the
                    # OR free chain so they still get a response.
                    logger.warning(
                        "DeepSeek direct failed (%r) — walking OpenRouter free chain",
                        dse,
                    )
            continue

    if data is None:
        # Iter 360 · Guard 17 — whole OpenRouter chain exhausted.
        if _or_attempted:
            _or_br.record_failure(repr(last_exc))
        # All OpenRouter candidates exhausted — try the Groq emergency
        # net as the absolute final link. This is the vendor-
        # independent safety hop (different infra, different account).
        if _groq_key():
            try:
                logger.warning(
                    "OpenRouter chain exhausted (%d candidates). "
                    "Trying Groq emergency fallback (model=%s)…",
                    len(candidates), _GROQ_MODEL,
                )
                content = await _call_groq(
                    messages=msgs,
                    system="",  # already prepended above
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if content:
                    logger.warning(
                        "DeepSeek call served by Groq fallback model=%s",
                        _GROQ_MODEL,
                    )
                    # Stash provenance on a module-global so the SSE
                    # pipeline can surface a "⚡ free mode" pill to
                    # the frontend on this turn.
                    _set_last_provider("groq", _GROQ_MODEL)
                    return content
            except Exception as ge:
                logger.error(
                    "Groq emergency fallback ALSO failed: %r — chain is "
                    "now fully exhausted",
                    ge,
                )
        logger.error(
            "OpenRouter exhausted all %d candidates. Last error: %r",
            len(candidates), last_exc,
        )
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("OpenRouter call failed: no candidate models returned")
    # Stash the served-by model in a logger context line; the data dict
    # itself is returned through legacy code paths so we don't change
    # the call contract — provenance is in the logs.
    if _or_attempted:
        _or_br.record_success()
    if served_by:
        logger.info("DeepSeek call served by model=%s", served_by)
        _set_last_provider("openrouter", served_by)
    try:
        msg = data["choices"][0]["message"]
        # If DeepSeek returned native tool_calls, serialize them back to
        # markdown fence so extract_tool_calls() in tools_bridge.py can
        # parse them with its existing Shape-1/2/3 logic.
        tool_calls = msg.get("tool_calls") or []
        if tool_calls and not msg.get("content"):
            parts = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                parts.append(
                    "```tool_call\n"
                    + json.dumps({"tool": name, "args": args})
                    + "\n```"
                )
            return "\n".join(parts)
        return msg.get("content") or ""
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"OpenRouter malformed response: {e}: {data!r}")
