"""
services/github_app.py — Phase 1.1

GitHub App runtime service. Provides:

  1. `app_jwt()`                     — RS256 JWT signed with the configured
                                       private key. 10-min TTL; in-process
                                       cached ~9 minutes to avoid re-signing
                                       per request. Used for App-level API
                                       calls (`GET /app`, `GET /app/installations`,
                                       `POST /app/installations/{id}/access_tokens`).

  2. `get_installation_token(id)`    — Mints a short-lived (≤1h) installation
                                       token via GitHub's API. In-process LRU
                                       with a **5-minute early-expiry safety
                                       margin** so the caller never sees a
                                       stale token. Tokens are **NEVER
                                       persisted to Mongo** — that's the
                                       security property that makes App-based
                                       auth strictly better than PAT storage.

  3. `list_installations()`          — Lists every installation of our App
                                       (App-JWT auth).

  4. `list_installations_for_user()` — Lists installations the given user OAuth
                                       token has admin access to (used by the
                                       wizard to render the picker in Phase 4).

  5. `list_installation_repos()`     — Lists repos accessible to a specific
                                       installation (uses installation token).

  6. `get_repo_via_installation()`   — Fetches a single repo through an
                                       installation token (used by
                                       `/projects/add` gate in Phase 3).

  7. `revoke_installation()`         — Deletes an installation on GitHub's
                                       side. Called by the future user-initiated
                                       disconnect endpoint.

  8. `verify_webhook_signature()`    — HMAC-SHA256 signature check for
                                       `X-Hub-Signature-256` headers on
                                       incoming webhook POSTs.

  9. `install_url(state)`            — Builds the install-flow entry URL:
                                       `https://github.com/apps/<slug>/installations/new?state=<>`.

Nothing here uses `os.environ` — all credentials come from the
`services.github_app_config` runtime cache which is hydrated at boot
from `admin_settings._id="github_app_config"`. If the cache is empty
(App not yet configured), every function raises
`GitHubAppNotConfigured` with a clear message. This is by design so
callers get a loud, single-point failure rather than silent errors.
"""
from __future__ import annotations

import hmac
import hashlib
import logging
import time
from typing import Optional

import httpx

from services.http import ext_client
import jwt  # PyJWT 2.10.0

from services.github_app_config import (
    get_runtime_github_app_config,
    is_configured,
)

logger = logging.getLogger(__name__)


GITHUB_API = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "aurem-github-app/1.0"

# App JWT — GitHub allows a maximum of 10 minutes. Re-sign at 9 minutes
# to guarantee a healthy margin even under clock drift.
_APP_JWT_TTL_SECONDS = 9 * 60

# Installation token — GitHub issues 1h TTL. We cache with a 5-minute
# early-expiry safety margin so a token is never returned within 5 min
# of its actual expiry, avoiding mid-operation 401s. The 5-minute
# margin is generous vs. typical repo op durations (indexing, ship).
_INSTALL_TOKEN_SAFETY_MARGIN_SECONDS = 5 * 60


class GitHubAppNotConfigured(RuntimeError):
    """Raised when any App operation is attempted before an admin has
    pasted valid credentials via `POST /admin/github-app-config`. This
    is a single, loud failure mode; callers should either short-circuit
    to a graceful "GitHub App not available yet" response or fall back
    to the PAT path (in `/projects/add`)."""


# ═════════════════════════════════════════════════════════════════════
# 1. App JWT (with in-process cache)
# ═════════════════════════════════════════════════════════════════════

# Cache tuple: (token: str, expires_at_epoch: float, app_id: str)
# Includes app_id so a credential rotation (different app_id) forces
# a fresh sign even if the previous token is still within its TTL.
_APP_JWT_CACHE: Optional[tuple[str, float, str]] = None


def _mint_app_jwt(app_id: str, private_key_pem: str) -> tuple[str, float]:
    """Return (token, expires_at_epoch) — no cache, no reuse."""
    now = int(time.time())
    payload = {
        # 30-second past-issue slack absorbs modest clock drift between
        # our host and GitHub's edge (GitHub tolerates up to 60s).
        "iat": now - 30,
        "exp": now + _APP_JWT_TTL_SECONDS + 30,
        "iss": str(app_id).strip(),
    }
    try:
        token = jwt.encode(payload, private_key_pem, algorithm="RS256")
    except Exception as e:                                       # noqa: BLE001
        # Wrap into a well-typed error so callers can distinguish
        # config errors from network/API failures.
        raise GitHubAppNotConfigured(
            f"Failed to sign App JWT — private key is invalid or unreadable: "
            f"{type(e).__name__}: {e}"
        ) from e
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token, now + _APP_JWT_TTL_SECONDS


def app_jwt() -> str:
    """Return a valid App JWT, minting a fresh one when the cached
    token is within 30s of expiry (or on credential rotation).
    """
    global _APP_JWT_CACHE
    if not is_configured():
        raise GitHubAppNotConfigured(
            "GitHub App is not configured. Ask an admin to paste credentials at "
            "Admin → Settings → GitHub App."
        )
    cfg = get_runtime_github_app_config()
    app_id = cfg["app_id"]
    now = time.time()

    cached = _APP_JWT_CACHE
    if (cached is not None
            and cached[2] == app_id
            and cached[1] - now > 30):                          # >30s of life left
        return cached[0]

    token, expires_at = _mint_app_jwt(app_id, cfg["private_key"])
    _APP_JWT_CACHE = (token, expires_at, app_id)
    return token


# ═════════════════════════════════════════════════════════════════════
# HTTP helpers
# ═════════════════════════════════════════════════════════════════════

def _headers_app() -> dict[str, str]:
    return {
        "Authorization":        f"Bearer {app_jwt()}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent":           USER_AGENT,
    }


def _headers_bearer(token: str) -> dict[str, str]:
    """Used for both installation tokens and user OAuth tokens. GitHub
    accepts `Bearer <token>` for both; the token type is inferred by
    GitHub from the token's own prefix (`ghs_` for install, `gho_`/`ghu_`
    for OAuth)."""
    return {
        "Authorization":        f"Bearer {token}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent":           USER_AGENT,
    }


# ═════════════════════════════════════════════════════════════════════
# 2. Installation token (with LRU + safety margin)
# ═════════════════════════════════════════════════════════════════════

# Cache: {installation_id: (token, expires_at_epoch, app_id)}
# `app_id` in the tuple ensures a credential rotation invalidates old
# tokens even if their TTL hasn't elapsed.
_INSTALL_TOKEN_CACHE: dict[int, tuple[str, float, str]] = {}


async def get_installation_token(installation_id: int) -> tuple[str, float]:
    """Mint (or reuse) an installation token for `installation_id`.

    Returns `(token, expires_at_epoch_utc)`. The token expires in ≤1h
    from GitHub; the cache never returns a token within
    `_INSTALL_TOKEN_SAFETY_MARGIN_SECONDS` of expiry.

    Raises `GitHubAppNotConfigured` if App creds are missing;
    raises `httpx.HTTPStatusError` on GitHub error responses.
    """
    if not is_configured():
        raise GitHubAppNotConfigured(
            "Installation token requested but the GitHub App is not configured."
        )
    cfg = get_runtime_github_app_config()
    app_id = cfg["app_id"]
    now = time.time()

    cached = _INSTALL_TOKEN_CACHE.get(installation_id)
    if (cached is not None
            and cached[2] == app_id
            and cached[1] - now > _INSTALL_TOKEN_SAFETY_MARGIN_SECONDS):
        return cached[0], cached[1]

    # Mint fresh
    url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"
    async with ext_client(
        "github",
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ) as client:
        r = await client.post(url, headers=_headers_app())
    if r.status_code == 404:
        # Installation was deleted or suspended — evict any stale
        # cache row and raise so the caller can propagate a clean 410.
        _INSTALL_TOKEN_CACHE.pop(installation_id, None)
        r.raise_for_status()                # raises HTTPStatusError
    r.raise_for_status()

    data = r.json() or {}
    token = data.get("token") or ""
    exp_iso = data.get("expires_at") or ""
    # GitHub returns ISO 8601 with `Z` suffix.
    from datetime import datetime, timezone as _tz
    try:
        expires_at = datetime.strptime(
            exp_iso, "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=_tz.utc).timestamp()
    except Exception:                                            # noqa: BLE001
        # Fallback: assume the documented 1h TTL if parsing fails.
        expires_at = now + 3600

    if not token:
        raise RuntimeError(
            f"GitHub returned an empty installation token payload for "
            f"installation_id={installation_id}"
        )

    _INSTALL_TOKEN_CACHE[installation_id] = (token, expires_at, app_id)
    return token, expires_at


def _prune_install_token_cache() -> None:
    """Best-effort eviction of tokens past their safety margin. Called
    opportunistically; not a strict invariant."""
    now = time.time()
    dead = [
        iid for iid, (_, exp, _) in _INSTALL_TOKEN_CACHE.items()
        if exp - now <= _INSTALL_TOKEN_SAFETY_MARGIN_SECONDS
    ]
    for iid in dead:
        _INSTALL_TOKEN_CACHE.pop(iid, None)


# ═════════════════════════════════════════════════════════════════════
# 3. Installation & repo listing
# ═════════════════════════════════════════════════════════════════════

async def list_installations() -> list[dict]:
    """Every installation of our App across all accounts (App-JWT auth)."""
    url = f"{GITHUB_API}/app/installations?per_page=100"
    out: list[dict] = []
    async with ext_client(
        "github",
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ) as client:
        while url:
            r = await client.get(url, headers=_headers_app())
            r.raise_for_status()
            page = r.json() or []
            out.extend(page)
            # Cursor via GitHub's Link header.
            link = r.headers.get("link", "")
            url = _next_link(link)
    return out


async def list_installations_for_user(user_access_token: str) -> list[dict]:
    """Installations the user (holding an OAuth token) has admin access
    to. Only installations of OUR App will appear here — GitHub filters
    by the OAuth App's client ID automatically when the user token
    belongs to us.

    Used by the wizard picker in Phase 4.
    """
    if not is_configured():
        raise GitHubAppNotConfigured(
            "list_installations_for_user requires the GitHub App to be configured."
        )
    url = f"{GITHUB_API}/user/installations?per_page=100"
    async with ext_client(
        "github",
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ) as client:
        r = await client.get(url, headers=_headers_bearer(user_access_token))
    r.raise_for_status()
    data = r.json() or {}
    return data.get("installations") or []


async def list_installation_repos(installation_id: int) -> list[dict]:
    """Repos accessible to a specific installation (installation-token auth).

    Handles pagination transparently.
    """
    token, _ = await get_installation_token(installation_id)
    out: list[dict] = []
    url = f"{GITHUB_API}/installation/repositories?per_page=100"
    async with ext_client(
        "github",
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ) as client:
        while url:
            r = await client.get(url, headers=_headers_bearer(token))
            r.raise_for_status()
            page = r.json() or {}
            out.extend(page.get("repositories") or [])
            link = r.headers.get("link", "")
            url = _next_link(link)
    return out


async def get_repo_via_installation(
    installation_id: int, owner: str, repo: str,
) -> dict:
    """Fetch a single repo through an installation token. Used by the
    Phase 3 `/projects/add` gate to verify the installation actually
    has access to the repo the user is trying to connect.

    Returns the GitHub `Repository` object on success. Raises
    `httpx.HTTPStatusError` on 404 (repo not accessible to this
    installation) / 401 (installation invalid).
    """
    token, _ = await get_installation_token(installation_id)
    url = f"{GITHUB_API}/repos/{owner}/{repo}"
    async with ext_client(
        "github",
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ) as client:
        r = await client.get(url, headers=_headers_bearer(token))
    r.raise_for_status()
    return r.json() or {}


async def verify_installation_for_repo(
    db, *, user_id: str, installation_id: int, owner: str, repo: str,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Shared verification used by BOTH connecting a new project
    (`cto_projects.py::add_project`) and reconnecting an existing one
    (`update_project`'s PATCH) — extracted 2026-08-26 so the two flows
    can never drift apart the way they just did (`add_project` set
    `installation_active=True` after this exact check; the PATCH
    reconnect endpoint set `auth_method="github_app"` but skipped the
    check AND the flag entirely — the real root cause of GitHub-App
    reconnects that never cleared the "not connected" banner).

    1. Confirms `user_id` owns an active `github_installations` row
       for `installation_id`.
    2. Confirms that installation actually has access to `owner/repo`
       via a real GitHub call (not just trusting the client-supplied
       IDs).

    Returns `(ok, error_code, message)` — `error_code`/`message` are
    None when `ok` is True.
    """
    install_row = await db.github_installations.find_one({
        "installation_id": int(installation_id),
        "user_id":         user_id,
        "active":          True,
    })
    if not install_row:
        return False, "installation_not_found_or_inactive", (
            "That GitHub App installation isn't linked to your account, "
            "or has been suspended/uninstalled. Re-install the App and "
            "try again."
        )
    try:
        await get_repo_via_installation(int(installation_id), owner, repo)
    except httpx.HTTPStatusError as _e:
        code = _e.response.status_code
        if code == 404:
            return False, "installation_no_repo_access", (
                f"The App installation on @{install_row.get('github_login','')} "
                f"doesn't have access to {owner}/{repo}. "
                "Grant access on GitHub → your App → Configure → "
                "\"Repository access\" (add this repo)."
            )
        if code in (401, 403):
            return False, "installation_token_rejected", (
                "GitHub rejected the installation access token. The "
                "installation may have been suspended — check the App "
                "settings on GitHub."
            )
        return False, "github_probe_failed", (
            f"GitHub returned HTTP {code} while verifying installation "
            f"access to {owner}/{repo}."
        )
    except httpx.RequestError as _e:
        return False, "installation_probe_request_error", (
            f"Couldn't reach GitHub to verify installation access "
            f"({type(_e).__name__}). Try again in a moment."
        )
    return True, None, None


async def revoke_installation(installation_id: int) -> None:
    """Delete an installation from GitHub's side (App-JWT auth).

    Called by the future user-initiated disconnect endpoint. Also
    evicts the local token cache row so a subsequent
    `get_installation_token` doesn't hand out a stale token.
    """
    url = f"{GITHUB_API}/app/installations/{installation_id}"
    async with ext_client(
        "github",
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ) as client:
        r = await client.delete(url, headers=_headers_app())
    # 204 = deleted; 404 = already gone (idempotent success).
    if r.status_code not in (204, 404):
        r.raise_for_status()
    _INSTALL_TOKEN_CACHE.pop(installation_id, None)


# ═════════════════════════════════════════════════════════════════════
# 4. Webhook signature verification
# ═════════════════════════════════════════════════════════════════════

def verify_webhook_signature(
    raw_body: bytes, signature_header: Optional[str],
) -> bool:
    """Constant-time HMAC-SHA256 check against the configured webhook
    secret. `signature_header` is the `X-Hub-Signature-256` header
    value from the incoming request (format: `sha256=<hex>`).

    Returns `False` (never raises) on any of: App not configured,
    missing header, malformed prefix, wrong length, hash mismatch.
    A `False` return by the webhook route MUST translate to `401`.
    """
    if not is_configured():
        return False
    if not signature_header or not isinstance(signature_header, str):
        return False
    if not signature_header.startswith("sha256="):
        return False
    provided = signature_header.split("=", 1)[1].strip().lower()
    if len(provided) != 64:
        return False

    cfg = get_runtime_github_app_config()
    secret = (cfg.get("webhook_secret") or "").encode("utf-8")
    if not secret:
        return False

    computed = hmac.new(
        secret, raw_body, hashlib.sha256,
    ).hexdigest().lower()
    return hmac.compare_digest(provided, computed)


# ═════════════════════════════════════════════════════════════════════
# 5. Install URL builder
# ═════════════════════════════════════════════════════════════════════

def install_url(state: Optional[str] = None) -> str:
    """Return the public install URL:
    `https://github.com/apps/<slug>/installations/new` (optional `?state=…`).

    Used by the wizard's "Continue with GitHub App" button and the
    Phase 2 `/github/app/install` redirect endpoint.
    """
    if not is_configured():
        raise GitHubAppNotConfigured(
            "install_url requires the GitHub App to be configured."
        )
    cfg = get_runtime_github_app_config()
    base = f"https://github.com/apps/{cfg['app_slug']}/installations/new"
    if state:
        from urllib.parse import quote
        return f"{base}?state={quote(state, safe='')}"
    return base


# ═════════════════════════════════════════════════════════════════════
# Internal — Link header pagination
# ═════════════════════════════════════════════════════════════════════

def _next_link(link_header: str) -> Optional[str]:
    """Parse the GitHub Link header and return the URL for `rel="next"`
    if present, otherwise `None`."""
    if not link_header:
        return None
    for part in link_header.split(","):
        segs = part.split(";")
        if len(segs) < 2:
            continue
        url_seg = segs[0].strip()
        rel_seg = segs[1].strip()
        if rel_seg == 'rel="next"':
            # Strip angle brackets: `<url>`
            return url_seg.strip("<>").strip()
    return None


# ═════════════════════════════════════════════════════════════════════
# R5c — Webhook Fence (live health check for the App's webhook pipeline)
# ═════════════════════════════════════════════════════════════════════

# The exact event set `routers/github_app.py::install_webhook` dispatches
# on (kept here, not re-derived, so the fence tile and the handler can
# never silently drift apart).
HANDLED_WEBHOOK_EVENTS = ("installation", "installation_repositories", "meta", "pull_request")

# Of the above, `installation` / `installation_repositories` / `meta`
# are sent to every GitHub App automatically — only these require an
# explicit checkbox under the App's "Permissions & events" settings.
SUBSCRIBABLE_WEBHOOK_EVENTS = ("pull_request",)


async def webhook_fence_status(recent_limit: int = 15) -> dict:
    """Live read-only health check for the GitHub App webhook pipeline
    (R5c "App Fence Tile"). Reports which content events the App is
    actually subscribed to on GitHub's side right now, the last N real
    delivery attempts with success/fail, and one overall verdict.
    Never raises — a broken webhook pipeline must not break the tile
    that reports it."""
    if not is_configured():
        return {
            "ok": False, "configured": False,
            "subscribed_events": [], "missing_subscriptions": list(SUBSCRIBABLE_WEBHOOK_EVENTS),
            "recent_deliveries": [], "failing_count": 0,
            "error": "GitHub App is not configured — paste credentials in Admin → GitHub App Config first.",
        }
    try:
        async with ext_client(
            "github",
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        ) as client:
            r_app = await client.get(f"{GITHUB_API}/app", headers=_headers_app())
            r_app.raise_for_status()
            subscribed_events = list(r_app.json().get("events") or [])

            r_del = await client.get(
                f"{GITHUB_API}/app/hook/deliveries?per_page={recent_limit}",
                headers=_headers_app(),
            )
            r_del.raise_for_status()
            deliveries = r_del.json() or []
    except Exception as e:                                        # noqa: BLE001
        return {
            "ok": False, "configured": True,
            "subscribed_events": [], "missing_subscriptions": [],
            "recent_deliveries": [], "failing_count": 0,
            "error": f"{type(e).__name__}: {e}"[:200],
        }

    missing = [e for e in SUBSCRIBABLE_WEBHOOK_EVENTS if e not in subscribed_events]
    recent = [
        {
            "id":            d.get("id"),
            "event":         d.get("event"),
            "action":        d.get("action"),
            "delivered_at":  d.get("delivered_at"),
            "status_code":   d.get("status_code"),
            "success":       bool(d.get("status_code") and 200 <= d["status_code"] < 300),
        }
        for d in deliveries
    ]
    failing = [d for d in recent if not d["success"]]
    return {
        "ok":                   (not missing) and (not failing),
        "configured":           True,
        "subscribed_events":    subscribed_events,
        "missing_subscriptions": missing,
        "recent_deliveries":    recent,
        "failing_count":        len(failing),
        "checked_at":           time.time(),
    }


__all__ = [
    "GitHubAppNotConfigured",
    "app_jwt",
    "get_installation_token",
    "list_installations",
    "list_installations_for_user",
    "list_installation_repos",
    "get_repo_via_installation",
    "revoke_installation",
    "verify_webhook_signature",
    "install_url",
    "webhook_fence_status",
    "HANDLED_WEBHOOK_EVENTS",
    "SUBSCRIBABLE_WEBHOOK_EVENTS",
]
