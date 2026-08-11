"""
services/pat_vault.py — Iter 212m-230 (canonical implementation)

Centralised PAT (Personal Access Token) helpers.  Previously this
module was a router-side shim that delegated back to
`routers.cto_projects` — creating the very cycle
`services/bin_context → services/pat_vault → routers/cto_projects → …`
that `architecture_health` was flagging.

Iter 212m-230 folds the canonical `_decrypt_pat`, `_encrypt_pat` and
`_user_gh_token` implementations HERE (services/ layer, single source
of truth).  `routers/cto_projects.py` now re-exports them for backward
compatibility, so downstream call-sites don't need to change.

Public API
==========
    async decrypt_pat(user_id, token)  -> str | None
    async encrypt_pat(user_id, token)  -> str | None
    async get_user_gh_token(user_id)   -> str | None

Legacy shim names (still exported for router-side callers):
    _decrypt_pat, _encrypt_pat, _user_gh_token
"""

from __future__ import annotations

from typing import Optional


async def _user_gh_token(user_id: str) -> Optional[str]:
    """Return the OAuth-based GitHub access token stored on
    `dev_users[<user_id>].github.access_token`.  Returns None when the
    user hasn't connected GitHub or the DB is unreachable."""
    # Deferred import: services/ layer must never import from routers/
    # at module scope — routers pull services in, not the other way
    # around.  `get_db` is defined in a stateless helper module.
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return None
    u = await db.dev_users.find_one({"user_id": user_id}, {"_id": 0, "github": 1})
    return ((u or {}).get("github") or {}).get("access_token")


async def _encrypt_pat(user_id: str, token: Optional[str]) -> Optional[str]:
    """Encrypt `token` at rest with per-user HKDF-Fernet via
    `services.vault`.  Idempotent — a `v1:`-prefixed ciphertext is
    passed through untouched.  Fails open (returns the plaintext) so
    project creation never blocks on a crypto outage."""
    if not token:
        return token
    if token.startswith("v1:"):
        return token   # already encrypted
    try:
        from services.vault import encrypt, is_vault_available
        if not is_vault_available():
            return token
        return await encrypt(user_id, token, kind="github_token")
    except Exception:
        return token   # fail-open: never block project creation on crypto


async def _decrypt_pat(user_id: str, token: Optional[str]) -> Optional[str]:
    """Decrypt a stored PAT ciphertext for `user_id`.  Returns:
      - the plaintext PAT on success,
      - the original string on legacy (pre-encryption) rows,
      - None on empty input or tamper/wrong-user failure.

    Never raises — every call site can rely on a str | None contract.
    """
    if not token:
        return token
    if not token.startswith("v1:"):
        return token   # legacy plaintext — pass through
    try:
        from services.vault import decrypt
        return await decrypt(user_id, token, kind="github_token")
    except Exception:
        return None    # tamper / wrong user → treat as missing token


# ── Public (non-underscore) API ─────────────────────────────────────
# Preferred name for new callers.  Retained the underscored aliases
# above for backwards compatibility with existing router code that
# still writes `_decrypt_pat` explicitly.
decrypt_pat        = _decrypt_pat
encrypt_pat        = _encrypt_pat
get_user_gh_token  = _user_gh_token


# ═══════════════════════════════════════════════════════════════════
# 2026-02-10 · Phase 3a — dual-auth repo-token resolver
# ═══════════════════════════════════════════════════════════════════
async def get_repo_token(project: dict) -> Optional[str]:
    """Return a valid GitHub token for the given `cto_projects` row.

    Dispatch by `project.auth_method`:
      * "github_app"                  → mint a fresh short-lived
                                        installation access token via
                                        `services.github_app.get_installation_token`.
                                        NEVER persisted; the caller uses
                                        it once and discards.
      * "pat" | missing               → decrypt the stored PAT
                                        (`project.github_token`) via
                                        `_decrypt_pat`. Legacy rows with
                                        no `auth_method` field fall
                                        through here (recommended-default
                                        per Phase 3 decision #2).

    Returns:
      * a non-empty token string on success
      * `None` when the project row is genuinely unconfigured for either
        path (empty PAT AND no installation_id). Callers preserve their
        existing "fallback to `_user_gh_token()` for legacy projects
        without a per-project PAT" semantics — this function only
        replaces the primary lookup.

    NEVER raises — every caller relies on the `str | None` contract to
    fall through to the `or await _user_gh_token(...)` fallback safely.
    """
    if not project:
        return None

    method = (project.get("auth_method") or "pat").lower()

    if method == "github_app":
        iid = project.get("installation_id")
        if not iid:
            # Malformed row — logged but non-raising so the caller
            # falls through to its existing `_user_gh_token` fallback
            # rather than a hard 500. Real production writes always
            # set installation_id when auth_method="github_app" (see
            # /projects/add gate rewrite), so this branch is defensive
            # only.
            return None
        try:
            from services.github_app import get_installation_token
            token, _expires_at = await get_installation_token(int(iid))
            return token or None
        except Exception:
            # Installation revoked, App misconfigured, GitHub 5xx, etc.
            # Return None so caller falls through to org-token fallback
            # rather than crashing the request. Caller sees the same
            # semantics as an unauthenticated request would produce.
            return None

    # Default path — PAT (explicit or legacy).
    return await _decrypt_pat(
        project.get("user_id") or "", project.get("github_token"),
    )


__all__ = [
    "decrypt_pat", "encrypt_pat", "get_user_gh_token",
    "_decrypt_pat", "_encrypt_pat", "_user_gh_token",
    "get_repo_token",
]
