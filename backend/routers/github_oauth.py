"""
routers/github_oauth.py — AUREM Dev
OAuth + repo-list endpoints. Mounted under /api/aurem-dev/github/oauth/*
so it does not collide with the legacy /github/{status,push} surface.
"""
# arch: allow-http — OAuth token exchange with github.com API (iter 212m-225)
from __future__ import annotations
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from services.http import ext_client
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from cto_services.auth import create_token, current_dev
from cto_services.db import get_db
from services.github_oauth import auth_url, exchange, gh_user, gh_repos, IDENTITY_SCOPES
from services.usage import is_founder_email
from routers.github_funnel import track_server_side as _funnel_track  # 2026-08-01

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github/oauth", tags=["GitHub OAuth"])


def _request_origin(req: Request) -> Optional[str]:
    """Derive the live origin (scheme + host) the user is sitting on so
    the OAuth callback can redirect back to that same domain instead of
    a hardcoded APP_URL. Handles preview pods, auremcto.com, aurem.dev,
    and any custom domain in one shot.

    Precedence:
      1. Origin header (set by the browser on the /connect navigation)
      2. Referer header (parsed for scheme + host)
      3. X-Forwarded-Proto + X-Forwarded-Host (Kubernetes ingress)
      4. None → callback falls back to APP_URL.
    """
    try:
        origin = req.headers.get("origin")
        if origin and origin.startswith(("http://", "https://")):
            return origin.rstrip("/")
        ref = req.headers.get("referer") or req.headers.get("referrer")
        if ref:
            from urllib.parse import urlparse
            p = urlparse(ref)
            if p.scheme and p.netloc:
                return f"{p.scheme}://{p.netloc}"
        proto = req.headers.get("x-forwarded-proto") or "https"
        host = req.headers.get("x-forwarded-host") or req.headers.get("host")
        if host:
            return f"{proto}://{host}".rstrip("/")
    except Exception:
        pass
    return None


def _frontend_url(path: str, query: str, origin: Optional[str] = None) -> str:
    # Prefer the origin captured at /connect time (multi-domain support).
    # Falls back to APP_URL env for legacy callers that didn't store one.
    base = (origin or os.getenv("APP_URL") or "").rstrip("/")
    if not base:
        raise HTTPException(500, "APP_URL not configured on this deployment")
    return f"{base}{path}?{query}"


def _frontend_settings_url(query: str, origin: Optional[str] = None) -> str:
    return _frontend_url("/settings", query, origin=origin)


async def _gh_primary_email(token: str) -> Optional[str]:
    """GitHub's /user endpoint returns email=null when the user marked
    it private. Hit /user/emails (needs read:user / user:email scope
    which our SCOPES list already includes via read:user) to find the
    verified primary.

    2026-02-12 · Batch 8b — migrated to services.http.ext_client.
    Retains graceful-degrade contract: ANY failure (network,
    timeout, non-2xx from raise_for_status, ExternalCallError from
    the wrapper) is swallowed and returns None. Signup flow at
    line 357 depends on this — a raise here would 500 the OAuth
    callback and break login for users with private emails.
    """
    try:
        async with ext_client("github", timeout=httpx.Timeout(10.0)) as c:
            r = await c.get(
                "https://api.github.com/user/emails",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            r.raise_for_status()
            for row in r.json() or []:
                if row.get("primary") and row.get("verified"):
                    return row.get("email")
            for row in r.json() or []:
                if row.get("verified"):
                    return row.get("email")
    except Exception as e:
        logger.warning(f"[oauth] /user/emails fetch failed: {e!r}")
    return None


@router.get("/connect")
async def connect(
    request: Request,
    authorization: Optional[str] = Header(None),
    auth: Optional[str] = Query(None),
    signup: Optional[str] = Query(None),
    intent: Optional[str] = Query(None),
    force_reauth: Optional[str] = Query(None),
    fs: Optional[str] = Query(default=None),    # 2026-08-01 funnel session_id
    fsrc: Optional[str] = Query(default=None),  # 2026-08-01 funnel source
):
    """Kick off OAuth.

    Three modes:
      • **Connect** (default) — used by an already-logged-in user from
        Settings to attach GitHub to their existing AUREM account. JWT
        required (Authorization header or `?auth=` query for browser
        navigation).
      • **Signup** (`?signup=1`) — true OAuth-first auth from the /signup
        page. No JWT required. Callback either logs in the matching user
        or creates a new account from the GitHub identity.
      • **Login** (`?signup=1&intent=login`) — same OAuth-first auth, but
        originating from /login. On cancel we send the user back to
        /login (not /signup) — iter 113 UX fix.

    `?force_reauth=1` (Iter 212) — append `prompt=select_account` so
    GitHub re-shows the authorize page and lets the user switch accounts
    instead of silently returning the cached session. Used by the
    "Switch GitHub account" link in the Add-Project modal.

    `?fs=<session_id>&fsrc=<source>` (2026-08-01) — funnel telemetry
    hooks so the client `cta_click` event and the server-side
    `oauth_redirect`/`callback_received`/`linked` events all share a
    session_id. Both params are stored in `oauth_states` and re-read
    inside `/callback` so the whole flow is stitchable.
    """
    fr = (force_reauth or "").lower() in ("1", "true", "yes")
    origin = _request_origin(request)
    db = get_db()
    if signup in ("1", "true", "yes"):
        # Anonymous OAuth start — state nonce only, no user binding.
        # iter 113 — encode the originating page in the state prefix so
        # we know where to redirect on cancel. Auth behaviour is
        # identical for both prefixes.
        prefix = "login" if (intent or "").lower() == "login" else "signup"
        nonce = uuid.uuid4().hex
        state = f"{prefix}:{nonce}"
        if db is not None:
            await db.oauth_states.insert_one({
                "state":      state,
                "mode":       prefix,
                "user_id":    None,
                "ts":         time.time(),
                # Iter 212m-99 — capture the live origin so /callback
                # can redirect back to the same host the user started
                # on (preview pod, auremcto.com, aurem.dev, custom).
                "origin":     origin,
                # Iter 212j — tz-aware created_at for the 5-min TTL
                # check in /callback. Prevents replay of stale state
                # rows if a user opens the OAuth tab, leaves it for
                # hours, then completes auth.
                "created_at": datetime.now(timezone.utc),
                # 2026-08-01 — funnel stitching.
                "funnel_session": fs,
                "funnel_source":  fsrc or prefix,
            })
        # 2026-08-01 — server-side funnel event.
        await _funnel_track(
            "oauth_redirect", source=(fsrc or prefix),
            session_id=fs, meta={"mode": prefix},
        )
        # Iter 212m-187 — signup/login only needs identity (profile +
        # email), NOT repo access. Repo connect happens later via the
        # PAT wizard.
        return RedirectResponse(
            url=auth_url(state, force_reauth=fr, scopes=IDENTITY_SCOPES))

    # Connect mode — must be authenticated.
    if not authorization and auth:
        authorization = f"Bearer {auth}"
    user = await current_dev(authorization)
    state = f"{user['user_id']}:{uuid.uuid4().hex}"
    if db is not None:
        await db.oauth_states.insert_one({
            "state":      state,
            "mode":       "connect",
            "user_id":    user["user_id"],
            "ts":         time.time(),
            "origin":     origin,
            "created_at": datetime.now(timezone.utc),
            "funnel_session": fs,
            "funnel_source":  fsrc or "settings_card",
        })
    # 2026-08-01 — server-side funnel event.
    await _funnel_track(
        "oauth_redirect", source=(fsrc or "settings_card"),
        session_id=fs, user_id=user["user_id"],
        meta={"mode": "connect"},
    )
    return RedirectResponse(url=auth_url(state, force_reauth=fr))


@router.get("/callback")
async def callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
):
    """GitHub redirects here after the user finishes the consent flow.

    Two non-success paths to handle gracefully (without 422):
      1. User clicked **Cancel** on GitHub → `?error=access_denied&...`
         (no `code` present). We redirect to the frontend with a friendly
         "github=cancelled" message instead of FastAPI's stock 422.
      2. Any other GitHub-side error → same friendly redirect.
    Success path: `?code=...&state=...` exchanges the code and continues.
    """
    # Iter 109/113 — user-cancelled the GitHub consent screen.
    if error or not code:
        logger.info("[oauth] callback non-success: error=%s code_present=%s state=%s",
                    error, bool(code), state)
        # 2026-08-01 — funnel event even on cancel/error so we can
        # measure abandonment rate on the GitHub consent screen.
        try:
            s_funnel = None
            db_f = get_db()
            if db_f is not None and state:
                s_funnel = await db_f.oauth_states.find_one({"state": state})
        except Exception:
            s_funnel = None
        await _funnel_track(
            "callback_received",
            source=(s_funnel or {}).get("funnel_source") or "unknown",
            session_id=(s_funnel or {}).get("funnel_session"),
            meta={"outcome": "error", "reason": error or "missing_code"},
        )
        # Iter 212m-99 — pull origin out of the state row so the
        # cancel-redirect lands on the same host the user started on.
        cancel_origin = None
        try:
            db_tmp = get_db()
            if db_tmp is not None and state:
                s_tmp = await db_tmp.oauth_states.find_one({"state": state})
                cancel_origin = (s_tmp or {}).get("origin")
        except Exception:
            pass
        # Decide where to send them based on the state prefix:
        #   "login:..."  → /login    (iter 113 — UX fix)
        #   "signup:..." → /signup
        #   any user-id prefix → /projects (iter 197 — connect flow
        #     starts on Projects page, errors should land back there
        #     so the user sees them in context and the PAT fallback
        #     auto-reveals via the `?github=…` useEffect)
        target_path = "/projects"
        if state and state.startswith("signup:"):
            target_path = "/signup"
        elif state and state.startswith("login:"):
            target_path = "/login"
        # Clean up the dangling state row so it can't be replayed.
        try:
            db = get_db()
            if db is not None and state:
                await db.oauth_states.delete_one({"state": state})
        except Exception:
            pass
        reason = error or "missing_code"
        # Keep the redirect URL short — frontend reads ?github=cancelled
        return RedirectResponse(
            url=_frontend_url(target_path, f"github=cancelled&reason={reason}",
                              origin=cancel_origin)
        )

    if not state or ":" not in state:
        raise HTTPException(400, "Invalid state")
    mode_or_user, _, _ = state.partition(":")

    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    # Verify state was actually issued by us
    s = await db.oauth_states.find_one({"state": state})
    if not s:
        raise HTTPException(400, "Unknown OAuth state")
    # Iter 212j — 5-minute TTL on OAuth state. Without this, a user
    # who opens the OAuth tab and finishes auth hours later could
    # complete the callback against a stale row (or, worse, an
    # attacker who steals an old state value could replay it). The
    # row is also deleted on success/error below so single-use is
    # guaranteed; this guards the time dimension.
    created_at = s.get("created_at")
    if isinstance(created_at, datetime):
        # Coerce naive datetimes to UTC for consistent comparison.
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created_at > timedelta(minutes=5):
            await db.oauth_states.delete_one({"state": state})
            raise HTTPException(400, "OAuth state expired")
    flow = s.get("mode") or (
        "signup" if mode_or_user in ("signup", "login") else "connect"
    )
    # Iter 212m-99 — origin captured at /connect time; falls back to APP_URL.
    state_origin = s.get("origin")
    # 2026-08-01 — funnel stitching info pulled from state row.
    funnel_session = s.get("funnel_session")
    funnel_source  = s.get("funnel_source") or flow
    # Iter 113 — both `signup:` and `login:` prefixes use the same
    # OAuth-first auth path. The only difference is the cancel-redirect
    # target (handled above) and where the SUCCESS path sends them on
    # login (existing user → /dashboard either way, new user → /dashboard).

    # Exchange code → token → GitHub user (common to both flows).
    try:
        token = await exchange(code)
        info  = await gh_user(token)
    except Exception as e:
        logger.error(f"[oauth] callback failed: {e!r}")
        # 2026-08-01 — funnel event for exchange failures too.
        await _funnel_track(
            "callback_received", source=funnel_source,
            session_id=funnel_session,
            meta={"outcome": "exchange_error", "reason": str(e)[:200]},
        )
        await db.oauth_states.delete_one({"state": state})
        # Iter 197 — connect-flow exchange errors should land back on
        # /projects (where the user started) rather than /settings, so
        # the inline error banner + auto-revealed PAT fallback are
        # visible in context.
        err_path = "/projects" if flow == "connect" else "/settings"
        from urllib.parse import quote_plus
        return RedirectResponse(
            url=_frontend_url(err_path, f"github=error&msg={quote_plus(str(e))}",
                              origin=state_origin)
        )

    # 2026-08-01 — success side of callback_received.
    await _funnel_track(
        "callback_received", source=funnel_source,
        session_id=funnel_session,
        meta={"outcome": "success", "flow": flow},
    )

    await db.oauth_states.delete_one({"state": state})

    gh_login  = (info or {}).get("login") or ""
    gh_avatar = (info or {}).get("avatar_url") or ""
    gh_email  = (info or {}).get("email")
    # Iter 212m-218 — capture display name for git author identity.
    # GitHub's `/user` endpoint returns `name` as the user's public
    # profile name (may be null if the user never set one).
    gh_name   = (info or {}).get("name") or ""

    # ── Signup / sign-in flow ─────────────────────────────────────────
    if flow == "signup":
        # Pull a verified email if GitHub hid it on the public profile.
        if not gh_email:
            gh_email = await _gh_primary_email(token)
        # Find an existing AUREM account to log in.
        existing = None
        if gh_login:
            existing = await db.dev_users.find_one(
                {"github.login": gh_login}, {"_id": 0},
            )
        if not existing and gh_email:
            existing = await db.dev_users.find_one(
                {"email": {"$regex": f"^{re.escape(gh_email)}$",
                           "$options": "i"}},
                {"_id": 0},
            )

        if existing:
            user_id   = existing["user_id"]
            user_mail = existing.get("email") or gh_email or ""
            is_admin  = bool(existing.get("is_admin"))
            is_new_account = False  # Iter 156 — return-login, not signup
            await db.dev_users.update_one(
                {"user_id": user_id},
                {"$set": {"github": {
                    "id":           (info or {}).get("id"),   # iter 211
                    "access_token": token,
                    "login":        gh_login,
                    "name":         gh_name,                  # iter 212m-218
                    "email":        gh_email or "",           # iter 212m-218
                    "avatar_url":   gh_avatar,
                    "connected_at": time.time(),
                }}},
            )
        else:
            # Brand-new account, no password (GitHub-only sign-in).
            is_new_account = True  # Iter 156 — fire signup conversion downstream
            user_id   = uuid.uuid4().hex
            # If GitHub still hid the email, fall back to a stable
            # synthetic so the unique constraint never bites us.
            user_mail = gh_email or f"{gh_login or user_id}@users.noreply.github.com"
            is_founder = is_founder_email(user_mail)
            is_admin   = is_founder
            tier       = "founder" if is_founder else "free"
            tokens     = 10**9 if is_founder else 1000
            # Iter 211 — capture github_id (immutable numeric ID).
            # `login` is the username and CAN change; `id` is forever.
            # Stored as the canonical dedupe key for future signups.
            gh_id_num = (info or {}).get("id")
            await db.dev_users.insert_one({
                "user_id":          user_id,
                "email":            user_mail,
                "name":             gh_login or user_mail.split("@")[0],
                "password":         None,            # OAuth-only user
                "auth_provider":    "github",
                "tier":             tier,
                "tokens_remaining": tokens,
                "is_admin":         is_admin,
                "is_unlimited":     is_admin,
                # Iter 212m-222 — CRITICAL: this path was silently
                # omitting created_at, so every GitHub-OAuth user was
                # invisible in the admin /users 24h/7d/30d windowed
                # filters (Mongo can't $gte on a missing field).
                # Explicit float epoch matches the writes in
                # /auth/signup and /auth/google/session.
                "created_at":       time.time(),
                # 2026-08-20 — same class of bug as the created_at fix
                # above, just for `email_verified`: it was never set,
                # so every GitHub-OAuth signup showed "email
                # unverified" forever (no way to fix it — OAuth users
                # never get our own verify email) and was silently
                # excluded from the First-50 promo. GitHub only
                # verifies when it actually hands us a real email
                # (`gh_email`) — the synthetic noreply fallback below
                # means GitHub hid it, so there's nothing to mark
                # verified in that case.
                "email_verified":   bool(gh_email),
                "github": {
                    "id":           gh_id_num,        # iter 211 — immutable
                    "access_token": token,
                    "login":        gh_login,
                    "name":         gh_name,          # iter 212m-218
                    "email":        gh_email or "",   # iter 212m-218
                    "avatar_url":   gh_avatar,
                    "connected_at": time.time(),
                },
                # Iter 390 — Developer-only default; see /signup handler.
                "track":              "developer",
                "track_updated_at":   time.time(),
            })

        jwt_token = create_token(user_id, user_mail, is_admin=is_admin)
        # 2026-08-01 — funnel `linked` event (terminal success for signup flow).
        await _funnel_track(
            "linked", source=funnel_source, session_id=funnel_session,
            user_id=user_id,
            meta={"flow": "signup", "new_account": is_new_account,
                  "gh_login": gh_login},
        )
        # Bounce the browser back to a tiny finish page that stashes
        # the token in localStorage and routes to /dashboard. Token
        # goes in the URL fragment (`#`) so it never hits server logs
        # or Referer headers.
        # Iter 212m-99 — use the origin captured at /connect time so
        # the JWT lands on the same domain the user started on
        # (auremcto.com vs aurem.dev vs preview pod).
        base = (state_origin or os.getenv("APP_URL") or "").rstrip("/")
        if not base:
            raise HTTPException(500, "APP_URL not configured")
        return RedirectResponse(
            url=(
                f"{base}/oauth-finish"
                f"#token={jwt_token}"
                f"&login={gh_login}"
                # Iter 156 — `new=1` signals to OAuthFinish.jsx that
                # this redirect just minted a brand-new account, so
                # the page can fire the Google Ads signup conversion
                # exactly once (no double-fire for return logins).
                f"&new={'1' if is_new_account else '0'}"
            )
        )

    # ── Connect-existing-account flow (original behaviour) ────────────
    user_id = s.get("user_id") or mode_or_user
    if not user_id:
        raise HTTPException(400, "Invalid state")
    await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {"github": {
            "access_token": token,
            "login":        gh_login,
            "name":         gh_name,          # iter 212m-218
            "email":        gh_email or "",   # iter 212m-218
            "avatar_url":   gh_avatar,
            "connected_at": time.time(),
        }}},
    )
    # 2026-08-01 — funnel `linked` event (terminal success for connect flow).
    await _funnel_track(
        "linked", source=funnel_source, session_id=funnel_session,
        user_id=user_id, meta={"flow": "connect", "gh_login": gh_login},
    )
    return RedirectResponse(
        url=_frontend_settings_url(f"github=connected&login={gh_login}",
                                    origin=state_origin)
    )


@router.get("/status")
async def status(authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True, "connected": False}
    u = await db.dev_users.find_one(
        {"user_id": user["user_id"]}, {"_id": 0, "github": 1}
    )
    gh = (u or {}).get("github") or {}
    if not gh.get("access_token"):
        return {"ok": True, "connected": False}
    return {
        "ok": True,
        "connected": True,
        "login": gh.get("login"),
        "avatar_url": gh.get("avatar_url"),
        "connected_at": gh.get("connected_at"),
    }


@router.get("/repos")
async def repos(authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    u = await db.dev_users.find_one(
        {"user_id": user["user_id"]}, {"_id": 0, "github": 1}
    )
    token = ((u or {}).get("github") or {}).get("access_token")
    if not token:
        raise HTTPException(400, "GitHub not connected")
    data = await gh_repos(token)
    return {
        "ok": True,
        "repos": [
            {
                "name": r.get("name"),
                "full_name": r.get("full_name"),
                "private": r.get("private", False),
                "url": r.get("html_url"),
                "default_branch": r.get("default_branch", "main"),
                "updated_at": r.get("updated_at"),
            }
            for r in data
        ],
    }


@router.delete("/disconnect")
async def disconnect(authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is not None:
        await db.dev_users.update_one(
            {"user_id": user["user_id"]},
            {"$unset": {"github": ""}},
        )
    return {"ok": True}
