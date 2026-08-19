"""test_chatux4_history_steps_api.py — Iter Chat-UX-4 Tier1 API test.

Verifies the HTTP contract: after we seed an assistant turn in Mongo
with a `steps` array, GET /api/aurem-dev/chat/history?session_id=...
returns each turn dict verbatim, including the `steps` field. This is
the exact wire shape the frontend hydration mapper depends on.
"""
from __future__ import annotations

import os
import uuid
import asyncio

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or \
    "https://launch-pad-237.preview.emergentagent.com"
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


def _login() -> str | None:
    """Return an auth token for the test user, or None if login flow
    is not accessible from this test runner."""
    # Try the aurem-dev login route
    for path in ("/api/aurem-dev/auth/login", "/api/auth/login"):
        try:
            r = requests.post(f"{BASE_URL}{path}",
                              json={"email": EMAIL, "password": PASSWORD},
                              timeout=15)
            if r.status_code == 200:
                j = r.json()
                tok = j.get("token") or j.get("access_token") or \
                    (j.get("user") or {}).get("token")
                if tok:
                    return tok
        except Exception:
            continue
    return None


def _seed_turn_with_steps(user_id: str, session_id: str, steps: list) -> None:
    async def _run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.chat_sessions.update_one(
            {"session_id": session_id, "user_id": user_id},
            {"$set": {"session_id": session_id, "user_id": user_id,
                      "title": "TEST_ux4"},
             "$push": {"turns": {"$each": [
                 {"role": "user", "content": "seed prompt"},
                 {"role": "assistant", "content": "seed reply",
                  "provider": "test-provider", "steps": steps},
             ]}}},
            upsert=True,
        )
        client.close()
    asyncio.run(_run())


def _cleanup(user_id: str, session_id: str) -> None:
    async def _run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.chat_sessions.delete_one(
            {"session_id": session_id, "user_id": user_id})
        client.close()
    asyncio.run(_run())


def test_history_returns_steps_field_on_assistant_turn():
    if not MONGO_URL:
        pytest.skip("no Mongo")
    token = _login()
    if not token:
        pytest.skip("could not obtain auth token for test@aurem.dev")

    # Resolve the user_id for test@aurem.dev via /me
    r = requests.get(f"{BASE_URL}/api/aurem-dev/auth/me",
                     headers={"Authorization": f"Bearer {token}"},
                     timeout=15)
    if r.status_code != 200:
        pytest.skip(f"/auth/me returned {r.status_code}: {r.text[:200]}")
    me = r.json()
    user_id = me.get("user_id") or me.get("id") or (me.get("user") or {}).get("user_id")
    assert user_id, f"no user_id in /me response: {me}"

    session_id = f"TEST_ux4_{uuid.uuid4().hex[:12]}"
    steps = [
        {"text": "Reading repo...", "done": True},
        {"text": "Writing files...", "done": True},
        {"text": "Committing...", "done": True},
    ]
    _seed_turn_with_steps(user_id, session_id, steps)
    try:
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/chat/history",
            params={"session_id": session_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        assert j.get("ok") is True
        msgs = j.get("messages") or []
        # find the assistant turn
        asst = [m for m in msgs if m.get("role") == "assistant"]
        assert len(asst) >= 1, f"no assistant turn returned: {msgs}"
        got_steps = asst[-1].get("steps")
        assert isinstance(got_steps, list), \
            f"steps missing/not list on assistant turn: {asst[-1]}"
        assert len(got_steps) == 3
        assert got_steps[0]["text"].startswith("Reading repo")
        assert all(s.get("done") is True for s in got_steps)
    finally:
        _cleanup(user_id, session_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
