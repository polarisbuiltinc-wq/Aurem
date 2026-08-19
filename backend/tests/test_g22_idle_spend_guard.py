"""
test_g22_idle_spend_guard.py — 2026-08-19

Real behavioral tests for the idle-window LLM-spend guard built after
the OpenRouter/LongCat cost-leak investigation. Uses a throwaway Mongo
collection namespace (real DB, disposable docs) rather than mocks, so
this proves the actual aggregation query works against real data
shapes, not just that the function was called.
"""
from __future__ import annotations

import os
import time

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services.g22_idle_spend_guard import check_idle_window_spend

pytestmark = pytest.mark.asyncio


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


@pytest.fixture
async def _clean_usage():
    db = _db()
    marker = f"g22test_{int(time.time())}"
    yield db, marker
    await db.ora_chat_usage.delete_many({"session_id": marker})
    await db.incidents.delete_one({"source_key": "idle_llm_spend_window"})


async def test_no_activity_at_all_is_not_flagged(_clean_usage):
    """Zero docs in the window at all — nothing happened, nothing to flag."""
    db, marker = _clean_usage
    result = await check_idle_window_spend(db, hours_back=0.001)  # ~3.6s window
    assert result["flagged"] is False


async def test_known_actor_under_ceiling_not_flagged(_clean_usage):
    """system:health_check spending its expected tiny amount, no real
    user active — this is the EXPECTED post-fix steady state, not a leak."""
    db, marker = _clean_usage
    await db.ora_chat_usage.insert_one({
        "user_id": "system:health_check", "session_id": marker,
        "route": "admin_health_probe", "ts": time.time(), "cost_usd": 0.001,
    })
    result = await check_idle_window_spend(db, hours_back=1)
    assert result["real_user_activity"] is False
    assert result["flagged"] is False


async def test_unknown_actor_any_spend_is_flagged(_clean_usage):
    """A background job NOT in the reviewed allowlist logging real spend
    with zero user activity — exactly the pattern the founder asked to
    catch (a future 'just in case' LLM call with no explicit reason)."""
    db, marker = _clean_usage
    await db.ora_chat_usage.insert_one({
        "user_id": "system:mystery_cron", "session_id": marker,
        "route": "unexplained", "ts": time.time(), "cost_usd": 0.05,
    })
    result = await check_idle_window_spend(db, hours_back=1)
    assert result["flagged"] is True
    assert "system:mystery_cron" in result["unknown_actors"]
    inc = await db.incidents.find_one(
        {"source_key": "idle_llm_spend_window", "status": "open"})
    assert inc is not None
    assert inc["guard"] == "idle_llm_spend"


async def test_known_actor_over_ceiling_is_flagged(_clean_usage):
    """system:health_check spending WAY more than its expected ceiling —
    still worth a heads-up even though the actor itself is known-good."""
    db, marker = _clean_usage
    await db.ora_chat_usage.insert_one({
        "user_id": "system:health_check", "session_id": marker,
        "route": "admin_health_probe", "ts": time.time(), "cost_usd": 5.00,
    })
    result = await check_idle_window_spend(db, hours_back=1)
    assert result["flagged"] is True
    assert "system:health_check" not in result["unknown_actors"]  # known, just over budget


async def test_real_user_activity_suppresses_the_alert(_clean_usage):
    """Same unknown-actor spend as above, but a real user was also
    active in the same window — not an 'idle-window' leak by definition,
    so this must NOT flag (avoids false positives during normal usage)."""
    db, marker = _clean_usage
    now = time.time()
    await db.ora_chat_usage.insert_many([
        {"user_id": "system:mystery_cron", "session_id": marker,
         "route": "unexplained", "ts": now, "cost_usd": 0.05},
        {"user_id": "real_user_42", "session_id": marker,
         "route": "chat", "ts": now, "cost_usd": 0.01},
    ])
    result = await check_idle_window_spend(db, hours_back=1)
    assert result["real_user_activity"] is True
    assert result["flagged"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
