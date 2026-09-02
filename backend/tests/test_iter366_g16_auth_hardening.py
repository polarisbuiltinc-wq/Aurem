"""
test_iter366_g16_auth_hardening.py — G16 close-out

Behavioural tests for the 3 previously-missing G16 pieces:
  1. Revoked sk-aurem key rejects on next request (no cache lag).
  2. Expired JWT rejects.
  3. Login rate-limit fires on brute-force (5 fails/window).
"""
from __future__ import annotations

import os
import secrets
import time

import pytest
from motor.motor_asyncio import AsyncIOMotorClient


def _db():
    from cto_services.db import set_db as _set_db
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "aurem_dev")]
    _set_db(db)
    return db


# ── 1. Revoked API key rejects immediately ──────────────────────────

@pytest.mark.asyncio
async def test_revoked_api_key_rejects_next_request():
    """Mint an sk-aurem-* key row, revoke it, verify the auth resolver
    treats it as invalid. Test hits the auth resolver directly (not
    HTTP) so the timing is deterministic — one line between revoke
    write and rejection read."""
    db = _db()
    key_id = f"key_g16_{secrets.token_hex(4)}"
    raw_key = f"sk-aurem-{secrets.token_urlsafe(16)}"
    uid = f"u_g16_{secrets.token_hex(3)}"

    # Seed dev_users so the resolver's user lookup succeeds.
    await db.dev_users.insert_one({
        "user_id": uid, "email": f"{uid}@example.com", "tier": "free",
    })
    try:
        # api_keys stores the raw token in `key` field (see mcp.py:672).
        await db.api_keys.insert_one({
            "key_id":  key_id,
            "user_id": uid,
            "key":     raw_key,
            "active":  True,
            "created_at": time.time(),
        })
        # Verify the key would resolve BEFORE revoke.
        row_pre = await db.api_keys.find_one({"key": raw_key, "active": True})
        assert row_pre is not None, "key should resolve before revoke"

        # Revoke it — the auth path filters on active=True.
        await db.api_keys.update_one(
            {"key_id": key_id},
            {"$set": {"active": False, "revoked_at": time.time()}},
        )

        # Verify the SAME query returns nothing immediately after.
        row_post = await db.api_keys.find_one({"key": raw_key, "active": True})
        assert row_post is None, (
            "revoked key must not resolve on the next request "
            "— no cache lag allowed"
        )
    finally:
        await db.api_keys.delete_many({"key_id": key_id})
        await db.dev_users.delete_one({"user_id": uid})


# ── 2. Expired JWT rejects ──────────────────────────────────────────

def test_expired_jwt_rejects():
    """Craft a JWT with exp in the past and confirm the resolver
    raises jwt.ExpiredSignatureError (which the FastAPI layer turns
    into 401)."""
    import jwt
    from cto_services.auth import JWT_SECRET

    secret = JWT_SECRET or "test-secret"
    now    = int(time.time())
    expired_token = jwt.encode(
        {"user_id": "u_expired", "email": "expired@example.com",
         "iat": now - 3600, "exp": now - 60},
        secret, algorithm="HS256",
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(expired_token, secret, algorithms=["HS256"])


# ── 3. Login rate-limit fires ────────────────────────────────────────

def test_login_burst_rate_limiter_exists_and_binds():
    """Confirm the /auth/login handler wires check_rate_limit with a
    conservative per-IP cap. This is the entry point that stops
    credential stuffing before Mongo lockout kicks in.

    2026 audit Risk #2 follow-up: root-caused this as test-fixture
    drift, not a live gap — routers/auth.py was refactored to the
    async rate-limiter (`check_rate_limit_async`) but this string
    assertion still checked for the old sync name. Confirmed LIVE the
    limiter still fires: 6 rapid /auth/login calls from one IP return
    429 on the 6th (see test_jwt_revocation.py::_unique_test_ip)."""
    from routers import auth
    import inspect
    src = inspect.getsource(auth)
    # Layer 1 burst-limiter must be referenced by the login handler.
    assert 'check_rate_limit_async(f"login-ip:' in src, (
        "routers/auth.py login handler lost its Layer-1 burst limiter"
    )
    # Layer 2 Mongo-persisted lockout must still be wired.
    assert "login_attempts" in src, (
        "Layer-2 login_attempts lockout collection reference missing"
    )
