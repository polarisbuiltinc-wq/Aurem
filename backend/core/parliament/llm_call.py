"""
core/parliament/llm_call.py — 2026-09-08 Phase 3 god-class split.

`_llm_call_protected` — the single protected LLM call wrapped by the
global concurrency semaphore + the circuit breaker. Moved verbatim
out of the single core/parliament.py (zero logic change).

Referenced via `from . import llm_call as _llm_call_mod` (dotted
module access, not `from .llm_call import _llm_call_protected`) by
councils.py / ceo.py / self_heal.py / parliament.py, so existing test
mocks that patch this module's `_llm_call_protected` attribute (e.g.
`monkeypatch.setattr(llm_call_mod, "_llm_call_protected", fake)`)
correctly reach every caller regardless of which file it lives in.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from ..observability import trace_llm  # Iter 212m-153 — safe Langfuse wrapper
from .breaker import ParliamentCircuitBreaker, _GLOBAL_BREAKER

MAX_CONCURRENT_LLM_CALLS = 6
_GLOBAL_LLM_SEM = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)


async def _llm_call_protected(*, system: str, user: str, max_tokens: int,
                              mode: str, review_mode: str,
                              user_id: Optional[str] = None,
                              temperature: float = 0.1,
                              trace_name: str = "parliament.llm_call",
                              trace_metadata: Optional[dict] = None,
                              ) -> tuple[str, float, Optional[str]]:
    """Make a single LLM call wrapped by the global semaphore + the
    circuit breaker's hard per-call timeout.

    Returns (content, latency_ms, error_str).  `error_str` is None on
    success, a short tag on failure (`"timeout"`, `"refused"`, or the
    exception class name).

    Iter 212m-153 — Every call is wrapped by a Langfuse generation
    observation.  Silent no-op when Langfuse keys are not configured.
    """
    from services.llm import call_llm_with_meta
    t0 = time.monotonic()
    md = {
        "mode":        mode,
        "review_mode": review_mode,
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    if trace_metadata:
        md.update(trace_metadata)
    # Truncate the input for safety — Langfuse handles long strings
    # but we don't need to ship 6 KB of self-heal context every time.
    trace_input = {
        "system_preview": (system or "")[:240],
        "user_preview":   (user or "")[:1200],
    }
    with trace_llm(trace_name, input=trace_input, metadata=md) as span:
        async with _GLOBAL_LLM_SEM:
            try:
                kwargs = dict(
                    system=system, user=user, max_tokens=max_tokens,
                    mode=mode, user_id=user_id, review_mode=review_mode,
                )
                try:
                    meta = await asyncio.wait_for(
                        call_llm_with_meta(temperature=temperature, **kwargs),
                        timeout=ParliamentCircuitBreaker.TIMEOUT_PER_CALL,
                    )
                except TypeError:
                    # Older signature without temperature kwarg.
                    meta = await asyncio.wait_for(
                        call_llm_with_meta(**kwargs),
                        timeout=ParliamentCircuitBreaker.TIMEOUT_PER_CALL,
                    )
            except asyncio.TimeoutError:
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                _GLOBAL_BREAKER.record_failure(latency_ms, kind="timeout")
                span.set_metadata({"latency_ms": latency_ms, "error": "timeout"})
                span.record_error("timeout")
                return "", latency_ms, "timeout"
            except Exception as e:                          # noqa: BLE001
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                _GLOBAL_BREAKER.record_failure(latency_ms, kind=type(e).__name__)
                err_tag = f"{type(e).__name__}: {str(e)[:120]}"
                span.set_metadata({"latency_ms": latency_ms, "error": err_tag})
                span.record_error(e)
                return "", latency_ms, err_tag
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        # 2026-08 hardening (F2) — a cost-cap breach comes back as a
        # normal {"ok": False, "error_code": "COST_CAP_REACHED"} dict
        # from _meta.py, not an exception. Tag it distinctly so callers
        # (CEO.decide, Parliament._fallback_single_call) can tell
        # "budget exhausted" apart from a generic empty/error response —
        # this is NOT a circuit-breaker-worthy failure (it's not the
        # provider being unhealthy), so skip recording it there.
        if isinstance(meta, dict) and meta.get("error_code") == "COST_CAP_REACHED":
            span.set_metadata({"latency_ms": latency_ms, "error": "cost_cap_reached"})
            span.record_error("cost_cap_reached")
            return "", latency_ms, "cost_cap_reached"
        content = (meta or {}).get("content", "") or ""
        if not content.strip():
            _GLOBAL_BREAKER.record_failure(latency_ms, kind="empty")
            span.set_metadata({"latency_ms": latency_ms, "error": "empty"})
            span.record_error("empty_response")
            return "", latency_ms, "empty"
        _GLOBAL_BREAKER.record_success(latency_ms)
        # Capture output + token usage (when llm meta provides it).
        span.set_output(content[:2000])
        usage_md = {"latency_ms": latency_ms}
        try:
            for k in ("input_tokens", "output_tokens", "total_tokens",
                      "provider", "model"):
                if isinstance(meta, dict) and meta.get(k) is not None:
                    usage_md[k] = meta[k]
        except Exception:
            pass
        span.set_metadata(usage_md)
        return content, latency_ms, None
