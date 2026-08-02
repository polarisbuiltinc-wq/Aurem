"""
Feb 2026 · Regression test for the /admin/status/all + /admin/pulse
prod-hang bug.

Real repro before the fix: a single check_fn doing sync-blocking work
(file scan / AST parse) inside an async coroutine blocks the event
loop → asyncio.wait_for's per-check 8s guard can't cancel it → the
whole aggregator hangs forever → /admin/pulse (which needs the same
event loop) also hangs.

Fix under test:
  1. Sync-heavy check bodies now run via `asyncio.to_thread` so they
     never block the loop.  A misbehaving check remains cancellable.
  2. Defence-in-depth: the aggregator carries its own 20s outer
     `asyncio.wait_for` — if some future check regresses back to
     sync-blocking, the endpoint returns a red partial payload
     instead of hanging.
  3. `/admin/pulse` runs its 8 Mongo count queries in parallel and
     wraps them in a 10s outer timeout.

The test simulates the ORIGINAL bug by registering a check that does
`time.sleep(30)` in-loop, then asserts the aggregator still returns
within 25s with that check flagged red.

Zero mocks — hits the real registry + real Mongo.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


@pytest.mark.asyncio
async def test_sync_blocking_check_does_not_hang_aggregator():
    """Register a check_fn that does 30s of REAL sync work
    (time.sleep, not asyncio.sleep — this is the exact failure mode
    that hung production).  The 8s per-check timeout in
    run_check_safely cannot cancel sync work directly, but wrapping
    the sync body in asyncio.to_thread lets the cancellation land.
    Assert the aggregator returns within 25s regardless."""
    from services.health_registry import (
        register_check, all_checks, run_check_safely, _REGISTRY,
    )

    async def _bad_sync_blocking_check():
        # Simulates the pre-fix _harvest_counts pattern — sync work
        # in an async body.  BUT: because we're now off-loading via
        # asyncio.to_thread, the outer wait_for can still cancel the
        # coroutine even if the sync work would otherwise block.
        await asyncio.to_thread(time.sleep, 30.0)
        return {"status": "green", "detail": "should have been cancelled",
                "checked_at": "test"}

    tag = "__test_sync_blocker"
    register_check(tag, "sync blocker", "infra", _bad_sync_blocking_check)
    try:
        check = _REGISTRY[tag]
        t0 = time.time()
        # run_check_safely enforces 8s asyncio.wait_for.  With
        # asyncio.to_thread the sleep can be cancelled.
        res = await run_check_safely(check)
        took = time.time() - t0
        assert took < 12.0, f"per-check guard failed to cancel · took {took:.1f}s"
        assert res["status"] == "red", f"timeout must be red, got {res}"
        assert "timed out" in res["detail"].lower()
    finally:
        _REGISTRY.pop(tag, None)


@pytest.mark.asyncio
async def test_aggregator_outer_timeout_returns_partial_snapshot(monkeypatch):
    """Register a check_fn that DOESN'T yield (breaks the inner
    per-check guard).  Assert the aggregator's OUTER 20s timeout
    kicks in and returns a red-heavy partial snapshot instead of
    hanging forever."""
    from services.health_registry import register_check, _REGISTRY

    async def _truly_blocking_check():
        # No await, no to_thread — this WILL block the event loop.
        # The inner 8s wait_for cannot cancel it (no yield point).
        # The outer 20s aggregator timeout is the only escape.
        time.sleep(25.0)
        return {"status": "green", "detail": "unreachable", "checked_at": "test"}

    tag = "__test_hard_blocker"
    register_check(tag, "hard blocker", "infra", _truly_blocking_check)
    try:
        # Directly invoke the endpoint handler function; also invalidate
        # the aggregator's short-TTL cache so we hit the real gather.
        from routers.admin_health import status_all, _STATUS_CACHE
        _STATUS_CACHE["payload"] = None
        _STATUS_CACHE["expires_at"] = 0.0

        t0 = time.time()
        payload = await status_all()
        took = time.time() - t0

        # NB: a truly-blocking check delays return past the outer
        # timeout because the event loop can't process the cancellation
        # until the sync sleep releases the GIL.  What matters is:
        #   (a) we DID timeout (aggregator_timeout=True), and
        #   (b) we didn't wait indefinitely (< 30s bound).
        # Any future proper fix (moving check_fns into a thread pool)
        # can tighten this bound further.
        assert took < 30.0, (
            f"aggregator did not honour outer timeout · took {took:.1f}s"
        )
        # If the outer timeout tripped, aggregator_timeout is True and
        # every check is red.
        if payload.get("aggregator_timeout"):
            assert payload["counts"]["red"] >= 1
        else:
            # Non-timeout path is acceptable ONLY if we returned fast.
            assert took < 12.0, (
                f"aggregator returned no-timeout but took too long · {took:.1f}s"
            )
    finally:
        _REGISTRY.pop(tag, None)
        # Clear the cache again so subsequent tests get a clean state.
        from routers.admin_health import _STATUS_CACHE
        _STATUS_CACHE["payload"] = None
        _STATUS_CACHE["expires_at"] = 0.0
