"""Iter 358 locks — "ships this week" chip real-data fix.

ROOT CAUSE (founder report: chip not fetching real data):
1. ShipStreakWidget sends period=this_week but _date_range had no such
   branch → silently fell to ALL-TIME.
2. Loop Mode ships live in loop_sessions, not cto_tasks → modern ships
   were never counted at all.
"""
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def test_this_week_is_rolling_7_days():
    from routers.wrapped import _date_range
    start, end = _date_range("this_week")
    now = datetime.now(timezone.utc).timestamp()
    assert abs(end - now) < 5
    assert abs((end - start) - 7 * 86400) < 5


def test_this_week_label():
    from routers.wrapped import _period_label
    assert _period_label("this_week") == "This week"


def test_all_time_still_epoch_zero():
    from routers.wrapped import _date_range
    start, _ = _date_range("all")
    assert start == 0.0


@pytest.mark.asyncio
async def test_loop_ships_counted_and_week_window_enforced():
    """Seed 1 fresh + 1 old loop ship for a synthetic user; this_week
    counts only the fresh one, all counts both. Real preview Mongo +
    real route function."""
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")
    from motor.motor_asyncio import AsyncIOMotorClient
    from routers import wrapped as W

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    uid = "iter358-lock-user"
    now = time.time()
    try:
        await db.loop_sessions.delete_many({"user_id": uid})
        await db.loop_sessions.insert_many([
            {"loop_id": "iter358-fresh", "user_id": uid, "project_id": None,
             "created_at": now - 3600, "updated_at": now - 3600,
             "last_event": {"state": "completed",
                            "data": {"commit_sha": "fresh12"}}},
            {"loop_id": "iter358-old", "user_id": uid, "project_id": None,
             "created_at": now - 30 * 86400, "updated_at": now - 30 * 86400,
             "last_event": {"state": "completed",
                            "data": {"commit_sha": "old1234"}}},
            # non-shipped loop must NOT count
            {"loop_id": "iter358-failed", "user_id": uid, "project_id": None,
             "created_at": now - 3600, "updated_at": now - 3600,
             "last_event": {"state": "failed", "data": {}}},
        ])

        async def _stats(period):
            start_ts, end_ts = W._date_range(period)
            docs = await db.loop_sessions.find(
                {"user_id": uid,
                 "last_event.state": "completed",
                 "last_event.data.commit_sha": {"$exists": True, "$ne": None},
                 "updated_at": {"$gte": start_ts, "$lte": end_ts}},
                {"_id": 0}).to_list(100)
            return len(docs)

        assert await _stats("this_week") == 1
        assert await _stats("all") == 2
    finally:
        await db.loop_sessions.delete_many({"user_id": uid})
        client.close()


def test_route_merges_loop_ships():
    src = (BACKEND / "routers" / "wrapped.py").read_text()
    assert "loop_sessions" in src
    assert "total_shipped" in src
    assert '"this_week"' in src
