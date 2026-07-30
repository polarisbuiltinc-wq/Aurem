"""Iter 356 — Live backend checks (against preview URL) for:
  1. /chat/sessions filter — no prod-e2e-* leaks (with seeded Mongo debris).
  2. /admin/qa/cleanup-e2e-sessions — admin-gated + deletes seeded debris.
  3. /usage/public/stats — real_developers/commits_shipped present & sane.
"""
import os
import time
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

BACKEND = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND / ".env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://launch-pad-237.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api/aurem-dev"
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_login():
    r = requests.post(f"{API}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    if data.get("mfa_required"):
        pytest.skip("Admin has MFA enabled — skipping live token tests")
    assert data.get("token"), f"no token in {data}"
    assert data.get("user_id"), f"no user_id in {data}"
    return data


@pytest.fixture(scope="module")
def admin_token(admin_login):
    return admin_login["token"]


@pytest.fixture(scope="module")
def admin_user_id(admin_login):
    return admin_login["user_id"]


# ── Public stats (no auth) ────────────────────────────────────────────────

def test_public_stats_shape_and_test_account_exclusion():
    r = requests.get(f"{API}/usage/public/stats", timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("available") is True, data
    assert isinstance(data.get("real_developers"), int)
    assert isinstance(data.get("commits_shipped"), int)
    # test accounts should be excluded — real_developers should be small on preview
    assert data["real_developers"] >= 0
    # Sanity: on preview, we expect real_developers to be much smaller than
    # raw user count (~2 vs ~323). Just assert it's not absurd.
    assert data["real_developers"] < 100, \
        f"real_developers unexpectedly large: {data['real_developers']}"


# ── Cleanup endpoint auth ────────────────────────────────────────────────

def test_cleanup_endpoint_requires_auth():
    r = requests.post(f"{API}/admin/qa/cleanup-e2e-sessions", timeout=15)
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ── Session filter: seed debris then verify list excludes it ──────────────

@pytest.mark.asyncio
async def test_sessions_list_excludes_prod_e2e_after_seed(admin_token, admin_user_id):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    seeded = f"prod-e2e-live-{int(time.time())}"
    real = f"s-real-live-{int(time.time())}"
    project_id = "home"
    try:
        await db.chat_sessions.insert_many([
            {"session_id": seeded, "user_id": admin_user_id,
             "title": "E2E debris", "project_id": project_id,
             "updated_at": "2026-01-01T00:00:00Z", "turns": []},
            {"session_id": real, "user_id": admin_user_id,
             "title": "Real chat", "project_id": project_id,
             "updated_at": "2026-01-01T00:00:00Z", "turns": []},
        ])

        r = requests.get(
            f"{API}/chat/sessions",
            params={"project_id": project_id},
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # response can be {sessions: [...]} or a list — handle both
        sessions = body.get("sessions") if isinstance(body, dict) else body
        assert sessions is not None, body
        ids = [s.get("session_id") for s in sessions]
        assert seeded not in ids, f"prod-e2e leak still present! ids={ids[:20]}"
        # note: real seed may or may not appear depending on shape validators;
        # do NOT assert on it beyond absence of debris.
    finally:
        await db.chat_sessions.delete_many(
            {"session_id": {"$in": [seeded, real]}, "user_id": admin_user_id})
        client.close()


# ── Cleanup endpoint deletes seeded debris ────────────────────────────────

@pytest.mark.asyncio
async def test_cleanup_endpoint_deletes_seeded_debris(admin_token, admin_user_id):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    seeded_ids = [f"prod-e2e-cleanup-{i}-{int(time.time())}" for i in range(3)]
    try:
        await db.chat_sessions.insert_many([
            {"session_id": sid, "user_id": admin_user_id,
             "title": "seed", "project_id": "home",
             "updated_at": "2026-01-01T00:00:00Z", "turns": []}
            for sid in seeded_ids
        ])
        r = requests.post(
            f"{API}/admin/qa/cleanup-e2e-sessions",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert isinstance(body.get("deleted"), int)
        assert body["deleted"] >= 3, f"expected >=3 deletes, got {body}"

        # confirm gone
        remaining = await db.chat_sessions.count_documents(
            {"session_id": {"$in": seeded_ids}})
        assert remaining == 0
    finally:
        await db.chat_sessions.delete_many({"session_id": {"$in": seeded_ids}})
        client.close()
