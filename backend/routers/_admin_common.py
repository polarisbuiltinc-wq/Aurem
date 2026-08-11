"""_admin_common.py — shared helpers used by every admin sub-router.

Extracted from routers/admin.py during the Phase 2 split (2026-02-11)
so each sub-router can import `_require_admin` without duplicating
the stale-JWT-escape-hatch pattern.

The router-level `dependencies=[Depends(require_admin_dep)]` gate
runs BEFORE the handler body, so `_require_admin` here is used
inside handler bodies purely as a defense-in-depth pattern that
also returns the live `dev_users` claim shape for callers that
need `is_unlimited`, `tier`, or the refreshed email.

Copied verbatim from the pre-split admin.py:50-69.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from cto_services.auth import current_dev
from cto_services.db import get_db


async def _require_admin(authorization: Optional[str]) -> dict:
    user = await current_dev(authorization)
    if user.get("is_admin"):
        return user
    # Stale-JWT escape hatch: the JWT might be from before the user was
    # promoted (e.g. founder allow-list added after their last login).
    # Trust the live DB row over the cached claim.
    db = get_db()
    if db is not None:
        row = await db.dev_users.find_one(
            {"user_id": user.get("user_id")},
            {"is_admin": 1, "tier": 1, "email": 1, "is_unlimited": 1},
        )
        if row and (row.get("is_admin") or row.get("tier") == "founder"):
            user["is_admin"] = True
            user["tier"] = row.get("tier") or user.get("tier")
            user["is_unlimited"] = bool(row.get("is_unlimited"))
            user["email"] = row.get("email") or user.get("email")
            return user
    raise HTTPException(403, "Admin access required")


__all__ = ["_require_admin"]
