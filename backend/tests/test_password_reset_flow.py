"""test_password_reset_flow.py — 2026-08-19

Self-service password rotation (forgot/reset/change-password), built
after a security audit found the app had NO rotation path at all for
a leaked credential. Runs against the real local MongoDB (MONGO_URL),
against a dedicated throwaway fixture account — never touches
test@aurem.dev or any documented credential.
"""
from __future__ import annotations

import os
import time
import uuid

import bcrypt
import pytest
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient

from main import app

pytestmark = pytest.mark.asyncio


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


@pytest.fixture
async def fixture_user():
    db = _db()
    email = f"pwreset-test-{uuid.uuid4().hex[:8]}@aurem.test"
    user_id = f"pwreset_{uuid.uuid4().hex[:8]}"
    password = "FixtureOriginal2026!"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    await db.dev_users.insert_one({
        "user_id": user_id, "email": email, "password": hashed,
        "name": "PW Reset Fixture", "tier": "free", "created_at": time.time(),
    })
    yield {"email": email, "user_id": user_id, "password": password}
    await db.dev_users.delete_one({"user_id": user_id})
    await db.password_reset_tokens.delete_many({"user_id": user_id})


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, email, password):
    return client.post(
        "/api/aurem-dev/auth/login", json={"email": email, "password": password},
    )


def _forgot(client, email):
    # Unique X-Forwarded-For per call so each test gets its own
    # rate-limit bucket (real requests share one bucket per real IP —
    # this is just to keep test-suite calls from tripping each other's
    # 3/min limit, per services/rate_limiter.client_ip_from_request).
    fake_ip = f"203.0.113.{uuid.uuid4().int % 250 + 1}"
    return client.post(
        "/api/aurem-dev/auth/forgot-password", json={"email": email},
        headers={"X-Forwarded-For": fake_ip},
    )


async def test_forgot_password_same_response_for_real_and_fake_email(client, fixture_user):
    r1 = _forgot(client, fixture_user["email"])
    r2 = _forgot(client, "definitely-not-a-real-user@aurem.test")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json(), "response must not leak account existence"


async def test_forgot_password_creates_usable_token(client, fixture_user):
    db = _db()
    r = _forgot(client, fixture_user["email"])
    assert r.status_code == 200
    row = await db.password_reset_tokens.find_one({"user_id": fixture_user["user_id"]})
    assert row is not None
    assert row["used"] is False
    assert row["expires_at"] > time.time()


async def test_reset_password_with_valid_token_changes_password(client, fixture_user):
    db = _db()
    c = client
    _forgot(c, fixture_user["email"])
    row = await db.password_reset_tokens.find_one({"user_id": fixture_user["user_id"]})
    r = c.post("/api/aurem-dev/auth/reset-password",
                json={"token": row["token"], "new_password": "FixtureNewPass2026!"})
    assert r.status_code == 200, r.text

    # old password now rejected, new one works
    assert _login(c, fixture_user["email"], fixture_user["password"]).status_code == 401
    assert _login(c, fixture_user["email"], "FixtureNewPass2026!").status_code == 200


async def test_reset_password_token_cannot_be_reused(client, fixture_user):
    db = _db()
    c = client
    _forgot(c, fixture_user["email"])
    row = await db.password_reset_tokens.find_one({"user_id": fixture_user["user_id"]})
    r1 = c.post("/api/aurem-dev/auth/reset-password",
                json={"token": row["token"], "new_password": "FixtureFirst2026!"})
    assert r1.status_code == 200
    r2 = c.post("/api/aurem-dev/auth/reset-password",
                json={"token": row["token"], "new_password": "FixtureSecond2026!"})
    assert r2.status_code == 400


async def test_reset_password_rejects_bogus_token(client):
    r = client.post("/api/aurem-dev/auth/reset-password",
                json={"token": "not-a-real-token", "new_password": "Whatever2026!"})
    assert r.status_code == 400


async def test_reset_password_rejects_expired_token(client, fixture_user):
    db = _db()
    c = client
    _forgot(c, fixture_user["email"])
    await db.password_reset_tokens.update_one(
        {"user_id": fixture_user["user_id"]}, {"$set": {"expires_at": time.time() - 10}},
    )
    row = await db.password_reset_tokens.find_one({"user_id": fixture_user["user_id"]})
    r = c.post("/api/aurem-dev/auth/reset-password",
                json={"token": row["token"], "new_password": "Whatever2026!"})
    assert r.status_code == 400


async def test_change_password_requires_correct_current_password(client, fixture_user):
    c = client
    login = _login(c, fixture_user["email"], fixture_user["password"])
    token = login.json()["token"]
    r = c.post("/api/aurem-dev/auth/change-password",
                headers={"Authorization": f"Bearer {token}"},
                json={"current_password": "WrongOne!", "new_password": "New2026!"})
    assert r.status_code == 401
    # original password still works
    assert _login(c, fixture_user["email"], fixture_user["password"]).status_code == 200


async def test_change_password_success_rotates_credential(client, fixture_user):
    c = client
    login = _login(c, fixture_user["email"], fixture_user["password"])
    token = login.json()["token"]
    r = c.post("/api/aurem-dev/auth/change-password",
                headers={"Authorization": f"Bearer {token}"},
                json={"current_password": fixture_user["password"],
                      "new_password": "RotatedFixture2026!"})
    assert r.status_code == 200
    assert _login(c, fixture_user["email"], fixture_user["password"]).status_code == 401
    assert _login(c, fixture_user["email"], "RotatedFixture2026!").status_code == 200


async def test_me_exposes_has_password_flag_never_raw_hash(client, fixture_user):
    c = client
    login = _login(c, fixture_user["email"], fixture_user["password"])
    token = login.json()["token"]
    r = c.get("/api/aurem-dev/auth/me", headers={"Authorization": f"Bearer {token}"})
    body = r.json()["user"]
    assert body["has_password"] is True
    assert "password" not in body


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
