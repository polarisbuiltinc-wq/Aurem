"""services/http/client.py — 2026-02-11 · Hotspot audit Phase 1

Shared HTTP client wrapper for all outbound third-party calls.
Replaces the 218 scattered `httpx.AsyncClient(timeout=X)` calls
across 20+ files with a single policy layer.

Design goals:
  - Zero visible behavior change on the happy path — callers still
    write `r = await client.get(url); r.raise_for_status()`.
  - Retries + circuit breaking automatically via retry_guard.
  - Structured error (`ExternalCallError`) instead of raw
    httpx.HTTPStatusError leaking to callers.
  - Per-dependency timeout defaults; overridable per-call.
  - Adds `X-Request-ID` header on every request for cross-service
    trace correlation (best-effort — uses uuid4 if no ambient id).

Usage — as a context manager (preferred, closes the underlying pool):

    from services.http import ext_client
    async with ext_client("github", base_url="https://api.github.com") as c:
        r = await c.get("/repos/foo/bar")
        r.raise_for_status()

Usage — one-shot request with retries + breaker:

    from services.http import ext_request
    r = await ext_request("resend", "POST",
                          "https://api.resend.com/emails",
                          json={"to": "x@y.com", "from": "n@aurem.live"},
                          headers={"Authorization": f"Bearer {api_key}"})
    # ExternalCallError raised on non-2xx after retries exhausted.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Iterable, Optional

import httpx

from services.retry_guard import call_with_retry, BreakerOpenError

logger = logging.getLogger(__name__)

# Per-dep timeout defaults (connect, read). Overridable per-call.
# Deliberately conservative — long enough for typical API round-trips,
# short enough that a hung upstream fails fast to the breaker.
_TIMEOUT_DEFAULTS: dict[str, httpx.Timeout] = {
    "github":   httpx.Timeout(connect=5.0,  read=20.0, write=10.0, pool=5.0),
    "resend":   httpx.Timeout(connect=5.0,  read=15.0, write=10.0, pool=5.0),
    "vercel":   httpx.Timeout(connect=5.0,  read=30.0, write=15.0, pool=5.0),
    "supabase": httpx.Timeout(connect=5.0,  read=20.0, write=10.0, pool=5.0),
    "openrouter": httpx.Timeout(connect=5.0, read=60.0, write=15.0, pool=5.0),
    "stripe":   httpx.Timeout(connect=5.0,  read=20.0, write=10.0, pool=5.0),
    # Generic fallback for any dep not listed above.
    "_default": httpx.Timeout(connect=5.0,  read=20.0, write=10.0, pool=5.0),
}

# Per-dep connection-pool limits. Overridable per-call via `limits=`.
# Deliberately conservative — a small keepalive pool is enough for
# most deps (they're accessed sparsely), but write-heavy deps like
# `github` (Git Data API: parallel blob/tree/commit calls in the
# `github_api_writer` module) get a larger burst budget.
#
# 2026-02-12 · added as part of Sub-batch 1 of the `github_api_writer`
# migration prep — the writer currently uses
# `httpx.Limits(max_connections=20, max_keepalive_connections=20)`
# so `github`'s default here MUST match, else the wrapper migration
# would silently 5× the burst budget and risk tripping GitHub's
# secondary rate-limiter on large multi-file commits.
_LIMITS_DEFAULTS: dict[str, httpx.Limits] = {
    "github":   httpx.Limits(max_connections=20, max_keepalive_connections=20),
    # Everyone else uses httpx's own defaults (100/20) — represented
    # by the absence of a per-dep override. Callers that need a
    # custom cap MUST pass `limits=httpx.Limits(...)` explicitly.
}


# Status codes that should trigger a retry (transient upstream failures).
# 4xx are user errors → no retry. 5xx / 408 / 429 → retry.
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class ExternalCallError(Exception):
    """Uniform error type raised when an external call fails
    permanently (after retries + breaker exhaust). Carries enough
    structured info for logs / Sentry / user-safe UI messages
    without leaking upstream stack traces."""

    def __init__(
        self,
        dep: str,
        message: str,
        *,
        status: Optional[int] = None,
        method: Optional[str] = None,
        url: Optional[str] = None,
        body_snippet: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ):
        self.dep = dep
        self.status = status
        self.method = method
        self.url = url
        self.body_snippet = (body_snippet or "")[:400]
        self.__cause__ = cause
        super().__init__(f"[{dep}] {message}")

    def to_dict(self) -> dict:
        return {
            "dep": self.dep, "status": self.status,
            "method": self.method, "url": self.url,
            "body_snippet": self.body_snippet,
        }


@asynccontextmanager
async def ext_client(
    dep: str,
    *,
    base_url: str = "",
    headers: Optional[dict] = None,
    timeout: Optional[httpx.Timeout] = None,
    limits: Optional[httpx.Limits] = None,
    follow_redirects: bool = True,
):
    """Context-managed httpx.AsyncClient with policy defaults for `dep`.

    The returned client is a normal httpx.AsyncClient — callers use it
    as usual. Retries + breaker are NOT auto-applied here (that would
    require intercepting every method); use `ext_request()` for the
    retry-wrapped one-shot form.

    The `dep` name still matters because it seeds the timeout defaults,
    the connection-pool limits, and the injected request-id header.
    Prefer `ext_request` for hot outbound calls where retries matter;
    use `ext_client` when you need to make multiple related calls in
    the same pooled session (e.g. GitHub tree/blob/commit chain) and
    manage retries yourself via `call_with_retry`.

    Iter 2026-02-12 · added `limits=` parameter (Sub-batch 1 of the
    `github_api_writer` migration prep).  When None, uses the per-dep
    default from `_LIMITS_DEFAULTS`, falling through to httpx's own
    default (100/20) if the dep is not listed there.  Callers that
    need a specific connection-pool shape (e.g. parallel blob uploads
    with a burst cap) MUST pass an explicit `httpx.Limits(...)`.
    """
    to = timeout or _TIMEOUT_DEFAULTS.get(dep, _TIMEOUT_DEFAULTS["_default"])
    lim = limits if limits is not None else _LIMITS_DEFAULTS.get(dep)
    hdrs = dict(headers or {})
    hdrs.setdefault("X-Request-ID", uuid.uuid4().hex)
    hdrs.setdefault("User-Agent", "aurem-dev/1.0 (+https://auremcto.com)")
    # Only forward `limits` when we actually have one — passing None
    # to httpx.AsyncClient would override its internal DEFAULT_LIMITS
    # with None, which is a TypeError. Omit the kwarg instead.
    # Same pattern for `base_url` — httpx 0.27 rejects `base_url=None`
    # with a TypeError, so omit the kwarg when the caller passed "".
    client_kwargs: dict = dict(
        headers=hdrs,
        timeout=to,
        follow_redirects=follow_redirects,
    )
    if base_url:
        client_kwargs["base_url"] = base_url
    if lim is not None:
        client_kwargs["limits"] = lim
    async with httpx.AsyncClient(**client_kwargs) as c:
        yield c


async def ext_request(
    dep: str,
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    json: Any = None,
    data: Any = None,
    content: Any = None,
    timeout: Optional[httpx.Timeout] = None,
    max_retries: int = 2,
    retry_on_status: Iterable[int] = _RETRY_STATUS,
    raise_for_status: bool = True,
) -> httpx.Response:
    """One-shot HTTP request through the retry_guard breaker for `dep`.

    Behavior:
      - Retries on network errors + 5xx / 408 / 429 up to `max_retries`
      - Fast-fails with BreakerOpenError if the dep breaker is OPEN
      - Raises ExternalCallError on final non-2xx (after retries)
        when `raise_for_status=True` (default)

    Returns the raw httpx.Response so callers can `.json()` / `.text`
    / `.headers` naturally.
    """
    retry_status = frozenset(retry_on_status)
    to = timeout or _TIMEOUT_DEFAULTS.get(dep, _TIMEOUT_DEFAULTS["_default"])
    hdrs = dict(headers or {})
    hdrs.setdefault("X-Request-ID", uuid.uuid4().hex)
    hdrs.setdefault("User-Agent", "aurem-dev/1.0 (+https://auremcto.com)")

    async def _do() -> httpx.Response:
        async with httpx.AsyncClient(timeout=to, follow_redirects=True) as c:
            r = await c.request(
                method.upper(), url,
                headers=hdrs, params=params,
                json=json, data=data, content=content,
            )
        # Escalate to a retryable exception so retry_guard retries.
        if r.status_code in retry_status:
            body_snippet = ""
            try:
                body_snippet = (r.text or "")[:400]
            except Exception:
                body_snippet = ""
            raise _RetriableStatus(
                dep=dep, status=r.status_code,
                method=method, url=url,
                body_snippet=body_snippet,
            )
        return r

    try:
        r = await call_with_retry(
            dep, _do,
            max_retries=max_retries,
            retry_on=(httpx.RequestError, httpx.TimeoutException, _RetriableStatus),
        )
    except BreakerOpenError:
        # Fast-fail — surface as ExternalCallError so callers only
        # need to handle ONE error type.
        raise ExternalCallError(
            dep,
            f"circuit breaker OPEN for {dep} — upstream is currently unhealthy",
            method=method, url=url,
        )
    except _RetriableStatus as rs:
        raise ExternalCallError(
            dep,
            f"{method.upper()} {url} failed with {rs.status} after {max_retries} retries",
            status=rs.status, method=method, url=url,
            body_snippet=rs.body_snippet,
        )
    except (httpx.RequestError, httpx.TimeoutException) as e:
        raise ExternalCallError(
            dep,
            f"{method.upper()} {url} failed: {type(e).__name__}: {e}",
            method=method, url=url, cause=e,
        )

    if raise_for_status and r.status_code >= 400:
        body_snippet = ""
        try:
            body_snippet = (r.text or "")[:400]
        except Exception:
            pass
        raise ExternalCallError(
            dep,
            f"{method.upper()} {url} returned {r.status_code}",
            status=r.status_code, method=method, url=url,
            body_snippet=body_snippet,
        )
    return r


class _RetriableStatus(Exception):
    """Internal sentinel — retriable HTTP status codes surface as an
    exception so retry_guard's `retry_on` filter can catch them."""
    def __init__(self, *, dep: str, status: int, method: str, url: str,
                 body_snippet: str):
        self.dep = dep
        self.status = status
        self.method = method
        self.url = url
        self.body_snippet = body_snippet
        super().__init__(f"[{dep}] {method} {url} -> {status}")
