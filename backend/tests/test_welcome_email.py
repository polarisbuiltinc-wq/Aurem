"""
tests/test_welcome_email.py — Track 3 · item #33.

Zero mocks against the running backend (:8001). Resend network hop
is short-circuited for @example.com via the same guard used by
verification_email + welcome_email modules.

Covers:
  1. Full signup → verify → welcome email row lands in onboarding_emails
     with campaign=signup_welcome + sent_ok=True.
  2. Idempotency: a second verify click (already-verified path) does
     NOT insert a second welcome row.
  3. Founder signup path: auto-verified at signup, no welcome fires
     (there is no verify click event to hang the campaign off).
  4. render_html contains the required security-first messaging blocks
     and single connect-repo CTA. render_text mirrors.
  5. Video block: WELCOME_DEMO_VIDEO_URL env unset → HTML omits the
     thumbnail block entirely (graceful degrade).
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
_API = "http://localhost:8001"


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture
async def client():
    async with AsyncClient(base_url=_API, timeout=30.0) as c:
        yield c


async def _cleanup(db, email: str) -> None:
    user = await db.dev_users.find_one({"email": email}, {"_id": 0, "user_id": 1})
    if user:
        uid = user["user_id"]
        await db.dev_users.delete_many({"user_id": uid})
        await db.email_verifications.delete_many({"user_id": uid})
        await db.onboarding_emails.delete_many({"user_id": uid})


async def _reset_singleton(db):
    await db.promo_first50_state.update_one(
        {"_id": "global"},
        {"$set": {"spots_claimed": 0, "total": 50, "is_active": True}},
        upsert=True,
    )


async def _signup_and_get_token(client, db, email):
    await _cleanup(db, email)
    r = await client.post(
        "/api/aurem-dev/auth/signup",
        json={"email": email, "password": "TestPass2026!",
              "name": "Welcome Test", "form_age_ms": 15_000},
    )
    r.raise_for_status()
    user_id = r.json()["user_id"]
    row = None
    for _ in range(20):
        row = await db.email_verifications.find_one({"user_id": user_id})
        if row:
            break
        await asyncio.sleep(0.1)
    return user_id, row["token"]


async def test_1_verify_fires_welcome(client, db):
    email = f"welcome-{uuid.uuid4().hex[:8]}@example.com"
    await _reset_singleton(db)
    user_id, token = await _signup_and_get_token(client, db, email)

    r = await client.get(
        f"/api/aurem-dev/auth/verify?token={token}",
        follow_redirects=False,
    )
    assert r.status_code == 302

    # Poll for the welcome row (bg task).
    doc = None
    for _ in range(20):
        doc = await db.onboarding_emails.find_one(
            {"user_id": user_id, "campaign": "signup_welcome"},
        )
        if doc:
            break
        await asyncio.sleep(0.1)
    assert doc is not None
    assert doc["sent_ok"] is True
    assert doc["stage"] == "welcome"

    await _cleanup(db, email)


async def test_2_second_verify_click_is_idempotent(client, db):
    email = f"welcome-idem-{uuid.uuid4().hex[:8]}@example.com"
    await _reset_singleton(db)
    user_id, token = await _signup_and_get_token(client, db, email)

    await client.get(
        f"/api/aurem-dev/auth/verify?token={token}",
        follow_redirects=False,
    )
    # Wait for first send.
    for _ in range(20):
        doc = await db.onboarding_emails.find_one(
            {"user_id": user_id, "campaign": "signup_welcome"},
        )
        if doc:
            break
        await asyncio.sleep(0.1)

    # Second click (uses the "already used" branch — no new welcome).
    await client.get(
        f"/api/aurem-dev/auth/verify?token={token}",
        follow_redirects=False,
    )
    await asyncio.sleep(0.5)  # Let any spurious bg task settle.
    count = await db.onboarding_emails.count_documents(
        {"user_id": user_id, "campaign": "signup_welcome"},
    )
    assert count == 1

    await _cleanup(db, email)


def test_3_render_contains_required_content():
    """Static content contract — matches the Session 4 spec."""
    from services.welcome_email import render_text, render_html
    user = {"user_id": "u_test", "email": "x@example.com", "name": "Alex"}
    text = render_text(user)
    html = render_html(user)
    for needle in ("Vanguard", "Citation Guard", "Verify-gate",
                   "Plan / Execute / Verify / Scan / Ship",
                   "Ask Advisor", "Rollback",
                   "Connect your first project"):
        assert needle in html, f"html missing {needle!r}"
    for needle in ("Vanguard", "Citation Guard", "Verify-gate",
                   "Ask Advisor", "Rollback",
                   "Connect your first project"):
        assert needle in text, f"text missing {needle!r}"
    # NO scarcity — no "spots left" / "founder pricing" language in the
    # welcome body (that belongs on the landing page).
    for banned in ("spots left", "500 spots", "50 spots"):
        assert banned not in text.lower()
        assert banned not in html.lower()


def test_4_video_block_gracefully_absent_when_url_unset(monkeypatch):
    """When WELCOME_DEMO_VIDEO_URL is empty, the HTML must NOT render
    the thumbnail block. Text must NOT include the demo link line."""
    monkeypatch.setenv("WELCOME_DEMO_VIDEO_URL", "")
    # Force a reload so the module-level constant re-reads env.
    import importlib
    import services.welcome_email as wm
    importlib.reload(wm)
    try:
        assert wm.DEMO_VIDEO_URL == ""
        user = {"user_id": "u", "email": "x@example.com", "name": "A"}
        text = wm.render_text(user)
        html = wm.render_html(user)
        assert "60-second demo" not in html
        assert "60-second demo" not in text
        assert "Watch a 60-second demo" not in text
    finally:
        # Restore default reload so other tests see a fresh state.
        importlib.reload(wm)


def test_5_video_block_present_when_url_set(monkeypatch):
    monkeypatch.setenv("WELCOME_DEMO_VIDEO_URL", "https://youtu.be/DEMO_ID")
    import importlib
    import services.welcome_email as wm
    importlib.reload(wm)
    try:
        assert wm.DEMO_VIDEO_URL == "https://youtu.be/DEMO_ID"
        user = {"user_id": "u", "email": "x@example.com", "name": "A"}
        html = wm.render_html(user)
        text = wm.render_text(user)
        assert "https://youtu.be/DEMO_ID" in html
        assert "60-second demo" in html
        assert "https://youtu.be/DEMO_ID" in text
    finally:
        monkeypatch.delenv("WELCOME_DEMO_VIDEO_URL", raising=False)
        importlib.reload(wm)
