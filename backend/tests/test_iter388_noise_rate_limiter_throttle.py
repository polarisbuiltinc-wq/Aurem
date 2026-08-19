"""
Iter 388-noise · services/rate_limiter.py warning throttle regression.

Bug: `_ensure_redis()` logged WARNING on EVERY failed connect attempt.
When Redis was sustained-unhealthy (Upstash quota exhausted with the
same "max requests limit exceeded" ResponseError), the pod emitted
100+ identical WARNING lines per minute — real signal drowned by
noise and pushed logs past ingestion quotas.  User flagged this as
looking like a deployment failure in the log stream.

Fix policy:
  · NEW error signature       → log immediately (flap still visible)
  · SAME signature, new minute → log once with a suppression tally
  · SAME signature, same minute → drop silently (counted)
"""
from __future__ import annotations

import logging
import time
import pytest


@pytest.mark.asyncio
async def test_new_error_logs_immediately(monkeypatch, caplog):
    """First occurrence of an error signature always logs."""
    from services import rate_limiter as rl

    monkeypatch.setattr(rl, "_ENABLED", True)
    monkeypatch.setattr(rl, "_REDIS_CLIENT", None)
    monkeypatch.setattr(rl, "_REDIS_BACKEND_ACTIVE", False)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_SIG", None)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_MINUTE", 0)
    monkeypatch.setattr(rl, "_REDIS_LAST_ATTEMPT_TS", None)
    monkeypatch.setattr(rl, "_REDIS_LAST_ERROR", None)
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379")

    async def _fail_ping(self):
        raise ConnectionError("simulated outage A")

    from redis import asyncio as aioredis
    monkeypatch.setattr(aioredis.Redis, "ping", _fail_ping)

    with caplog.at_level(logging.WARNING, logger="services.rate_limiter"):
        client = await rl._ensure_redis()
    assert client is None
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "outage A" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_repeated_same_error_within_minute_suppressed(monkeypatch, caplog):
    """Second+ occurrence of the SAME signature in the SAME minute
    bucket is dropped silently — the fix's core anti-noise policy."""
    from services import rate_limiter as rl

    monkeypatch.setattr(rl, "_ENABLED", True)
    monkeypatch.setattr(rl, "_REDIS_CLIENT", None)
    monkeypatch.setattr(rl, "_REDIS_BACKEND_ACTIVE", False)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_SIG", None)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_MINUTE", 0)
    monkeypatch.setattr(rl, "_REDIS_WARN_SUPPRESSED_COUNT", 0)
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379")

    async def _fail_ping(self):
        raise ConnectionError("same-outage")

    from redis import asyncio as aioredis
    monkeypatch.setattr(aioredis.Redis, "ping", _fail_ping)

    # Pin time so all 5 attempts fall in the same minute bucket.
    fixed_ts = 1_800_000_000.0
    monkeypatch.setattr(time, "time", lambda: fixed_ts)

    with caplog.at_level(logging.WARNING, logger="services.rate_limiter"):
        for _ in range(5):
            monkeypatch.setattr(rl, "_REDIS_LAST_ATTEMPT_TS", None)
            await rl._ensure_redis()

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, f"expected exactly 1 warning, got {len(warnings)}"
    # Suppression counter tracks the drops.
    assert rl._REDIS_WARN_SUPPRESSED_COUNT == 4


@pytest.mark.asyncio
async def test_minute_rollover_logs_with_suppression_tally(monkeypatch, caplog):
    """After the minute bucket advances with the same error signature,
    ONE new WARNING logs including the suppressed count."""
    from services import rate_limiter as rl

    monkeypatch.setattr(rl, "_ENABLED", True)
    monkeypatch.setattr(rl, "_REDIS_CLIENT", None)
    monkeypatch.setattr(rl, "_REDIS_BACKEND_ACTIVE", False)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_SIG", None)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_MINUTE", 0)
    monkeypatch.setattr(rl, "_REDIS_WARN_SUPPRESSED_COUNT", 0)
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379")

    async def _fail_ping(self):
        raise ConnectionError("quota-exceeded")

    from redis import asyncio as aioredis
    monkeypatch.setattr(aioredis.Redis, "ping", _fail_ping)

    # First minute: 3 attempts (1 warn, 2 suppressed).
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)
    with caplog.at_level(logging.WARNING, logger="services.rate_limiter"):
        for _ in range(3):
            monkeypatch.setattr(rl, "_REDIS_LAST_ATTEMPT_TS", None)
            await rl._ensure_redis()
        # Advance minute bucket → 1 more warn (with tally=2).
        monkeypatch.setattr(time, "time", lambda: 1_800_000_060.0)
        monkeypatch.setattr(rl, "_REDIS_LAST_ATTEMPT_TS", None)
        await rl._ensure_redis()

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 2
    assert "2 identical warnings suppressed" in warnings[1].getMessage()


@pytest.mark.asyncio
async def test_different_error_signature_bypasses_throttle(monkeypatch, caplog):
    """A DIFFERENT error signature (real flap / new failure mode)
    always logs immediately — the throttle only suppresses IDENTICAL
    errors, so incident visibility for changing conditions is
    preserved."""
    from services import rate_limiter as rl

    monkeypatch.setattr(rl, "_ENABLED", True)
    monkeypatch.setattr(rl, "_REDIS_CLIENT", None)
    monkeypatch.setattr(rl, "_REDIS_BACKEND_ACTIVE", False)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_SIG", None)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_MINUTE", 0)
    monkeypatch.setattr(rl, "_REDIS_WARN_SUPPRESSED_COUNT", 0)
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379")
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)

    calls = {"n": 0}
    async def _flap_ping(self):
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            raise ConnectionError("outage-A")
        raise TimeoutError("outage-B-different")

    from redis import asyncio as aioredis
    monkeypatch.setattr(aioredis.Redis, "ping", _flap_ping)

    with caplog.at_level(logging.WARNING, logger="services.rate_limiter"):
        for _ in range(4):
            monkeypatch.setattr(rl, "_REDIS_LAST_ATTEMPT_TS", None)
            await rl._ensure_redis()

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    # 4 attempts, alternating errors → each new signature logs = 4 warns
    assert len(warnings) == 4
