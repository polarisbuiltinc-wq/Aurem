"""
Public-URL integration test for Track 3 First-50 promo.
Hits the preview REACT_APP_BACKEND_URL (behind the k8s ingress /api
proxy) instead of localhost, exercising the full request path.

Runs against the *running* backend where PROMO_FIRST50_TOTAL is the
default (50). We manipulate the singleton via direct Mongo write to
speed up the 51st-user cap test.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

pytestmark = pytest.mark.asyncio

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or \
    "https://launch-pad-237.preview.emergentagent.com"


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture
async def app_client():
    async with AsyncClient(base_url=BASE, timeout=45.0) as c:
        yield c


async def _reset(db, total=50):
    await db.promo_first50_state.update_one(
        {"_id": "global"},
        {"$set": {"spots_claimed": 0, "total": total, "is_active": True}},
        upsert=True,
    )


async def _cleanup_test_users(db):
    users = db.dev_users.find(
        {"email": {"$regex": r"^promo51-", "$options": "i"}},
        {"_id": 0, "user_id": 1},
    )
    async for u in users:
        uid = u["user_id"]
        await db.dev_users.delete_many({"user_id": uid})
        await db.email_verifications.delete_many({"user_id": uid})
        await db.onboarding_emails.delete_many({"user_id": uid})


async def test_status_endpoint_public(app_client, db):
    """Public URL returns the counter shape."""
    await _reset(db, total=50)
    r = await app_client.get("/api/aurem-dev/promo/first50/status")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 50
    assert body["claimed"] == 0
    assert body["remaining"] == 50
    assert body["is_active"] is True


async def test_status_reflects_claim_increment(app_client, db):
    await _reset(db, total=50)
    await db.promo_first50_state.update_one(
        {"_id": "global"}, {"$inc": {"spots_claimed": 1}},
    )
    body = (await app_client.get("/api/aurem-dev/promo/first50/status")).json()
    assert body["claimed"] == 1
    assert body["remaining"] == 49
    await _reset(db, total=50)


async def test_invalid_token_redirects(app_client):
    r = await app_client.get(
        "/api/aurem-dev/auth/verify?token=totally-bogus-nonexistent-xyz",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "reason=invalid_token" in r.headers["location"]
    assert "/verify?" in r.headers["location"]


async def test_missing_token_redirects(app_client):
    r = await app_client.get(
        "/api/aurem-dev/auth/verify",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "missing_token" in r.headers["location"]


async def test_51st_user_hits_promo_full(app_client, db):
    """Cap held: 50 users get claimed=1, 51st gets promo_full.

    Optimisation — we drop the cap to 3 via direct Mongo write and
    exercise 4 users. The atomic $expr claim in the router uses the
    stored `total` field, so this is a faithful replication of the
    50→51 scenario without 51 real signups."""
    await _cleanup_test_users(db)
    await _reset(db, total=3)

    emails = [f"promo51-{uuid.uuid4().hex[:10]}@example.com" for _ in range(4)]
    user_tokens = []

    for e in emails:
        r = await app_client.post(
            "/api/aurem-dev/auth/signup",
            json={
                "email": e, "password": "TestPass2026!",
                "name": "Cap Test", "form_age_ms": 15_000,
            },
        )
        assert r.status_code == 200, r.text
        uid = r.json()["user_id"]
        # Wait for the background task to mint the token row.
        row = None
        for _ in range(30):
            row = await db.email_verifications.find_one(
                {"user_id": uid, "used_at": None},
            )
            if row:
                break
            await asyncio.sleep(0.15)
        assert row is not None, f"no token for {uid}"
        user_tokens.append((uid, row["token"]))

    results = []
    for uid, tok in user_tokens:
        r = await app_client.get(
            f"/api/aurem-dev/auth/verify?token={tok}",
            follow_redirects=False,
        )
        assert r.status_code == 302
        results.append(r.headers["location"])

    # 1st, 2nd, 3rd claim; 4th (= the "51st") promo_full.
    assert "claimed=1" in results[0]
    assert "claimed=1" in results[1]
    assert "claimed=1" in results[2]
    assert "claimed=1" not in results[3]
    assert "promo_full" in results[3]

    state = await db.promo_first50_state.find_one({"_id": "global"})
    assert state["spots_claimed"] == 3

    fourth = await db.dev_users.find_one({"user_id": user_tokens[3][0]})
    assert fourth["email_verified"] is True
    assert fourth.get("promo_first50_claimed") is False

    # Clean up + restore singleton to production default.
    await _cleanup_test_users(db)
    await _reset(db, total=50)
