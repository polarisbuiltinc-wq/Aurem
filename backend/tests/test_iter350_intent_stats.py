"""Iter 350 — loop intent-gate observability locks."""
import asyncio
import os
import re

import pytest
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from services.loop_intent_stats import (  # noqa: E402
    BUCKETS, _hour_key, get_intent_stats, record_intent_stat,
)

_ROUTER_SRC = open(
    os.path.join(os.path.dirname(__file__), "..", "routers", "loop.py")).read()
_ENGINE_SRC = open(
    os.path.join(os.path.dirname(__file__), "..",
                 "services", "loop_engine.py")).read()


def _db():
    import motor.motor_asyncio
    cli = motor.motor_asyncio.AsyncIOMotorClient(os.environ["MONGO_URL"])
    return cli[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_record_and_read_hourly_buckets():
    db = _db()
    hk = _hour_key()
    await db.loop_intent_stats.delete_one({"hour_key": hk})
    await record_intent_stat(db, "chat_redirect")
    await record_intent_stat(db, "chat_redirect")
    await record_intent_stat(db, "loop_triggered")
    await record_intent_stat(db, "timeout_failed")
    stats = await get_intent_stats(db, hours=2)
    assert stats["totals"]["chat_redirect"] >= 2
    assert stats["totals"]["loop_triggered"] >= 1
    assert stats["totals"]["timeout_failed"] >= 1
    assert stats["redirect_rate"] is not None
    row = [r for r in stats["hourly"] if r["hour_key"] == hk][0]
    assert row["chat_redirect"] >= 2
    await db.loop_intent_stats.delete_one({"hour_key": hk})


@pytest.mark.asyncio
async def test_record_invalid_bucket_is_noop():
    db = _db()
    await record_intent_stat(db, "not_a_bucket")   # must not raise
    await record_intent_stat(None, "chat_redirect")  # db None → noop


def test_bucket_names_locked():
    assert BUCKETS == ("chat_redirect", "loop_triggered", "timeout_failed")


# ── Source locks — instrumentation stays wired ───────────────────────
def test_router_records_redirect_and_trigger():
    assert 'record_intent_stat(db, "chat_redirect")' in _ROUTER_SRC
    assert 'record_intent_stat(db, "loop_triggered")' in _ROUTER_SRC


def test_engine_records_timeout():
    assert "_record_timeout_stat" in _ENGINE_SRC
    assert 'record_intent_stat(self.db, "timeout_failed")' in _ENGINE_SRC


def test_intent_stats_endpoint_is_founder_only():
    m = re.search(
        r'@router\.get\("/intent-stats"\).*?(?=\n@router)',
        _ROUTER_SRC, re.DOTALL,
    )
    assert m, "/loop/intent-stats endpoint missing"
    src = m.group(0)
    assert "is_founder" in src and "403" in src, (
        "intent-stats must keep the founder-only 403 gate")
