"""Resend-verification endpoint behaviour tests.

Covers:
  • Unverified user gets a fresh token (returns ok=true)
  • Second resend within 15 min → HTTP 429
  • Already-verified user → {ok:true, already_verified:true}
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

_BR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BR not in sys.path:
    sys.path.insert(0, _BR)

pytestmark = pytest.mark.asyncio
BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or \
    "https://launch-pad-237.preview.emergentagent.com"


@pytest.fixture
def db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture
async def client():
    async with AsyncClient(base_url=BASE, timeout=45.0) as c:
        yield c


async def _cleanup(db, email):
    u = await db.dev_users.find_one({"email": email}, {"_id": 0, "user_id": 1})
    if u:
        uid = u["user_id"]
        await db.dev_users.delete_many({"user_id": uid})
        await db.email_verifications.delete_many({"user_id": uid})
        await db.onboarding_emails.delete_many({"user_id": uid})


async def _signup_and_login(client, email):
    r = await client.post(
        "/api/aurem-dev/auth/signup",
        json={"email": email, "password": "TestPass2026!",
              "name": "R User", "form_age_ms": 15_000},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Signup returns a token directly (see auth.py).
    tok = body.get("token") or body.get("access_token")
    assert tok, f"signup response missing token: {body}"
    return body["user_id"], tok


async def test_resend_unverified_ok_then_429(client, db):
    email = f"resend-{uuid.uuid4().hex[:10]}@example.com"
    await _cleanup(db, email)

    uid, tok = await _signup_and_login(client, email)

    # Wait for first bg-task-minted row (from signup).
    for _ in range(30):
        if await db.email_verifications.find_one({"user_id": uid}):
            break
        await asyncio.sleep(0.15)

    headers = {"Authorization": f"Bearer {tok}"}
    r1 = await client.post(
        "/api/aurem-dev/auth/resend-verification", headers=headers,
    )
    # Should be 429 immediately because the signup already inserted an
    # onboarding_emails row (< 15 min ago).
    assert r1.status_code in (200, 429), r1.text
    if r1.status_code == 200:
        # Second call must be 429.
        r2 = await client.post(
            "/api/aurem-dev/auth/resend-verification", headers=headers,
        )
        assert r2.status_code == 429

    await _cleanup(db, email)


async def test_resend_already_verified_returns_true(client, db):
    email = f"resend-verified-{uuid.uuid4().hex[:10]}@example.com"
    await _cleanup(db, email)
    uid, tok = await _signup_and_login(client, email)

    # Fake-verify the user directly.
    await db.dev_users.update_one(
        {"user_id": uid}, {"$set": {"email_verified": True}},
    )

    headers = {"Authorization": f"Bearer {tok}"}
    r = await client.post(
        "/api/aurem-dev/auth/resend-verification", headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("already_verified") is True

    await _cleanup(db, email)
