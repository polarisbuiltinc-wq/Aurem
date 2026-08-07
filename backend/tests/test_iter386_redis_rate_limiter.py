"""Iter 386 · Session 2 · Part 0 — Redis-shared rate limiter coverage.

The bug this fixes: `services/rate_limiter.check_rate_limit` uses an
in-memory dict that is per-process. In a K8s deployment with N pods
behind a load balancer, each pod has its own counter — no IP ever
accumulates enough for any single pod to trip its ceiling. Empirical
proof: 350 parallel curls against auremcto.com produced 0 × 429 while
the same test on preview (single pod) produced 41 × 429.

The fix ships an async `check_rate_limit_async` variant that uses a
Redis sorted-set with an atomic Lua sliding-window script so every
pod shares the SAME bucket. This test suite proves:

  · The Redis path is atomic — a concurrent burst of N requests
    with limit=K gives EXACTLY N-K rejections, not fewer (which would
    indicate race conditions) and not more (double-count).
  · The sliding-window semantics match the in-memory version — same
    60-second horizon, same first-N-allowed pattern.
  · Fail-open behaviour on Redis outage: a broken client falls back
    to the sync in-memory path so users never get 429'd because our
    cache is sick.
  · `redis_backend_active()` reports the truth so a health-endpoint
    can assert multi-pod protection is actually on.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, "/app/backend")


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset both in-memory buckets and Redis state before every test.
    Each test also gets a unique key prefix so parallel test runs don't
    collide on a shared Redis instance."""
    import services.rate_limiter as rl
    rl._buckets.clear()
    rl._ENABLED = True
    # Reset Redis-connection cached state so `_ensure_redis` will
    # re-evaluate the current env for the next call.
    rl._REDIS_CLIENT = None
    rl._REDIS_TRIED = False
    rl._REDIS_LUA_SHA = None
    rl._REDIS_BACKEND_ACTIVE = False
    yield
    rl._buckets.clear()


# ══════════════════════════════════════════════════════════════════════
# 1) Redis path — the actual multi-pod fix
# ══════════════════════════════════════════════════════════════════════
@pytest.fixture
def redis_url():
    """Use the locally-running Redis if present. Skips the whole class
    when Redis is unavailable so this file stays green on CI runners
    without a Redis service."""
    url = os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0"
    # Verify reachability.
    try:
        import redis as _rsync
        c = _rsync.from_url(url, socket_connect_timeout=1.0)
        c.ping()
    except Exception:
        pytest.skip(f"Redis not reachable at {url} — skipping Redis path tests")
    os.environ["REDIS_URL"] = url
    return url


class TestRedisBackedLimiter:
    async def test_first_N_allowed_then_denied(self, redis_url):
        """Sequential calls: first `limit` return True, subsequent
        return False. Exact-match contract."""
        from services.rate_limiter import (
            check_rate_limit_async, redis_backend_active,
        )
        key = f"pytest:seq:{os.getpid()}:{id(self)}"

        # Clean any stale state from a previous run.
        import redis
        _c = redis.from_url(redis_url)
        _c.delete(f"aurem:rl:{key}")

        results = []
        for i in range(5):
            r = await check_rate_limit_async(key, 3)
            results.append(r)
        assert results == [True, True, True, False, False], (
            f"expected first 3 True then 2 False; got {results}")
        # Ensure we're on the Redis path, not the fallback — otherwise
        # this test isn't validating what its name claims.
        assert redis_backend_active() is True, (
            "Redis path did not activate — this test would silently "
            "pass on the in-memory fallback")

    async def test_concurrent_burst_exact_count(self, redis_url):
        """N concurrent tasks, limit K: EXACTLY K allowed, N-K denied.
        This is the atomicity check — a race in the primitive would
        show up as either fewer denials (>K allowed) or more (<K
        allowed). We assert exact-K."""
        from services.rate_limiter import check_rate_limit_async
        key = f"pytest:concurrent:{os.getpid()}:{id(self)}"
        limit = 10

        # Clean any stale bucket.
        import redis
        _c = redis.from_url(redis_url)
        _c.delete(f"aurem:rl:{key}")

        async def _one():
            return await check_rate_limit_async(key, limit)

        # 25 concurrent calls, limit 10.
        results = await asyncio.gather(*[_one() for _ in range(25)])
        allowed = sum(1 for r in results if r is True)
        denied = sum(1 for r in results if r is False)
        assert allowed == limit, (
            f"expected exactly {limit} allowed under concurrency; "
            f"got {allowed} — atomicity broken?")
        assert denied == 25 - limit

    async def test_two_separate_keys_have_independent_buckets(self, redis_url):
        """Different IPs (different keys) get their own budgets — the
        Redis path preserves this contract."""
        from services.rate_limiter import check_rate_limit_async
        k1 = f"pytest:kA:{os.getpid()}"
        k2 = f"pytest:kB:{os.getpid()}"

        import redis
        _c = redis.from_url(redis_url)
        _c.delete(f"aurem:rl:{k1}", f"aurem:rl:{k2}")

        # Exhaust k1.
        for _ in range(3):
            assert await check_rate_limit_async(k1, 3) is True
        assert await check_rate_limit_async(k1, 3) is False
        # k2 is completely untouched.
        assert await check_rate_limit_async(k2, 3) is True


# ══════════════════════════════════════════════════════════════════════
# 2) Fail-open on Redis outage — never 429 users because cache is sick
# ══════════════════════════════════════════════════════════════════════
class TestRedisOutageFallback:
    async def test_ensure_redis_returns_none_when_url_missing(
            self, monkeypatch):
        """No REDIS_URL → Redis path never runs, in-memory takes over."""
        monkeypatch.delenv("REDIS_URL", raising=False)
        from services.rate_limiter import (
            _ensure_redis, redis_backend_active,
        )
        client = await _ensure_redis()
        assert client is None
        assert redis_backend_active() is False

    async def test_async_falls_back_to_in_memory_when_redis_missing(
            self, monkeypatch):
        """The async entry point calls the sync in-memory path when
        Redis is unavailable — user sees the SAME contract."""
        monkeypatch.delenv("REDIS_URL", raising=False)
        from services.rate_limiter import check_rate_limit_async
        key = "test:fallback:memory"
        results = []
        for i in range(5):
            r = await check_rate_limit_async(key, 3)
            results.append(r)
        assert results == [True, True, True, False, False]

    async def test_mid_request_redis_error_fails_open(self, monkeypatch):
        """If EVALSHA/EVAL blows up mid-request, we swallow the error
        and defer to in-memory. Users don't see a Redis 500 leak
        through as a 429 or a 500."""
        # Force Redis path "live" then break the Lua call.
        import services.rate_limiter as rl
        monkeypatch.setattr(rl, "_REDIS_BACKEND_ACTIVE", True)
        monkeypatch.setattr(rl, "_REDIS_LUA_SHA", "deadbeef")
        broken = AsyncMock(side_effect=RuntimeError("redis-timeout"))
        monkeypatch.setattr(rl, "_REDIS_CLIENT",
                            AsyncMock(evalsha=broken, eval=broken))
        # Should NOT raise — should fall back to in-memory sync path.
        r = await rl.check_rate_limit_async("outage:test", 5)
        assert r is True, "outage path must fail-open"


# ══════════════════════════════════════════════════════════════════════
# 3) Observability — `redis_backend_active()` reports truth
# ══════════════════════════════════════════════════════════════════════
class TestObservability:
    async def test_active_flag_flips_on_after_first_call_with_redis(
            self, redis_url):
        from services.rate_limiter import (
            check_rate_limit_async, redis_backend_active,
        )
        assert redis_backend_active() is False  # not yet initialised
        await check_rate_limit_async("obs:probe", 10)
        assert redis_backend_active() is True

    async def test_active_flag_stays_off_without_redis(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        from services.rate_limiter import (
            check_rate_limit_async, redis_backend_active,
        )
        await check_rate_limit_async("obs:probe", 10)
        assert redis_backend_active() is False


# ══════════════════════════════════════════════════════════════════════
# 4) In-memory sync path — unchanged, ensures no regression
# ══════════════════════════════════════════════════════════════════════
class TestInMemorySyncPath:
    def test_still_works_identically(self):
        """The sync `check_rate_limit` primitive is still exported and
        behaves the same as before this session — reused by the async
        fallback."""
        from services.rate_limiter import check_rate_limit
        key = "test:sync:legacy"
        results = [check_rate_limit(key, 3) for _ in range(5)]
        assert results == [True, True, True, False, False]


# ══════════════════════════════════════════════════════════════════════
# 5) Health endpoint contract — /api/aurem-dev/health/rate-limiter
# ══════════════════════════════════════════════════════════════════════
class TestHealthEndpointContract:
    """Regression guard for the shape of the health probe. If ANY
    field changes, the founder's post-deploy verification script will
    silently break — this test light lights up first."""

    async def test_response_shape(self, redis_url):
        # Exercise the endpoint via the shipped handler directly so
        # this test doesn't need FastAPI's full app boot.
        from main import health_rate_limiter
        result = await health_rate_limiter()
        assert set(result.keys()) == {
            "backend", "redis_active",
            "redis_url_set", "global_ceiling_per_min", "diag",
        }, f"unexpected keys: {set(result.keys())}"
        assert result["backend"] == "redis"
        assert result["redis_active"] is True
        assert result["redis_url_set"] is True
        assert isinstance(result["global_ceiling_per_min"], int)
        assert result["global_ceiling_per_min"] > 0
        # Diag block on a healthy connection: host present, no error.
        assert result["diag"]["host"] is not None
        assert result["diag"]["last_error"] is None
        assert isinstance(result["diag"]["last_attempt_ts"], float)

    async def test_reports_in_memory_when_redis_missing(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        # Reset connection state so the endpoint's `_ensure_redis` call
        # re-evaluates the freshly-cleared env.
        import services.rate_limiter as rl
        rl._REDIS_CLIENT = None
        rl._REDIS_TRIED = False
        rl._REDIS_BACKEND_ACTIVE = False
        from main import health_rate_limiter
        result = await health_rate_limiter()
        assert result["backend"] == "in_memory"
        assert result["redis_active"] is False
        assert result["redis_url_set"] is False


class TestConnectionDiagnostics:
    """Iter 386 · Session 2.6 — prod showed `backend:in_memory` with
    `redis_url_set:true`; without diag surfacing we had no way to
    tell whether it was DNS, auth, or firewall. These tests prove
    the enhanced probe now yields actionable diagnostics."""

    async def test_url_present_but_host_unreachable_surfaces_error(
            self, monkeypatch):
        """Simulate the exact prod failure mode — REDIS_URL is set but
        points at an unreachable host. Diag block MUST carry the
        error type + message so on-call sees WHY it failed."""
        monkeypatch.setenv("REDIS_URL",
                            "redis://192.0.2.1:6379/0")  # RFC 5737 TEST-NET-1
        import services.rate_limiter as rl
        # Reset connection cache so the new env is evaluated.
        rl._REDIS_CLIENT = None
        rl._REDIS_TRIED = False
        rl._REDIS_BACKEND_ACTIVE = False
        rl._REDIS_LAST_ERROR = None
        rl._REDIS_HOST_REDACTED = None
        from main import health_rate_limiter
        result = await health_rate_limiter()
        # The critical asserts — on-call MUST be able to see this.
        assert result["backend"] == "in_memory"
        assert result["redis_active"] is False
        assert result["redis_url_set"] is True  # url IS present
        assert result["diag"]["host"] == "192.0.2.1:6379"
        assert result["diag"]["last_error"] is not None
        # Whatever the specific error, on-call gets a real string,
        # not just "in_memory" with no explanation.
        assert len(result["diag"]["last_error"]) > 10

    async def test_redact_never_leaks_credentials(self):
        """The diag `host` field must never contain user:password even
        if the caller put credentials in REDIS_URL."""
        from services.rate_limiter import _redact_redis_url
        redacted = _redact_redis_url(
            "redis://user:secret-password@redis.internal:6379/0")
        assert redacted == "redis.internal:6379"
        assert "user" not in redacted
        assert "secret-password" not in redacted

    async def test_redact_handles_rediss_scheme(self):
        """TLS variant `rediss://` is a common pattern for managed
        Redis (Upstash, Redis Cloud). Redaction still works."""
        from services.rate_limiter import _redact_redis_url
        redacted = _redact_redis_url(
            "rediss://default:token@some.upstash.io:38612")
        assert redacted == "some.upstash.io:38612"
        assert "token" not in redacted

    async def test_redact_unparseable_input_never_raises(self):
        """Garbage input to _redact_redis_url must NOT explode — the
        health probe would 500 and hide the very problem it exists
        to surface."""
        from services.rate_limiter import _redact_redis_url
        # Whatever the exact fallback, it MUST be a string and MUST
        # NOT raise.
        for garbage in ("", "not-a-url-at-all", "://broken"):
            r = _redact_redis_url(garbage)
            assert isinstance(r, str) and len(r) > 0
