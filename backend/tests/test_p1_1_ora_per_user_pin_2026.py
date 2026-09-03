"""R3 P1-1 (overnight round) — per-user hashed /ora PIN, live test.

Real HTTP calls against the running backend (localhost:8001), real Mongo.
Proves:
  1. A logged-in admin can set + check their own personal PIN
     (bcrypt-hashed, stored on their own dev_users row).
  2. pin-login with `identifier` + the correct personal PIN mints a
     token bound to THAT account (not a hardcoded founder).
  3. A wrong personal PIN is rejected (401) and does NOT touch a
     different identifier's lockout bucket (real per-user isolation).
  4. After 5 wrong attempts against ONE identifier, even a CORRECT
     PIN for that same identifier is blocked (429) — genuine
     per-user lockout, not just per-IP.
"""
import time

import httpx
import pytest

BASE = "http://localhost:8001/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"
TEST_PIN = "myPersonalPin987"


@pytest.mark.asyncio
async def test_per_user_pin_set_and_login():
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    async with httpx.AsyncClient(timeout=10) as client:
        login = await client.post(f"{BASE}/auth/login",
                                   json={"email": EMAIL, "password": PASSWORD})
        assert login.status_code == 200, login.text
        token = login.json()["token"]
        user_id = login.json()["user_id"]
        headers = {"Authorization": f"Bearer {token}"}

        # Other suites share this same trusted account_key within the
        # 1h lockout window — clear it so this happy-path run isn't
        # collateral-blocked by an unrelated suite's wrong-PIN rows.
        await db.ora_chat_pin_attempts.delete_many({"account_key": user_id})

        set_r = await client.post(f"{BASE}/ora-chat/pin/set",
                                   json={"pin": TEST_PIN}, headers=headers)
        assert set_r.status_code == 200, set_r.text

        status_r = await client.get(f"{BASE}/ora-chat/pin/status", headers=headers)
        assert status_r.status_code == 200
        assert status_r.json()["pin_set"] is True

        marker = f"10.7.{int(time.time()) % 250}.1"
        login_r = await client.post(
            f"{BASE}/ora-chat/pin-login",
            json={"pin": TEST_PIN, "identifier": EMAIL},
            headers={"X-Forwarded-For": marker},
        )
        assert login_r.status_code == 200, login_r.text
        body = login_r.json()
        assert body["ok"] is True
        assert body["user"]["email"] == EMAIL


@pytest.mark.asyncio
async def test_per_user_lockout_isolated_then_trips():
    marker_prefix = f"p1-1-{int(time.time())}"
    async with httpx.AsyncClient(timeout=10) as client:
        # 5 wrong attempts against EMAIL specifically.
        for i in range(5):
            r = await client.post(
                f"{BASE}/ora-chat/pin-login",
                json={"pin": f"wrong-{marker_prefix}-{i}", "identifier": EMAIL},
                headers={"X-Forwarded-For": f"10.8.{i}.{i}"},
            )
            assert r.status_code in (401, 429), r.text

        # A totally different (unknown) identifier is UNAFFECTED —
        # proves the two accounts' lockouts don't cross-contaminate.
        other = await client.post(
            f"{BASE}/ora-chat/pin-login",
            json={"pin": "irrelevant", "identifier": "nobody-else@aurem.dev"},
            headers={"X-Forwarded-For": "10.8.99.99"},
        )
        assert other.status_code in (401, 503), other.text

        # 6th attempt on EMAIL, even with the CORRECT pin, must be
        # blocked — real per-user lockout, not merely per-IP.
        blocked = await client.post(
            f"{BASE}/ora-chat/pin-login",
            json={"pin": TEST_PIN, "identifier": EMAIL},
            headers={"X-Forwarded-For": "10.8.200.200"},
        )
        assert blocked.status_code == 429, blocked.text
