"""R6 (overnight round) — second-teammate self-serve /ora PIN, live test.

Proves the EXISTING PersonalOraPinCard endpoints (`POST /ora-chat/pin/set`,
`GET /ora-chat/pin/status`) are genuinely per-user, not hardcoded to the
founder account — a SECOND, independent admin user can set + check
their OWN PIN without touching the first user's PIN/state, and can
then pin-login with their own identifier + PIN.
"""
import os
import time

import httpx
import pytest

BASE = "http://localhost:8001/api/aurem-dev"


@pytest.mark.asyncio
async def test_second_teammate_independent_personal_pin():
    from motor.motor_asyncio import AsyncIOMotorClient
    from cto_services.auth import create_token

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]

    teammate_email = f"teammate-r6-{int(time.time())}@aurem.dev"
    teammate_user_id = f"u_r6_teammate_{int(time.time())}"
    await db.dev_users.update_one(
        {"user_id": teammate_user_id},
        {"$set": {"user_id": teammate_user_id, "email": teammate_email,
                   "is_admin": True, "is_founder": False}},
        upsert=True,
    )
    token = create_token(user_id=teammate_user_id, email=teammate_email, is_admin=True)
    headers = {"Authorization": f"Bearer {token}"}
    teammate_pin = "teammatePin123"

    async with httpx.AsyncClient(timeout=10) as client:
        # Before setting a PIN, status must be false for THIS account.
        before = await client.get(f"{BASE}/ora-chat/pin/status", headers=headers)
        assert before.status_code == 200, before.text
        assert before.json()["pin_set"] is False

        set_r = await client.post(f"{BASE}/ora-chat/pin/set",
                                   json={"pin": teammate_pin}, headers=headers)
        assert set_r.status_code == 200, set_r.text

        after = await client.get(f"{BASE}/ora-chat/pin/status", headers=headers)
        assert after.json()["pin_set"] is True
        assert after.json()["email"] == teammate_email

        await db.ora_chat_pin_attempts.delete_many({"account_key": teammate_user_id})
        login_r = await client.post(
            f"{BASE}/ora-chat/pin-login",
            json={"pin": teammate_pin, "identifier": teammate_email},
            headers={"X-Forwarded-For": f"10.9.{int(time.time()) % 250}.5"},
        )
        assert login_r.status_code == 200, login_r.text
        assert login_r.json()["user"]["email"] == teammate_email

    # Cleanup — this is a throwaway test-only account.
    await db.dev_users.delete_one({"user_id": teammate_user_id})
