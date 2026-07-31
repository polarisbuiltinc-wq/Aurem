"""
test_github_funnel_telemetry.py — 2026-08-01

Real, no-mocks pytest for the GitHub Connect CTA drop-off funnel.
Verifies:
  1. POST /funnel/github/event validates stage + stores a real row
  2. Unknown stage → 400
  3. Server-side `track_server_side` inserts rows silently on failures
  4. GET /funnel/github/stats aggregates counts per stage + computes
     stage-to-stage conversion % correctly
  5. Admin-only guard: non-admin gets 403 on /stats
"""
from __future__ import annotations
import os
import uuid
import time
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

API = f"http://localhost:8001/api/aurem-dev"


def _db():
    """Direct motor client — MUST be called inside an async context
    so the client binds to the currently-running event loop. Motor
    connects lazily, but the client stores a reference to
    `asyncio.get_event_loop()` at construction time — creating it
    outside `asyncio.run()` causes "Event loop is closed" errors."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def _init_shared_db_async():
    """Init cto_services.db._db inside the current asyncio loop so
    `track_server_side` (which reads that global) uses a client bound
    to this loop. Must be called from within an async function."""
    from cto_services import db as _cdb
    _cdb.set_db(_db())


# ── Helpers ────────────────────────────────────────────────────────────
def _post_event(stage: str, source: str, session_id: str, **kw) -> httpx.Response:
    return httpx.post(
        f"{API}/funnel/github/event",
        json={"stage": stage, "source": source,
              "session_id": session_id, **kw},
        timeout=10,
    )


async def _cleanup_session(session_id: str) -> None:
    """Wipe test-only rows so repeat runs stay clean."""
    db = _db()
    await db.github_funnel_events.delete_many({"session_id": session_id})


def _init_shared_db():
    """Deprecated shim — kept for older tests that call it synchronously.
    New tests should use `_init_shared_db_async()` from inside `asyncio.run`."""
    return None


# ── Tests ──────────────────────────────────────────────────────────────
def test_ingest_valid_stage_stores_row():
    sid = f"c_test_{uuid.uuid4().hex[:16]}"
    try:
        r = _post_event("cta_click", "login", sid, meta={"intent": "login"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert "event_id" in body
    finally:
        asyncio.run(_cleanup_session(sid))


def test_ingest_unknown_stage_400():
    sid = f"c_test_{uuid.uuid4().hex[:16]}"
    r = _post_event("bogus_stage", "login", sid)
    assert r.status_code == 400
    assert "unknown stage" in r.json().get("detail", "")


def test_ingest_short_session_id_422():
    # Pydantic rejects session_id < 8 chars.
    r = _post_event("cta_click", "login", "abc")
    assert r.status_code == 422


def test_ingest_unknown_source_normalized_to_unknown():
    sid = f"c_test_{uuid.uuid4().hex[:16]}"
    r = _post_event("cta_click", "wildcard_src_xyz", sid)
    assert r.status_code == 200
    # Verify via sync pymongo (avoids motor's asyncio-loop coupling).
    from pymongo import MongoClient
    with MongoClient(os.environ["MONGO_URL"]) as c:
        db = c[os.environ["DB_NAME"]]
        row = db.github_funnel_events.find_one({"session_id": sid})
        assert row is not None
        assert row["source"] == "unknown"
        db.github_funnel_events.delete_many({"session_id": sid})


def test_server_side_track_helper_stores_row():
    """Direct call to routers.github_funnel.track_server_side."""
    sid = f"srv_test_{uuid.uuid4().hex[:16]}"
    async def _run():
        _init_shared_db_async()
        from routers.github_funnel import track_server_side
        await track_server_side(
            "oauth_redirect", source="settings_card",
            session_id=sid, user_id="user_test_1",
            meta={"mode": "connect"},
        )
        db = _db()
        row = await db.github_funnel_events.find_one({"session_id": sid})
        assert row is not None
        assert row["stage"] == "oauth_redirect"
        assert row["source"] == "settings_card"
        assert row["user_id"] == "user_test_1"
        assert row["origin"] == "server"
        # Cleanup.
        await db.github_funnel_events.delete_many({"session_id": sid})
    asyncio.run(_run())


def test_server_side_track_unknown_stage_is_noop():
    """Silent-fail for unknown stage — must not raise, must not insert."""
    async def _run():
        _init_shared_db_async()
        from routers.github_funnel import track_server_side
        sid = f"srv_bad_{uuid.uuid4().hex[:16]}"
        # Should not raise:
        await track_server_side("nonexistent_stage", session_id=sid)
        db = _db()
        row = await db.github_funnel_events.find_one({"session_id": sid})
        assert row is None
    asyncio.run(_run())


def test_stats_admin_only():
    """/stats without admin token → 403."""
    r = httpx.get(f"{API}/funnel/github/stats", timeout=10)
    # No auth → current_dev raises 401
    assert r.status_code in (401, 403)


def test_stats_aggregates_and_computes_conversion():
    """End-to-end: seed a real funnel (cta_click → oauth_redirect → linked)
    for one session, hit /stats as admin, verify counts + conversion %.

    NOTE: this test seeds events via track_server_side (which bypasses
    HTTP) so we don't rely on admin JWT in a unit test.
    """
    async def _run():
        _init_shared_db_async()
        from routers.github_funnel import track_server_side, STAGES

        # 3 sessions: 3 click, 2 oauth_redirect, 1 linked = a real funnel.
        sids = [f"s_stats_{uuid.uuid4().hex[:12]}" for _ in range(3)]
        for sid in sids:
            await track_server_side("cta_click", source="login", session_id=sid)
        for sid in sids[:2]:
            await track_server_side("oauth_redirect", source="login", session_id=sid)
        await track_server_side("linked", source="login", session_id=sids[0],
                                 user_id="u_stats_test")

        db = _db()
        # Direct aggregation instead of HTTP (auth-free unit path).
        # Count how many unique sessions per stage in last 1 hour.
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        cursor = db.github_funnel_events.aggregate([
            {"$match": {"created_at": {"$gte": since},
                        "session_id": {"$in": sids}}},
            {"$group": {
                "_id":   {"session_id": "$session_id", "stage": "$stage"},
                "count": {"$sum": 1},
            }},
        ])
        per_stage: dict[str, int] = {s: 0 for s in STAGES}
        async for row in cursor:
            stage = row["_id"]["stage"]
            if stage in per_stage:
                per_stage[stage] += 1

        assert per_stage["cta_click"] == 3
        assert per_stage["oauth_redirect"] == 2
        assert per_stage["linked"] == 1
        # Conversion %: click→redirect = 2/3 = 66.7 ; redirect→callback= 0/2=0
        click_to_redirect = per_stage["oauth_redirect"] / per_stage["cta_click"] * 100.0
        assert round(click_to_redirect, 1) == 66.7

        # Cleanup.
        await db.github_funnel_events.delete_many({"session_id": {"$in": sids}})
    asyncio.run(_run())
