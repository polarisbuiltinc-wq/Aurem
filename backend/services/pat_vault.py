"""services/pat_vault.py — GitHub repo-auth resolver. **App-only since 2026-06.**

Founder directive (PAT-removal, 2026-06): Personal Access Tokens and
user-OAuth tokens are NO LONGER valid auth for ANY GitHub repo
operation. Every repo read/write authenticates exclusively via the
AUREM GitHub App's short-lived installation tokens (the mechanism
proven in the Pillar-1 rollback drill).

The old contract ("returns str|None, never raises, callers fall back
to the user's OAuth token") was the engine of silent auth fallbacks
and is intentionally GONE. The new contract FAILS CLOSED with typed,
honest error codes:

    app_installation_missing  — project not connected via the GitHub
                                App (no auth_method="github_app" /
                                no installation_id), or the App itself
                                isn't configured server-side.
    app_installation_revoked  — GitHub rejected the installation
                                (suspended, uninstalled, 401/403/404
                                on token mint).
    github_unreachable        — network-level failure reaching GitHub.
                                TEMPORARY — never to be presented as
                                a revocation.
    github_rejected           — any other GitHub-side rejection.

Legacy PAT rows: `migrations/002_encrypt_pats.py` is kept as inert
history only. Row migration lives in routers/github_auth_migration.py
(admin, propose-then-execute).
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger("aurem.pat_vault")


class GithubAppAuthError(Exception):
    """Typed, user-safe GitHub-App auth failure. `.code` is machine-
    readable; `str(e)` / `.detail` is safe to show verbatim in UI."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


_MISSING_DETAIL = (
    "This project isn't connected through the AUREM GitHub App. "
    "Open Projects → Connect and install the GitHub App for this repo. "
    "(PAT and OAuth-token auth were removed — the App is the only "
    "supported method.)"
)
_REVOKED_DETAIL = (
    "GitHub rejected the App installation for this project — it may "
    "have been suspended or uninstalled. Re-install the AUREM GitHub "
    "App on this repo, then retry."
)
_UNREACHABLE_DETAIL = (
    "GitHub is unreachable right now (network issue between our server "
    "and github.com). This is temporary — your repo connection is fine. "
    "Retry in a moment."
)


async def get_repo_token(project: dict) -> str:
    """Mint a fresh GitHub App installation token for this project.

    App-only. Raises GithubAppAuthError (never returns None/empty).
    The token is short-lived and must never be persisted.
    """
    if not project:
        raise GithubAppAuthError("app_installation_missing", _MISSING_DETAIL)

    method = (project.get("auth_method") or "").lower()
    iid = project.get("installation_id")
    if method != "github_app" or not iid:
        raise GithubAppAuthError("app_installation_missing", _MISSING_DETAIL)

    try:
        from services.github_app import (
            GitHubAppNotConfigured, get_installation_token,
        )
    except ImportError as e:  # pragma: no cover — packaging error
        raise GithubAppAuthError("app_installation_missing",
                                 _MISSING_DETAIL) from e
    try:
        token, _expires_at = await get_installation_token(int(iid))
    except GitHubAppNotConfigured as e:
        raise GithubAppAuthError("app_installation_missing",
                                 _MISSING_DETAIL) from e
    except (httpx.TimeoutException, httpx.NetworkError) as e:
        raise GithubAppAuthError("github_unreachable",
                                 _UNREACHABLE_DETAIL) from e
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403, 404):
            # 2026-08-23 — BUG FIX: a 401/403/404 minting a FRESH
            # installation token does NOT reliably mean the App
            # installation was revoked. GitHub App auth for the mint
            # call itself goes through a short-lived App-level JWT
            # (regenerated per call) — any transient clock-skew/JWT/
            # GitHub-side hiccup on THAT call can 401/403 even for a
            # perfectly healthy installation. The AUTHORITATIVE signal
            # for a real revocation is the webhook-maintained
            # `github_installations.suspended_at`/`deleted_at` fields
            # (routers/github_app.py's installation.suspend/deleted
            # handlers) — check those before concluding "revoked". A
            # real user hit this exact false alarm mid-ship (GitHub
            # App showed "disconnected" while actively shipping a
            # task, even though nothing was ever actually revoked on
            # GitHub's side).
            is_really_revoked = True
            try:
                from cto_services.db import get_db
                _db = get_db()
                if _db is not None:
                    inst = await _db.github_installations.find_one(
                        {"installation_id": int(iid)},
                        {"_id": 0, "suspended_at": 1, "deleted_at": 1},
                    )
                    is_really_revoked = bool(
                        inst and (inst.get("suspended_at") or inst.get("deleted_at"))
                    )
            except Exception:
                pass  # fail closed to the pre-existing "revoked" behaviour
            if is_really_revoked:
                raise GithubAppAuthError("app_installation_revoked",
                                         _REVOKED_DETAIL) from e
            raise GithubAppAuthError(
                "github_rejected",
                f"GitHub returned HTTP {e.response.status_code} while minting "
                "the App installation token, but our own records show this "
                "installation is still active — likely transient. "
                "Retry shortly.") from e
        raise GithubAppAuthError(
            "github_rejected",
            f"GitHub returned HTTP {e.response.status_code} while minting "
            "the App installation token. Retry shortly.") from e
    except Exception as e:  # noqa: BLE001
        raise GithubAppAuthError(
            "github_rejected",
            "GitHub App token mint failed unexpectedly. Retry shortly."
        ) from e

    if not token:
        raise GithubAppAuthError("app_installation_revoked", _REVOKED_DETAIL)
    return token


async def get_repo_token_or_error(
    project: dict,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Non-raising wrapper for background/scanner callers:
    returns (token, error_code, error_detail). Still fail-closed —
    there is NO fallback auth; a None token means the operation must
    surface the code, not silently try something else."""
    try:
        return await get_repo_token(project), None, None
    except GithubAppAuthError as e:
        return None, e.code, e.detail


async def probe_pat_status(project: dict) -> dict:
    """Live GitHub HEAD probe: mint a token for `project` and check it
    actually works against the repo. Returns
    {"pat_status": str, "pat_last4": str|None}.

    2026-08-30 — factored out of routers/admin_bin.py's inline
    `_probe` (BIN Tracker, Section 1) so the new cross-user bulk-
    revoke connections table (routers/admin_bin.py's
    `/admin/github/connections`) uses the exact same probe semantics
    instead of a second, possibly-drifting copy. `admin_bin.py`'s own
    `bin_tracker_projects` probe is untouched — this is a new,
    additive helper, not a refactor of working code.
    """
    from services.http import ext_client

    pat, _auth_err, _ = await get_repo_token_or_error(project)
    pat_status = "missing"
    pat_last4 = None
    if pat:
        pat_last4 = pat[-4:]
        owner = (project.get("github_owner") or "").strip()
        repo = (project.get("github_repo") or "").strip()
        if owner and repo:
            try:
                async with ext_client("github", timeout=httpx.Timeout(4.0)) as c:
                    r = await c.head(
                        f"https://api.github.com/repos/{owner}/{repo}",
                        headers={
                            "Authorization": f"Bearer {pat}",
                            "Accept": "application/vnd.github+json",
                        },
                    )
                if r.status_code == 200:
                    pat_status = "valid"
                elif r.status_code == 401:
                    pat_status = "invalid"
                elif r.status_code == 404:
                    pat_status = "repo_not_found"
                else:
                    pat_status = f"http_{r.status_code}"
            except Exception:
                pat_status = "probe_error"
        else:
            pat_status = "no_repo"
    return {"pat_status": pat_status, "pat_last4": pat_last4}


__all__ = [
    "get_repo_token", "get_repo_token_or_error", "GithubAppAuthError",
    "probe_pat_status",
]
