"""
services/github_oauth.py — AUREM Dev
GitHub OAuth flow + read-only helpers (user info, repo list).
"""
from __future__ import annotations
import logging
import os
from typing import Any

import httpx

from services.http import ext_client

logger = logging.getLogger(__name__)


def _env(k: str) -> str:
    return os.getenv(k, "")


def client_id() -> str:    return _env("GITHUB_OAUTH_CLIENT_ID")
def client_secret() -> str: return _env("GITHUB_OAUTH_CLIENT_SECRET")
def redirect_uri() -> str:  return _env("GITHUB_REDIRECT_URI")

SCOPES = "repo,read:user,user:email"
IDENTITY_SCOPES = "read:user,user:email"


def auth_url(state: str, force_reauth: bool = False,
             scopes: str | None = None) -> str:
    """Build GitHub's OAuth authorize URL.

    When `force_reauth=True` we append `prompt=select_account` so GitHub
    re-shows the authorize page and gives the user a chance to switch
    accounts (Iter 212). GitHub honors this on github.com sessions.

    `scopes` overrides the default full scope set — signup/login flows
    pass IDENTITY_SCOPES so users aren't asked for repo access just to
    authenticate (Iter 212m-187).
    """
    base = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={client_id()}"
        f"&redirect_uri={redirect_uri()}"
        f"&scope={scopes or SCOPES}"
        f"&state={state}"
    )
    if force_reauth:
        base += "&prompt=select_account"
    return base


async def exchange(code: str) -> str:
    """Exchange OAuth `code` for an access_token. Raises on error.

    NOTE: This POSTs to github.com (the web host) NOT api.github.com.
    Both usually fail together in an outage, so routing this through
    the same "github" dep breaker is intentional and desired.
    """
    async with ext_client(
        "github",
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ) as c:
        r = await c.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": client_id(),
                "client_secret": client_secret(),
                "code": code,
                "redirect_uri": redirect_uri(),
            },
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        d = r.json()
        if "error" in d:
            raise ValueError(d.get("error_description", "OAuth failed"))
        return d["access_token"]


async def gh_user(token: str) -> dict:
    async with ext_client(
        "github",
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ) as c:
        r = await c.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        r.raise_for_status()
        return r.json()


async def gh_repos(token: str) -> list[dict[str, Any]]:
    async with ext_client(
        "github",
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ) as c:
        r = await c.get(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            params={"sort": "updated", "per_page": 30, "type": "owner"},
        )
        r.raise_for_status()
        return r.json()
