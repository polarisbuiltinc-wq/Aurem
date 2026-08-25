"""
routers/google_oauth.py — AUREM Dev
Direct Google OAuth 2.0 sign-in/sign-up. Mirrors routers/github_oauth.py's
signup/login branch exactly (same oauth_states collection, same 5-min TTL,
same JWT-mint + /oauth-finish redirect) so downstream account-bridging
code (dev_users lookup/merge, is_admin/tier bootstrap, JWT minting) is
100% reused, unchanged.

2026-08-25 — built as a PARALLEL path alongside the existing Emergent-
broker route (POST /auth/google/session in routers/auth.py).
2026-08-28 — Login.jsx/Signup.jsx flipped to this flow and the broker
route was deleted entirely. This is now the ONLY Google auth path.

Identity-only — no repo/Drive/Calendar scopes. Mounted at
/api/aurem-dev/google/oauth/* (see main.py), matching the exact same
prefix convention as /api/aurem-dev/github/oauth/*.
"""
from __future__ import annotations
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from cto_services.auth import create_token
from cto_services.db import get_db
from routers.auth import _email_ci
from routers.github_oauth import _request_origin, _frontend_url
from services.google_oauth import auth_url, exchange, get_profile
from services.usage import is_founder_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/google/oauth", tags=["Google OAuth"])


@router.get("/start")
async def start(request: Request, intent: Optional[str] = Query(default=None)):
    """Kick off direct Google sign-in. Identity-only — no user auth
    required to hit this (same as GitHub's signup/login branch)."""
    origin = _request_origin(request)
    if not origin:
        raise HTTPException(500, "Could not determine request origin")
    redirect_uri = f"{origin}/api/aurem-dev/google/oauth/callback"
    prefix = "login" if (intent or "").lower() == "login" else "signup"
    state = f"{prefix}:{uuid.uuid4().hex}"
    db = get_db()
    if db is not None:
        await db.oauth_states.insert_one({
            "state":        state,
            "mode":         prefix,
            "provider":     "google",
            "user_id":      None,
            "ts":           time.time(),
            "origin":       origin,
            "redirect_uri": redirect_uri,
            "created_at":   datetime.now(timezone.utc),
        })
    return RedirectResponse(url=auth_url(state, redirect_uri))


@router.get("/callback")
async def callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
):
    """Google redirects here after consent. Mirrors github_oauth.py's
    callback: graceful cancel/error redirects (no stock 422), single-use
    state row with a 5-min TTL, then the exact same dev_users bridging
    logic as the GitHub signup flow."""
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    if error or not code:
        logger.info("[google-oauth] callback non-success: error=%s code_present=%s",
                     error, bool(code))
        cancel_origin = None
        target_path = "/login"
        try:
            if state:
                s_tmp = await db.oauth_states.find_one({"state": state})
                if s_tmp:
                    cancel_origin = s_tmp.get("origin")
                    target_path = "/signup" if s_tmp.get("mode") == "signup" else "/login"
                await db.oauth_states.delete_one({"state": state})
        except Exception:
            pass
        reason = error or "missing_code"
        return RedirectResponse(
            url=_frontend_url(target_path, f"google=cancelled&reason={reason}",
                               origin=cancel_origin)
        )

    if not state or ":" not in state:
        raise HTTPException(400, "Invalid state")

    s = await db.oauth_states.find_one({"state": state})
    if not s:
        raise HTTPException(400, "Unknown OAuth state")
    created_at = s.get("created_at")
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created_at > timedelta(minutes=5):
            await db.oauth_states.delete_one({"state": state})
            raise HTTPException(400, "OAuth state expired")

    state_origin = s.get("origin")
    redirect_uri = s.get("redirect_uri")
    mode = s.get("mode") or "signup"
    await db.oauth_states.delete_one({"state": state})

    try:
        token = await exchange(code, redirect_uri)
        profile = await get_profile(token)
    except Exception as e:
        logger.error("[google-oauth] callback failed: %r", e)
        return RedirectResponse(
            url=_frontend_url("/login", f"google=error&msg={quote_plus(str(e))}",
                               origin=state_origin)
        )

    g_email   = (profile.get("email") or "").strip()
    g_name    = profile.get("name") or ""
    g_picture = profile.get("picture") or ""
    g_sub     = profile.get("sub") or ""
    g_verified = bool(profile.get("email_verified", False))

    if not g_email:
        return RedirectResponse(
            url=_frontend_url("/login", "google=error&msg=no_email_returned",
                               origin=state_origin)
        )

    existing = await db.dev_users.find_one(_email_ci(g_email), {"_id": 0})

    if existing:
        user_id   = existing["user_id"]
        user_mail = existing.get("email") or g_email
        is_admin  = bool(existing.get("is_admin"))
        is_new_account = False
        await db.dev_users.update_one(
            {"user_id": user_id},
            {"$set": {"google": {
                "id":           g_sub,
                "name":         g_name,
                "email":        g_email,
                "picture":      g_picture,
                "connected_at": time.time(),
            }}},
        )
    else:
        is_new_account = True
        user_id    = uuid.uuid4().hex
        user_mail  = g_email
        is_founder = is_founder_email(user_mail)
        is_admin   = is_founder
        tier       = "founder" if is_founder else "free"
        tokens     = 10**9 if is_founder else 1000
        await db.dev_users.insert_one({
            "user_id":          user_id,
            "email":            user_mail,
            "name":             g_name or user_mail.split("@")[0],
            "password":         None,          # OAuth-only user
            "auth_provider":    "google",
            "tier":             tier,
            "tokens_remaining": tokens,
            "is_admin":         is_admin,
            "is_unlimited":     is_admin,
            "created_at":       time.time(),
            "email_verified":   g_verified,
            "google": {
                "id":           g_sub,
                "name":         g_name,
                "email":        g_email,
                "picture":      g_picture,
                "connected_at": time.time(),
            },
            "track":            "developer",
            "track_updated_at": time.time(),
        })

    jwt_token = create_token(user_id, user_mail, is_admin=is_admin)
    base = (state_origin or "").rstrip("/")
    if not base:
        raise HTTPException(500, "Could not determine redirect origin")
    return RedirectResponse(
        url=(
            f"{base}/oauth-finish"
            f"#token={jwt_token}"
            f"&login={quote_plus(g_name or user_mail)}"
            f"&new={'1' if is_new_account else '0'}"
            # 2026-08-25 — root-cause fix for a founder-reported bug:
            # this callback shares OAuthFinish.jsx's GitHub-labeled
            # `#token=` branch, so any failure inside that branch
            # (e.g. a stale re-run reading an already-cleared hash)
            # was bouncing to `/login?github=missing_token` for what
            # was structurally a Google sign-in. `provider=google`
            # lets the frontend label any failure correctly.
            f"&provider=google"
        )
    )
