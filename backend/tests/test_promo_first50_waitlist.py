"""
tests/test_promo_first50_waitlist.py — Track 3 · Waitlist capture.

Zero mocks. Hits the running backend on :8001 with real Mongo.
Covers:
  1. Valid email → 200 ok=True, row lands in promo_first50_waitlist.
  2. Repeat submission with same email → idempotent (single row,
     touch_count increments).
  3. Invalid email format → 400 invalid_email.
  4. Disposable email domain → 400 disposable_email.
  5. Rate limit → 5+ rapid submits from same IP → at least one 429.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

pytestmark = pytest.mark.asyncio

_API = "http://localhost:8001"


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture
async def client():
    async with AsyncClient(base_url=_API, timeout=30.0) as c:
        # Clear the per-IP waitlist bucket in Redis so the sliding
        # window doesn't leak across test runs.
        try:
            import redis.asyncio as _redis_a
            r = _redis_a.from_url(os.environ.get("REDIS_URL", ""))
            await r.delete("aurem:rl:waitlist-ip:127.0.0.1")
            await r.aclose()
        except Exception:
            pass
        yield c


async def _cleanup(db, email: str):
    await db.promo_first50_waitlist.delete_many({"email": email.lower()})


async def test_1_valid_email_captured(client, db):
    email = f"wait-{uuid.uuid4().hex[:8]}@example.com"
    await _cleanup(db, email)
    r = await client.post(
        "/api/aurem-dev/promo/first50/waitlist",
        json={"email": email},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    row = await db.promo_first50_waitlist.find_one({"email": email})
    assert row is not None
    assert row["converted"] is False
    assert row["touch_count"] == 1

    await _cleanup(db, email)


async def test_2_idempotent_upsert(client, db):
    email = f"wait-{uuid.uuid4().hex[:8]}@example.com"
    await _cleanup(db, email)
    for _ in range(3):
        r = await client.post(
            "/api/aurem-dev/promo/first50/waitlist",
            json={"email": email},
        )
        assert r.status_code == 200
    rows = await db.promo_first50_waitlist.find({"email": email}).to_list(None)
    assert len(rows) == 1
    assert rows[0]["touch_count"] == 3

    await _cleanup(db, email)


async def test_3_invalid_email_rejected(client):
    r = await client.post(
        "/api/aurem-dev/promo/first50/waitlist",
        json={"email": "not-an-email"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"] == "invalid_email"


async def test_4_disposable_email_rejected(client):
    r = await client.post(
        "/api/aurem-dev/promo/first50/waitlist",
        json={"email": f"burn-{uuid.uuid4().hex[:6]}@mailinator.com"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"] == "disposable_email"


async def test_5_rate_limit_kicks_in(client):
    """5/min per IP. Fire 12 requests fast — expect at least 1 × 429."""
    codes = []
    for _ in range(12):
        email = f"burst-{uuid.uuid4().hex[:6]}@example.com"
        r = await client.post(
            "/api/aurem-dev/promo/first50/waitlist",
            json={"email": email},
        )
        codes.append(r.status_code)
    assert 429 in codes, (
        f"expected at least one 429 in 12 rapid requests, got {codes}"
    )
