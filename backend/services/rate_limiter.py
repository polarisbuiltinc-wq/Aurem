"""
services/rate_limiter.py — async rate limiter (Redis-shared + in-memory fallback).

Why hand-rolled? `slowapi`'s decorator collides with FastAPI's automatic
dependency injection when the function signature mixes Request, Pydantic
bodies, and BackgroundTasks (the decorator re-orders the params and
FastAPI then misinterprets them as query strings).

This module gives us:
  - `check_rate_limit(key, limit_per_minute)` — SYNC, in-memory only.
    Retained for tests + local dev + single-pod deployments.
  - `check_rate_limit_async(key, limit_per_minute)` — ASYNC, Redis-
    backed when `REDIS_URL` is set (shares state across ALL pods in
    the K8s deployment), falls back to the in-memory bucket when
    Redis is unavailable so tests + preview keep working.

The async variant is the ONE the middleware + all endpoint hot-paths
must use. Iter 386 · Session 2 audit surfaced the multi-pod gap: an
in-memory bucket is PER-PROCESS, so behind an N-replica K8s Service
each pod has its own counter and no IP ever accumulates enough for
any single pod to trip its ceiling. Redis fixes that by giving every
pod a shared view of the same sliding window.

Suspended when env RATE_LIMIT_DISABLED=1 (tests).
"""
from __future__ import annotations
import logging
import os
import time
import uuid
from collections import deque, defaultdict
from typing import Deque

logger = logging.getLogger(__name__)

_ENABLED = os.getenv("RATE_LIMIT_DISABLED", "0") != "1"

# key → sliding-window timestamps (most recent first). 60-second buckets.
_buckets: dict[str, Deque[float]] = defaultdict(deque)
_WINDOW_SEC = 60.0

# BUG 7 fix — cap the in-memory bucket map so attackers can't OOM the
# process by rotating Bearer-token Authorization headers (or IP-spoofed
# X-Forwarded-For values) on every request. 10K active keys per minute
# is comfortably above any honest traffic pattern.
_MAX_BUCKETS = int(os.getenv("RATE_LIMIT_MAX_BUCKETS", "10000"))


def check_rate_limit(key: str, limit_per_minute: int) -> bool:
    """Returns True if this request is allowed under the per-minute limit."""
    if not _ENABLED:
        return True
    now = time.time()
    # Eject the oldest entry when we're about to add a new one over the cap.
    # Using `next(iter(...))` on a dict gives us the insertion-oldest key
    # in CPython, which is the closest we get to an LRU without an extra
    # data structure. The window check below cleans expired entries anyway.
    if key not in _buckets and len(_buckets) >= _MAX_BUCKETS:
        try:
            oldest = next(iter(_buckets))
            del _buckets[oldest]
        except StopIteration:
            pass
    bucket = _buckets[key]
    # Drop timestamps older than the window
    while bucket and (now - bucket[0]) > _WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= limit_per_minute:
        return False
    bucket.append(now)
    return True


def client_ip_from_request(request) -> str:
    """Extract the real client IP, honoring X-Forwarded-For when set by
    the K8s ingress."""
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if fwd:
        return fwd
    return getattr(request.client, "host", "unknown") or "unknown"


# ══════════════════════════════════════════════════════════════════════
# Iter 386 · Session 2 · Part 0 — Redis-shared sliding-window limiter
# ══════════════════════════════════════════════════════════════════════
#
# Design: sliding-window log using a Redis sorted set.
#
#   ZREMRANGEBYSCORE  drop timestamps older than window
#   ZCARD             count entries currently in the window
#   if count >= limit → reject
#   ZADD              insert this request's timestamp
#   EXPIRE            let the whole key TTL out
#
# Executed atomically via a Lua script (one RTT).  Falls back to the
# in-memory `check_rate_limit` above whenever Redis is unavailable so
# tests, preview, and any deployment without REDIS_URL keep working
# with the previous semantics (per-pod bucketing).
_REDIS_PREFIX = "aurem:rl:"

# One RTT atomic sliding window.  Returns 1 when allowed, 0 when denied.
_RL_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local mid = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local n = redis.call('ZCARD', key)
if n >= limit then
  return 0
end
redis.call('ZADD', key, now, mid)
redis.call('EXPIRE', key, math.floor(window / 1000) + 1)
return 1
"""

# Lazy Redis connection state — mirrors scan_cache.py / admin_analytics_cache.py
# conventions so operators see one consistent lifecycle across the codebase.
_REDIS_CLIENT = None
_REDIS_TRIED = False
_REDIS_LUA_SHA: str | None = None
_REDIS_BACKEND_ACTIVE = False   # exposed so tests + observability endpoints
#                                 can assert we're actually on Redis path
_REDIS_LAST_ERROR: str | None = None  # last connection-attempt error, for
#                                       the /health/rate-limiter probe. Never
#                                       includes credentials (see _redact).
_REDIS_LAST_ATTEMPT_TS: float | None = None
_REDIS_HOST_REDACTED: str | None = None  # "host:port" only, no user:pass


def _redact_redis_url(url: str) -> str:
    """Return `host:port` from a redis[s]:// URL without ever exposing
    user:password. Used by the health probe so on-call can SEE which
    endpoint the pod is trying to reach without risking a credential
    leak into logs / Sentry / API responses."""
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        host = p.hostname or "?"
        port = p.port or 6379
        return f"{host}:{port}"
    except Exception:
        return "<unparseable>"


async def _ensure_redis():
    """Lazy connect. Returns the aioredis client or None. Never raises —
    Redis outage falls back to in-memory silently."""
    global _REDIS_CLIENT, _REDIS_TRIED, _REDIS_LUA_SHA, _REDIS_BACKEND_ACTIVE
    global _REDIS_LAST_ERROR, _REDIS_LAST_ATTEMPT_TS, _REDIS_HOST_REDACTED
    if not _ENABLED:
        return None
    url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        if not _REDIS_TRIED:
            logger.info(
                "rate_limiter: REDIS_URL not set — cross-pod counter OFF, "
                "using per-process in-memory fallback (multi-pod deployments "
                "will not enforce a shared ceiling)")
            _REDIS_TRIED = True
        return None
    if _REDIS_CLIENT is not None and _REDIS_BACKEND_ACTIVE:
        return _REDIS_CLIENT
    _REDIS_LAST_ATTEMPT_TS = time.time()
    _REDIS_HOST_REDACTED = _redact_redis_url(url)
    try:
        from redis import asyncio as aioredis
        client = aioredis.from_url(
            url, encoding="utf-8", decode_responses=False,
            socket_connect_timeout=2.0, socket_timeout=2.0,
            retry_on_timeout=False, health_check_interval=30,
        )
        await client.ping()
        # Pre-load the Lua script so subsequent calls use EVALSHA (small
        # payload, ~half the wire bytes of full EVAL each time).
        _REDIS_LUA_SHA = await client.script_load(_RL_LUA)
        _REDIS_CLIENT = client
        _REDIS_BACKEND_ACTIVE = True
        _REDIS_LAST_ERROR = None
        if not _REDIS_TRIED:
            logger.info(
                "rate_limiter: Redis backend LIVE — cross-pod shared "
                "counter ON (host=%s)", _REDIS_HOST_REDACTED)
            _REDIS_TRIED = True
        return client
    except Exception as e:
        _REDIS_CLIENT = None
        _REDIS_BACKEND_ACTIVE = False
        # Capture the FULL error string (type + message) so the health
        # probe can surface WHY the connection failed. Truncated to
        # 300 chars so a huge traceback doesn't blow up the JSON.
        _REDIS_LAST_ERROR = f"{type(e).__name__}: {str(e)[:250]}"
        # Log every attempt (not just the first) so a Redis flap is
        # visible in `journalctl -u backend | grep rate_limiter`.
        logger.warning(
            "rate_limiter: Redis unavailable at host=%s error=%s — "
            "falling back to per-process in-memory",
            _REDIS_HOST_REDACTED, _REDIS_LAST_ERROR)
        _REDIS_TRIED = True
        return None


async def check_rate_limit_async(key: str, limit_per_minute: int) -> bool:
    """Async, Redis-shared sliding-window rate limit.

    Returns True if the request is allowed, False if it should be
    429'd. Uses a Redis sorted-set + Lua atomic-check when
    `REDIS_URL` is configured (shared across all pods), silently
    falls back to the per-process in-memory `check_rate_limit` on
    Redis outage or missing env.

    Non-raising by design — a Redis timeout MUST NOT block the
    request. If Redis is down we treat it as "allow" via the in-
    memory fallback so users don't get 429s because our cache is
    sick.
    """
    if not _ENABLED:
        return True
    client = await _ensure_redis()
    if client is None:
        # No Redis → per-process bucket (previous behaviour).
        return check_rate_limit(key, limit_per_minute)
    try:
        # Millisecond-resolution timestamps so bursts within the same
        # second don't collide on the ZSET member score.
        now_ms = int(time.time() * 1000)
        window_ms = int(_WINDOW_SEC * 1000)
        # Unique member per call — ZADD is idempotent by score+member,
        # so we need a distinct member for every request to actually
        # log 1 entry per call. uuid4 hex is 32 bytes; cheap.
        mid = uuid.uuid4().hex
        rkey = f"{_REDIS_PREFIX}{key}"
        # EVALSHA when the script is loaded (fast path), fall back to
        # EVAL on NOSCRIPT (rare — Redis restarted).
        try:
            if _REDIS_LUA_SHA is None:
                raise RuntimeError("lua_sha_missing")
            res = await client.evalsha(
                _REDIS_LUA_SHA, 1, rkey,
                str(now_ms), str(window_ms), str(limit_per_minute), mid,
            )
        except Exception as e:
            # NOSCRIPT or dropped SHA — reload once and retry via EVAL.
            if "NOSCRIPT" in repr(e) or "lua_sha_missing" in repr(e):
                res = await client.eval(
                    _RL_LUA, 1, rkey,
                    str(now_ms), str(window_ms),
                    str(limit_per_minute), mid,
                )
            else:
                raise
        return bool(int(res))
    except Exception as e:                                    # noqa: BLE001
        # Redis went sideways mid-request. Fail OPEN (allow) via the
        # in-memory path so we don't 429 legitimate users on a Redis
        # outage. Log so we notice.
        logger.warning(
            "rate_limiter: Redis path failed (%s), fell back to in-memory",
            type(e).__name__)
        return check_rate_limit(key, limit_per_minute)


def redis_backend_active() -> bool:
    """Observability: True if the Redis path is live and being used.
    Used by pytest + admin /healthz to assert multi-pod protection is
    actually on."""
    return _REDIS_BACKEND_ACTIVE


def redis_diag() -> dict:
    """Return structured diagnostic info for the /health/rate-limiter
    probe. NEVER returns credentials — only `host:port`, error type,
    and truncated error message. Safe to expose over public HTTP."""
    return {
        "host":              _REDIS_HOST_REDACTED,
        "last_error":        _REDIS_LAST_ERROR,
        "last_attempt_ts":   _REDIS_LAST_ATTEMPT_TS,
        "backend_active":    _REDIS_BACKEND_ACTIVE,
    }

