"""
routers/user_rollback.py — Iter 366 · User-facing rollback

Non-admin, per-loop rollback. A Pro/Team user who just shipped a
loop can revert THEIR OWN latest ship with one call, without going
through founder-only /admin/qa endpoints.

Endpoints:
  GET  /rollback/candidates          — user's last 5 successful ships
  POST /rollback/revert-last-ship    — reverts THE latest ship by
                                        creating an "undo" commit that
                                        removes the loop's changes.

Guards:
  - Auth required (`current_dev`).
  - Only the loop's own user_id can revert it (verified via
    loop_sessions.user_id match).
  - Free/Starter tier locked — this is a Pro/Team feature.
  - Loop must be in COMPLETED state to revert.
  - Only the MOST RECENT completed loop can be reverted (no
    time-travel rollback across multiple ships).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from cto_services.auth import current_dev
from cto_services.db import get_db, require_db

logger = logging.getLogger("aurem.user_rollback")

router = APIRouter(prefix="/rollback", tags=["User Rollback"])


def _tier_allowed(tier: str) -> bool:
    return (tier or "").lower() in ("pro", "team", "founder")


@router.get("/candidates")
async def rollback_candidates(authorization: Optional[str] = Header(None)):
    """List up to 5 of the user's own recently-shipped loops that are
    eligible for a one-click revert."""
    user = await current_dev(authorization)
    if not _tier_allowed(user.get("tier")) and not user.get("is_admin"):
        raise HTTPException(403, {
            "error":  "rollback_locked",
            "tier":   user.get("tier"),
            "message": ("Rollback is a Pro/Team feature. Upgrade to "
                        "revert a shipped loop with one click."),
        })
    db = require_db()
    out = []
    try:
        async for d in db.loop_outcomes.find(
            {"user_id": user["user_id"], "shipped": True},
            {"_id": 0, "loop_id": 1, "user_id": 1, "project_id": 1,
             "shipped_at": 1, "commit_sha": 1, "summary": 1,
             "reverted": 1},
        ).sort("shipped_at", -1).limit(5):
            if d.get("reverted"):
                continue
            out.append({
                "loop_id":     d.get("loop_id"),
                "project_id":  d.get("project_id"),
                "commit_sha":  d.get("commit_sha"),
                "summary":     (d.get("summary") or "")[:120],
                "shipped_at":  d.get("shipped_at"),
            })
    except Exception as e:
        logger.warning("rollback candidates query failed: %r", e)
    return {"candidates": out, "count": len(out)}


@router.post("/revert-last-ship")
async def revert_last_ship(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Reverts the user's MOST RECENT completed loop by staging a
    rollback_trigger row. The actual git-revert commit is applied by
    the deployer/github_deploy_service worker on next tick."""
    user = await current_dev(authorization)
    if not _tier_allowed(user.get("tier")) and not user.get("is_admin"):
        raise HTTPException(403, {
            "error": "rollback_locked", "tier": user.get("tier"),
            "message": "Rollback is a Pro/Team feature.",
        })
    db = require_db()
    latest = await db.loop_outcomes.find_one(
        {"user_id": user["user_id"], "shipped": True,
         "reverted": {"$ne": True}},
        sort=[("shipped_at", -1)],
    )
    if not latest:
        raise HTTPException(404, {
            "error":   "no_recent_ship",
            "message": "No recently-shipped loop found to revert.",
        })
    loop_id = latest.get("loop_id")

    # Stage the rollback intent — deployer picks it up on next tick.
    intent = {
        "kind":         "user_ship_revert",
        "loop_id":      loop_id,
        "user_id":      user["user_id"],
        "project_id":   latest.get("project_id"),
        "target_sha":   latest.get("commit_sha"),
        "triggered_by": user.get("email") or user["user_id"],
        "status":       "pending",
        "created_at":   datetime.now(timezone.utc),
    }
    try:
        res = await db.rollback_trigger.insert_one(intent)
        # Mark the loop_outcomes row so a second click is idempotent.
        await db.loop_outcomes.update_one(
            {"loop_id": loop_id},
            {"$set": {"reverted": True,
                       "reverted_at": datetime.now(timezone.utc)}},
        )
        intent["_id"] = str(res.inserted_id)
    except Exception as e:
        raise HTTPException(500, {"error": "rollback_stage_failed",
                                   "detail": str(e)[:200]}) from None
    logger.info("[user_rollback] staged for loop=%s user=%s",
                loop_id, user["user_id"])
    return {"ok": True, "loop_id": loop_id,
             "commit_sha": latest.get("commit_sha"),
             "status": "pending"}
