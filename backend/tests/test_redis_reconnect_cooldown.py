"""test_redis_reconnect_cooldown.py — 2026-08-19

Production incident: Upstash Redis hit its plan's request quota
("max requests limit exceeded"). `_ensure_redis()` had NO cooldown
after a failed connection attempt — every single incoming request
re-attempted a fresh TLS connect + PING + SCRIPT LOAD to Upstash, got
rejected again, and paid that round-trip on the request's critical
path. Under concurrent load this reconnect storm surfaced as
`RuntimeError: No response returned` on unrelated endpoints (not just
rate-limited ones), because the ASGI pipeline was starved.

Fix: `_ensure_redis()` now skips reconnect attempts for
`_REDIS_RETRY_COOLDOWN_S` (30s) after a failure, returning `None`
immediately (== rate limiter fails open, exactly as before — this
only changes HOW OFTEN we retry the handshake, not the fail-open
behavior).
"""
from __future__ import annotations

import time

import pytest


@pytest.mark.asyncio
async def test_second_attempt_within_cooldown_skips_reconnect(monkeypatch):
    from services import rate_limiter as rl

    monkeypatch.setattr(rl, "_ENABLED", True)
    monkeypatch.setattr(rl, "_REDIS_CLIENT", None)
    monkeypatch.setattr(rl, "_REDIS_BACKEND_ACTIVE", False)
    monkeypatch.setattr(rl, "_REDIS_LAST_ATTEMPT_TS", None)
    monkeypatch.setattr(rl, "_REDIS_LAST_ERROR", None)
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379")

    calls = {"n": 0}

    async def _fail_ping(self):
        calls["n"] += 1
        raise ConnectionError("max requests limit exceeded")

    from redis import asyncio as aioredis
    monkeypatch.setattr(aioredis.Redis, "ping", _fail_ping)

    fixed_ts = 1_800_000_000.0
    monkeypatch.setattr(time, "time", lambda: fixed_ts)

    # First attempt — real connect, fails, records the error + timestamp.
    r1 = await rl._ensure_redis()
    assert r1 is None
    assert calls["n"] == 1

    # 2nd..10th attempts, all within the 30s cooldown, same frozen clock —
    # must NOT touch the network again.
    for _ in range(9):
        r = await rl._ensure_redis()
        assert r is None
    assert calls["n"] == 1, (
        "reconnect storm — _ensure_redis() re-attempted the handshake "
        "during the cooldown window"
    )


@pytest.mark.asyncio
async def test_attempt_after_cooldown_expires_retries(monkeypatch):
    from services import rate_limiter as rl

    monkeypatch.setattr(rl, "_ENABLED", True)
    monkeypatch.setattr(rl, "_REDIS_CLIENT", None)
    monkeypatch.setattr(rl, "_REDIS_BACKEND_ACTIVE", False)
    monkeypatch.setattr(rl, "_REDIS_LAST_ATTEMPT_TS", None)
    monkeypatch.setattr(rl, "_REDIS_LAST_ERROR", None)
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379")

    calls = {"n": 0}

    async def _fail_ping(self):
        calls["n"] += 1
        raise ConnectionError("max requests limit exceeded")

    from redis import asyncio as aioredis
    monkeypatch.setattr(aioredis.Redis, "ping", _fail_ping)

    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)
    await rl._ensure_redis()
    assert calls["n"] == 1

    # Still within cooldown at +10s.
    monkeypatch.setattr(time, "time", lambda: 1_800_000_010.0)
    await rl._ensure_redis()
    assert calls["n"] == 1

    # Cooldown (30s) has elapsed — must retry for real.
    monkeypatch.setattr(time, "time", lambda: 1_800_000_031.0)
    await rl._ensure_redis()
    assert calls["n"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
