"""
services/google_oauth.py — AUREM Dev
Direct Google OAuth 2.0 (Authorization Code flow), mirroring the existing
services/github_oauth.py pattern exactly — manual httpx calls, no new
library dependency (Rule 12: reuse the proven pattern already in this
codebase over introducing authlib).

2026-08-25 — built as AUREM's own Google Cloud OAuth client to replace the
Emergent-broker redirect (auth.emergentagent.com) so the consent screen
shows AUREM's own branding. Identity-only scope (email/profile) — Google
is never used for repo/Drive/Calendar access here.

2026-08-28 — Login.jsx/Signup.jsx buttons flipped to this flow and the
old Emergent-broker route (/auth/google/session in routers/auth.py) was
deleted entirely, so no traffic can ever land on auth.emergentagent.com.
This is now the ONLY Google auth path.
"""
from __future__ import annotations
import os
from typing import Any
from urllib.parse import urlencode

import httpx
from services.http import ext_client


def _env(k: str) -> str:
    return os.getenv(k, "")


def client_id() -> str:     return _env("GOOGLE_OAUTH_CLIENT_ID")
def client_secret() -> str: return _env("GOOGLE_OAUTH_CLIENT_SECRET")

SCOPES = "openid email profile"


def auth_url(state: str, redirect_uri: str) -> str:
    """Build Google's OAuth authorize URL.

    `redirect_uri` is computed per-request by the caller (not a fixed
    env var) so Preview and Production each redirect back to their own
    domain — see routers/google_oauth.py::start(). Google requires the
    exact same redirect_uri string on both the authorize call and the
    later token exchange, so it's stashed in `oauth_states` between the
    two calls.
    """
    params = {
        "client_id":     client_id(),
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         SCOPES,
        "state":         state,
        "access_type":   "online",
        "prompt":        "select_account",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


async def exchange(code: str, redirect_uri: str) -> str:
    """Exchange OAuth `code` for an access_token. Raises on error."""
    async with ext_client("google", timeout=httpx.Timeout(10.0)) as c:
        r = await c.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id":     client_id(),
                "client_secret": client_secret(),
                "code":          code,
                "grant_type":    "authorization_code",
                "redirect_uri":  redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
    r.raise_for_status()
    data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Google token exchange returned no access_token: {data}")
    return token


async def get_profile(access_token: str) -> dict[str, Any]:
    """Fetch the signed-in user's Google profile (email/name/picture)."""
    async with ext_client("google", timeout=httpx.Timeout(10.0)) as c:
        r = await c.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    r.raise_for_status()
    return r.json()
