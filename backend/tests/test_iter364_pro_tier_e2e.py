"""
Iter 364 rollout — end-to-end verification of the tiered gate at the
HTTP layer (not just the pure is_user_allowed() unit).

Confirms:
  1. A pro-tier user (no loop_beta_enabled flag, no is_admin/is_unlimited)
     is NOT rejected with error='loop_beta_not_enabled' or
     'loop_mode_locked' by POST /api/aurem-dev/loop/start. It may fail
     for downstream reasons (missing project, etc.) — that is fine.
  2. GET  /api/aurem-dev/admin/loop-beta/status still reports the
     kill-switch state correctly with an admin token.
  3. Free-tier remains locked (error='loop_mode_locked').
"""
from __future__ import annotations
import os
import secrets
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://bin-context-pat.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE}/api/aurem-dev"


ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login",
                      json={"email": ADMIN_EMAIL,
                            "password": ADMIN_PASSWORD},
                      timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text[:200]}"
    token = r.json().get("token")
    assert token
    return token


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


async def _create_tier_user(tier: str):
    """Insert a dev_users row and issue a JWT via /auth/signup so we
    have a real bearer token. Returns (user_id, jwt)."""
    import bcrypt
    email = f"tier_{tier}_{secrets.token_hex(3)}@aurem.dev"
    pw = f"Iter364{secrets.token_hex(4)}!Aa"
    # Signup gives us a JWT + creates the user
    r = requests.post(f"{API}/auth/signup",
                      json={"email": email, "password": pw, "name": f"iter364_{tier}"},
                      timeout=15)
    assert r.status_code in (200, 201), f"signup: {r.status_code} {r.text[:300]}"
    body = r.json()
    token = body.get("token")
    user = body.get("user") or {}
    uid = user.get("user_id") or user.get("id")
    if token and not uid:
        # Decode JWT payload (unverified) to extract user_id
        import base64, json as _json
        try:
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
            uid = payload.get("user_id") or payload.get("sub")
        except Exception as e:
            print(f"jwt decode failed: {e}")
    assert token and uid, f"missing token or uid; body={body}"
    # Force tier + strip any admin flags in DB
    db = _db()
    await db.dev_users.update_one(
        {"user_id": uid},
        {"$set": {"tier": tier, "is_admin": False,
                  "is_unlimited": False, "loop_beta_enabled": False}},
    )
    return uid, token, email


@pytest.mark.asyncio
async def test_pro_tier_passes_the_gate():
    uid, tok, email = await _create_tier_user("pro")
    try:
        r = requests.post(f"{API}/loop/start",
                          headers={"Authorization": f"Bearer {tok}"},
                          json={"user_message": "iter364 pro-tier gate probe"},
                          timeout=20)
        # It's OK for the call to fail for unrelated reasons (missing
        # project, etc.). What must NOT happen is a tier-gate 403 with
        # error='loop_beta_not_enabled' or 'loop_mode_locked'.
        status = r.status_code
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:300]}
        detail = body.get("detail", body) if isinstance(body, dict) else {}
        if isinstance(detail, dict):
            err = detail.get("error")
        else:
            err = None
        print(f"[pro] status={status} error={err} detail={detail}")
        assert err not in ("loop_beta_not_enabled", "loop_mode_locked"), (
            f"Pro tier was blocked by tier-gate! status={status}, body={body}"
        )
    finally:
        db = _db()
        await db.dev_users.delete_one({"user_id": uid})


@pytest.mark.asyncio
async def test_free_tier_still_locked():
    uid, tok, email = await _create_tier_user("free")
    try:
        r = requests.post(f"{API}/loop/start",
                          headers={"Authorization": f"Bearer {tok}"},
                          json={"user_message": "iter364 free-tier gate probe"},
                          timeout=20)
        assert r.status_code == 403, f"expected 403, got {r.status_code} {r.text[:200]}"
        body = r.json()
        detail = body.get("detail", body)
        err = detail.get("error") if isinstance(detail, dict) else None
        assert err == "loop_mode_locked", (
            f"expected loop_mode_locked for free tier, got err={err} body={body}"
        )
    finally:
        db = _db()
        await db.dev_users.delete_one({"user_id": uid})


@pytest.mark.asyncio
async def test_starter_tier_still_locked():
    uid, tok, email = await _create_tier_user("starter")
    try:
        r = requests.post(f"{API}/loop/start",
                          headers={"Authorization": f"Bearer {tok}"},
                          json={"user_message": "iter364 starter-tier gate probe"},
                          timeout=20)
        assert r.status_code == 403, r.text[:200]
        body = r.json()
        detail = body.get("detail", body)
        err = detail.get("error") if isinstance(detail, dict) else None
        assert err == "loop_mode_locked", f"starter must be locked; got {err}"
    finally:
        db = _db()
        await db.dev_users.delete_one({"user_id": uid})


def test_admin_kill_switch_status(admin_token):
    r = requests.get(f"{API}/admin/loop-beta/status",
                     headers={"Authorization": f"Bearer {admin_token}"},
                     timeout=15)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    body = r.json()
    # Expect at least kill_switch info in some form
    assert isinstance(body, dict), body
    # Look for a truthy hint of kill switch state field
    keys = " ".join(body.keys()).lower()
    assert "kill" in keys or "switch" in keys, f"missing kill_switch info in response: {body}"
    print(f"[admin kill-switch status] {body}")


def test_admin_login_still_works_for_founder_bypass(admin_token):
    """Regression: admin/founder must still pass the tier gate.
    We don't need loop to complete — just confirm no 403 with a
    tier-gate error code."""
    r = requests.post(f"{API}/loop/start",
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"user_message": "iter364 admin bypass check"},
                      timeout=20)
    status = r.status_code
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:300]}
    detail = body.get("detail", body) if isinstance(body, dict) else {}
    err = detail.get("error") if isinstance(detail, dict) else None
    print(f"[admin bypass] status={status} error={err}")
    assert err not in ("loop_beta_not_enabled", "loop_mode_locked",
                       "tier_locked"), (
        f"Admin was blocked by tier-gate! status={status}, body={body}"
    )
