"""
test_promo_first50.py — 2026-08-20

Real, no-mocks pytest for routers/promo_first50.py — the code that
decides who gets a free 30-day Pro tier (Track 3 item #31). Flagged
during the engineering-discipline audit as the one revenue/access
-controlling router with zero test coverage; this closes that gap.

Hits the live backend on localhost:8001 over real HTTP, uses a direct
Motor client for setup/cleanup (same convention as
test_github_funnel_telemetry.py). Every row created here is prefixed
with a unique uuid and cleaned up in a fixture teardown.

Covers:
  1. GET /promo/first50/status reflects real singleton state
  2. A valid unexpired token: verifies the user, claims a spot,
     upgrades tier to pro with a ~30-day pro_expires_at
  3. Promo already full (spots_claimed >= total): verification still
     succeeds but NO tier upgrade happens
  4. Invalid/expired token → error redirect, no state change
  5. Re-clicking an already-used token is idempotent (no double spot
     claim, no double counter increment)
  6. downgrade_expired_promos() downgrades an expired promo-Pro user
     to free
  7. downgrade_expired_promos() does NOT touch a user with
     stripe_subscription_active=True — the exact "don't touch a real
     paying customer" guarantee the founder cares about
  8. downgrade_expired_promos() does NOT touch a founder/admin
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

API = "http://localhost:8001/api/aurem-dev"


def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def _now():
    return datetime.now(timezone.utc)


async def _make_user(db, **overrides) -> dict:
    uid = f"test_promo_{uuid.uuid4().hex[:10]}"
    doc = {
        "user_id":       uid,
        "email":         f"{uid}@aurem.test",
        "name":          "Promo Test User",
        "tier":          "free",
        "is_admin":      False,
        "email_verified": False,
        "created_at":    _now(),
    }
    doc.update(overrides)
    await db.dev_users.insert_one(doc)
    return doc


async def _make_token(db, user_id: str, *, expired: bool = False,
                       used: bool = False) -> str:
    token = uuid.uuid4().hex
    now = _now()
    await db.email_verifications.insert_one({
        "token":          token,
        "user_id":        user_id,
        "email":          f"{user_id}@aurem.test",
        "created_at":     now,
        "expires_at":     now - timedelta(hours=1) if expired else now + timedelta(hours=24),
        "used_at":        now if used else None,
        "invalidated_at": None,
    })
    return token


@pytest.fixture
async def cleanup_ids():
    """Collect user_ids/tokens created during a test; wipe them (and
    any promo_first50_state increment they caused) on teardown."""
    ids: dict = {"user_ids": [], "tokens": [], "spots_to_release": 0}
    yield ids

    db = _db()
    if ids["user_ids"]:
        await db.dev_users.delete_many({"user_id": {"$in": ids["user_ids"]}})
    if ids["tokens"]:
        await db.email_verifications.delete_many({"token": {"$in": ids["tokens"]}})
    if ids["spots_to_release"]:
        await db.promo_first50_state.update_one(
            {"_id": "global", "spots_claimed": {"$gte": ids["spots_to_release"]}},
            {"$inc": {"spots_claimed": -ids["spots_to_release"]}},
        )


@pytest.mark.asyncio
async def test_status_reflects_real_singleton_state():
    db = _db()
    doc = await db.promo_first50_state.find_one({"_id": "global"})
    r = httpx.get(f"{API}/promo/first50/status", timeout=10)
    assert r.status_code == 200
    body = r.json()
    if doc:
        assert body["claimed"] == doc.get("spots_claimed", 0)
        assert body["total"] == doc.get("total", 50)
    assert body["remaining"] == max(0, body["total"] - body["claimed"])


@pytest.mark.asyncio
async def test_valid_token_verifies_and_claims_pro(cleanup_ids):
    db = _db()
    # Only run the "spot claimed" assertion if there's real room —
    # otherwise (promo sold out in this env) just assert verification
    # itself still works, matching the code's own "promo_full" branch.
    status = await db.promo_first50_state.find_one({"_id": "global"})
    room = not status or status.get("spots_claimed", 0) < status.get("total", 50)

    user = await _make_user(db)
    cleanup_ids["user_ids"].append(user["user_id"])
    token = await _make_token(db, user["user_id"])
    cleanup_ids["tokens"].append(token)

    r = httpx.get(f"{API}/auth/verify", params={"token": token},
                  timeout=10, follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    assert "status=ok" in location

    fresh = await db.dev_users.find_one({"user_id": user["user_id"]})
    assert fresh["email_verified"] is True

    if room:
        assert "claimed=1" in location
        cleanup_ids["spots_to_release"] += 1
        assert fresh["tier"] == "pro"
        assert fresh["promo_first50_claimed"] is True
        assert fresh["pro_expires_at"] is not None
        expires = fresh["pro_expires_at"]
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        days_out = (expires - _now()).days
        assert 28 <= days_out <= 30
    else:
        assert "claimed=1" not in location
        assert fresh["tier"] == "free"


@pytest.mark.asyncio
async def test_promo_full_verifies_but_does_not_upgrade_tier(cleanup_ids):
    """Directly force spots_claimed == total so we deterministically
    hit the sold-out branch regardless of the real env's live count."""
    db = _db()
    await db.promo_first50_state.update_one(
        {"_id": "global"}, {"$setOnInsert": {"is_active": True, "created_at": _now()}},
        upsert=True,
    )
    real = await db.promo_first50_state.find_one({"_id": "global"})
    total = real.get("total", 50)
    # Bump spots_claimed to exactly `total` for the duration of this test.
    await db.promo_first50_state.update_one(
        {"_id": "global"}, {"$set": {"spots_claimed": total}},
    )
    try:
        user = await _make_user(db)
        cleanup_ids["user_ids"].append(user["user_id"])
        token = await _make_token(db, user["user_id"])
        cleanup_ids["tokens"].append(token)

        r = httpx.get(f"{API}/auth/verify", params={"token": token},
                      timeout=10, follow_redirects=False)
        assert r.status_code == 302
        assert "reason=promo_full" in r.headers["location"]

        fresh = await db.dev_users.find_one({"user_id": user["user_id"]})
        assert fresh["email_verified"] is True
        assert fresh["tier"] == "free"
        assert not fresh.get("promo_first50_claimed")
    finally:
        # Restore the real counter exactly (we only changed spots_claimed).
        await db.promo_first50_state.update_one(
            {"_id": "global"}, {"$set": {"spots_claimed": real.get("spots_claimed", 0)}},
        )


@pytest.mark.asyncio
async def test_expired_token_redirects_error_no_state_change(cleanup_ids):
    db = _db()
    user = await _make_user(db)
    cleanup_ids["user_ids"].append(user["user_id"])
    token = await _make_token(db, user["user_id"], expired=True)
    cleanup_ids["tokens"].append(token)

    r = httpx.get(f"{API}/auth/verify", params={"token": token},
                  timeout=10, follow_redirects=False)
    assert r.status_code == 302
    assert "status=error" in r.headers["location"]
    assert "expired_token" in r.headers["location"]

    fresh = await db.dev_users.find_one({"user_id": user["user_id"]})
    assert fresh["email_verified"] is False
    assert fresh["tier"] == "free"


@pytest.mark.asyncio
async def test_reclicking_used_token_is_idempotent_no_double_claim(cleanup_ids):
    db = _db()
    user = await _make_user(db, email_verified=True, tier="pro",
                             promo_first50_claimed=True,
                             promo_first50_claimed_at=_now(),
                             pro_expires_at=_now() + timedelta(days=30))
    cleanup_ids["user_ids"].append(user["user_id"])
    # A used token pointing at an already-verified, already-claimed user.
    token = await _make_token(db, user["user_id"], used=True)
    cleanup_ids["tokens"].append(token)

    before = await db.promo_first50_state.find_one({"_id": "global"})
    before_count = before.get("spots_claimed", 0) if before else 0

    r = httpx.get(f"{API}/auth/verify", params={"token": token},
                  timeout=10, follow_redirects=False)
    assert r.status_code == 302
    # find_one_and_update on a used token finds nothing → falls into
    # the "already used" branch.
    assert "status=ok" in r.headers["location"]
    assert "already_verified" in r.headers["location"]

    after = await db.promo_first50_state.find_one({"_id": "global"})
    after_count = after.get("spots_claimed", 0) if after else 0
    assert after_count == before_count, (
        "re-clicking a used token must never increment the promo counter"
    )


@pytest.mark.asyncio
async def test_downgrade_expired_promo_user_to_free(cleanup_ids):
    from routers.promo_first50 import downgrade_expired_promos
    db = _db()
    user = await _make_user(
        db, tier="pro", promo_first50_claimed=True,
        pro_expires_at=_now() - timedelta(days=1),
    )
    cleanup_ids["user_ids"].append(user["user_id"])

    result = await downgrade_expired_promos(db)
    assert result["downgraded"] >= 1

    fresh = await db.dev_users.find_one({"user_id": user["user_id"]})
    assert fresh["tier"] == "free"
    assert fresh.get("promo_downgraded_at") is not None


@pytest.mark.asyncio
async def test_downgrade_never_touches_real_stripe_subscriber(cleanup_ids):
    """The exact guarantee that matters: a real paying customer who
    also happens to carry an expired promo flag must NEVER be
    auto-downgraded."""
    from routers.promo_first50 import downgrade_expired_promos
    db = _db()
    user = await _make_user(
        db, tier="pro", promo_first50_claimed=True,
        pro_expires_at=_now() - timedelta(days=1),
        stripe_subscription_active=True,
    )
    cleanup_ids["user_ids"].append(user["user_id"])

    await downgrade_expired_promos(db)

    fresh = await db.dev_users.find_one({"user_id": user["user_id"]})
    assert fresh["tier"] == "pro", "a real Stripe subscriber must never be auto-downgraded"


@pytest.mark.asyncio
async def test_downgrade_never_touches_founder_admin(cleanup_ids):
    from routers.promo_first50 import downgrade_expired_promos
    db = _db()
    user = await _make_user(
        db, tier="pro", promo_first50_claimed=True,
        pro_expires_at=_now() - timedelta(days=1),
        is_admin=True,
    )
    cleanup_ids["user_ids"].append(user["user_id"])

    await downgrade_expired_promos(db)

    fresh = await db.dev_users.find_one({"user_id": user["user_id"]})
    assert fresh["tier"] == "pro", "a founder/admin must never be auto-downgraded"


@pytest.mark.asyncio
async def test_waitlist_accepts_valid_email_rejects_disposable():
    email = f"promo-waitlist-{uuid.uuid4().hex[:8]}@aurem.test"
    try:
        r = httpx.post(f"{API}/promo/first50/waitlist",
                        json={"email": email}, timeout=10)
        assert r.status_code == 200
        assert r.json()["ok"] is True

        r2 = httpx.post(f"{API}/promo/first50/waitlist",
                         json={"email": "someone@mailinator.com"}, timeout=10)
        assert r2.status_code == 400
    finally:
        db = _db()
        await db.promo_first50_waitlist.delete_one({"email": email})
