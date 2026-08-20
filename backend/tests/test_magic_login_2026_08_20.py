"""Magic-login feature backend tests.

Covers POST /auth/magic-login/exchange and /auth/magic-login/refresh
plus DB side effects (used flag, onboarding_emails click count, expiry
semantics, replay protection). See review request iter (2026-08-20).
"""
import os
import secrets
import time
import uuid
from datetime import datetime, timezone

import pytest
import requests
from pymongo import MongoClient

BACKEND = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BACKEND}/api/aurem-dev"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest.fixture()
def test_user(db):
    """Create a bare-bones dev_users doc; clean up after."""
    uid = f"TEST_magiclogin_{uuid.uuid4().hex[:8]}"
    email = f"TEST_{uid}@aurem.dev"
    now = datetime.now(timezone.utc)
    db.dev_users.insert_one({
        "user_id": uid,
        "email": email,
        "name": "Magic Login Test",
        "tier": "free",
        "tokens_remaining": 10000,
        "is_admin": False,
        "password": None,
        "created_at": now,
    })
    yield {"user_id": uid, "email": email}
    db.dev_users.delete_many({"user_id": uid})
    db.magic_login_tokens.delete_many({"user_id": uid})
    db.onboarding_emails.delete_many({"user_id": uid})


def _mint_token(db, user, stage="stage1_github_started", *,
                expired=False, used=False):
    tok = secrets.token_urlsafe(32)
    now = time.time()
    if expired:
        created_at = now - 8 * 86400
        expires_at = now - 86400
    else:
        created_at = now
        expires_at = now + 7 * 86400
    db.magic_login_tokens.insert_one({
        "token": tok,
        "user_id": user["user_id"],
        "email": user["email"],
        "stage": stage,
        "created_at": created_at,
        "expires_at": expires_at,
        "used": used,
    })
    return tok


# --- Valid-token exchange ---------------------------------------------------

class TestExchangeValid:
    def test_stage1_exchange_success(self, db, test_user):
        tok = _mint_token(db, test_user, "stage1_github_started")
        r = requests.post(f"{API}/auth/magic-login/exchange",
                          json={"token": tok}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "token" in body and isinstance(body["token"], str) and len(body["token"]) > 20
        assert body.get("user_id") == test_user["user_id"]
        assert body.get("email") == test_user["email"]
        assert body.get("stage") == "stage1_github_started"
        # Row must be marked used
        row = db.magic_login_tokens.find_one({"token": tok})
        assert row and row.get("used") is True
        assert "used_at" in row

    def test_stage2_exchange_success(self, db, test_user):
        tok = _mint_token(db, test_user, "stage2_project_pending")
        r = requests.post(f"{API}/auth/magic-login/exchange",
                          json={"token": tok}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json().get("stage") == "stage2_project_pending"


# --- Replay / invalid / expired --------------------------------------------

class TestExchangeErrors:
    def test_replay_returns_410(self, db, test_user):
        tok = _mint_token(db, test_user)
        r1 = requests.post(f"{API}/auth/magic-login/exchange",
                           json={"token": tok}, timeout=15)
        assert r1.status_code == 200
        r2 = requests.post(f"{API}/auth/magic-login/exchange",
                           json={"token": tok}, timeout=15)
        assert r2.status_code == 410, r2.text

    def test_bogus_token_returns_404(self):
        r = requests.post(f"{API}/auth/magic-login/exchange",
                          json={"token": "not-a-real-token-xyz"}, timeout=15)
        assert r.status_code == 404, r.text

    def test_expired_unused_returns_410(self, db, test_user):
        tok = _mint_token(db, test_user, expired=True)
        r = requests.post(f"{API}/auth/magic-login/exchange",
                          json={"token": tok}, timeout=15)
        assert r.status_code == 410, r.text


# --- Refresh flow -----------------------------------------------------------

class TestRefresh:
    def test_refresh_expired_mints_and_consumes(self, db, test_user):
        old = _mint_token(db, test_user, "stage2_project_pending", expired=True)
        r = requests.post(f"{API}/auth/magic-login/refresh",
                          json={"token": old}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("stage") == "stage2_project_pending"
        assert body.get("user_id") == test_user["user_id"]
        # Old row: used=True
        old_row = db.magic_login_tokens.find_one({"token": old})
        assert old_row and old_row.get("used") is True
        # A new row should exist for the same user, also used=True
        others = list(db.magic_login_tokens.find(
            {"user_id": test_user["user_id"], "token": {"$ne": old}}))
        assert len(others) >= 1
        assert all(o.get("used") is True for o in others)

    def test_refresh_after_use_permanently_dead(self, db, test_user):
        old = _mint_token(db, test_user, expired=True)
        # First refresh: OK
        r1 = requests.post(f"{API}/auth/magic-login/refresh",
                           json={"token": old}, timeout=15)
        assert r1.status_code == 200
        # Second refresh w/ same old token: 410
        r2 = requests.post(f"{API}/auth/magic-login/refresh",
                           json={"token": old}, timeout=15)
        assert r2.status_code == 410, r2.text
        # And exchange on same old token: 410
        r3 = requests.post(f"{API}/auth/magic-login/exchange",
                           json={"token": old}, timeout=15)
        assert r3.status_code == 410, r3.text

    def test_refresh_bogus_returns_404(self):
        r = requests.post(f"{API}/auth/magic-login/refresh",
                          json={"token": "totally-fake"}, timeout=15)
        assert r.status_code == 404


# --- Click tracking regression ---------------------------------------------

class TestClickTracking:
    def test_exchange_increments_onboarding_email_click(self, db, test_user):
        # Seed a matching onboarding_emails doc, sent_ok=True, no click yet.
        stage = "stage1_github_started"
        db.onboarding_emails.insert_one({
            "user_id": test_user["user_id"],
            "campaign": "funnel_stage_nudge",
            "stage": stage,
            "sent_ok": True,
            "sent_at": datetime.now(timezone.utc),
            "click_count": 0,
        })
        tok = _mint_token(db, test_user, stage)
        r = requests.post(f"{API}/auth/magic-login/exchange",
                          json={"token": tok}, timeout=15)
        assert r.status_code == 200
        doc = db.onboarding_emails.find_one({
            "user_id": test_user["user_id"], "stage": stage,
        })
        assert doc.get("click_count") == 1
        assert doc.get("clicked_at") is not None


# --- Dry-run regression on funnel_nudge_cron.magic_click_url ---------------

class TestDryRunNudge:
    def test_preview_does_not_mint_token(self, db, test_user):
        """preview=True should return placeholder URL, no DB write."""
        import asyncio
        import sys
        sys.path.insert(0, "/app/backend")
        from services.funnel_nudge_cron import magic_click_url
        from motor.motor_asyncio import AsyncIOMotorClient

        async def _run():
            c = AsyncIOMotorClient(MONGO_URL)
            adb = c[DB_NAME]
            before = await adb.magic_login_tokens.count_documents({"user_id": test_user["user_id"]})
            url = await magic_click_url(adb, test_user["user_id"], test_user["email"],
                                        "stage1_github_started", preview=True)
            after = await adb.magic_login_tokens.count_documents({"user_id": test_user["user_id"]})
            c.close()
            return url, before, after

        url, before, after = asyncio.run(_run())
        assert "preview-not-a-real-token" in url
        assert before == after, f"preview minted a token! before={before} after={after}"
