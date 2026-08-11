"""
routers/user_rollback.py — Iter 366 · User-facing rollback (REAL, not staged)

Non-admin, per-loop rollback. A Pro/Team user who just shipped a loop
can revert THEIR OWN latest ship with one call — the actual git-revert
commit is created via `services/loop_rollback.run_rollback()` in a
background task (the SAME workhorse `POST /loop/{loop_id}/rollback`
already uses).

Endpoints:
  GET  /rollback/candidates          — user's last 5 unreverted ships
  POST /rollback/revert-last-ship    — fires a real GitHub revert on
                                        the most recent unreverted loop

Guards:
  - Auth required (`current_dev`).
  - Only the loop's own `user_id` can revert it.
  - Free/Starter tier locked — Pro/Team/founder only.
  - Loop must have a persisted `commit_sha` (recorded via
    `record_shipped_commit`) and `reverted=False`.

Iter 367 (audit fix) — Previously wrote to a `rollback_trigger`
collection expecting a "deployer daemon" to pick it up. No such
daemon exists; the endpoint returned 200-success while doing nothing.
Now calls `run_rollback()` directly so a real revert commit is created
on GitHub before the client can consider the request "done".
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Header

from cto_services.auth import current_dev
from cto_services.db import require_db

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
        # Iter 367 audit fix — was filtering `shipped: True` which no
        # loop_outcomes doc ever has (record_shipped_commit doesn't
        # write that field). The correct filter is unreverted rows —
        # every row in loop_outcomes IS a shipped commit by construction.
        async for d in db.loop_outcomes.find(
            {"user_id": user["user_id"],
             "reverted": {"$ne": True}},
            {"_id": 0, "loop_id": 1, "user_id": 1, "project_id": 1,
             "shipped_at": 1, "commit_sha": 1, "file_paths": 1,
             "repeat_touch": 1},
        ).sort("shipped_at", -1).limit(5):
            files = d.get("file_paths") or []
            out.append({
                "loop_id":     d.get("loop_id"),
                "project_id":  d.get("project_id"),
                "commit_sha":  d.get("commit_sha"),
                "summary":     (f"{len(files)} file(s) changed"
                                if files else "no files recorded"),
                "shipped_at":  d.get("shipped_at"),
                "repeat_touch": bool(d.get("repeat_touch")),
            })
    except Exception as e:
        logger.warning("rollback candidates query failed: %r", e)
    return {"candidates": out, "count": len(out)}


@router.post("/revert-last-ship")
async def revert_last_ship(
    bg: BackgroundTasks,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Reverts the user's MOST RECENT unreverted shipped loop by firing
    a REAL github_api_writer.revert_commit() via loop_rollback."""
    user = await current_dev(authorization)
    if not _tier_allowed(user.get("tier")) and not user.get("is_admin"):
        raise HTTPException(403, {
            "error": "rollback_locked", "tier": user.get("tier"),
            "message": "Rollback is a Pro/Team feature.",
        })
    db = require_db()

    # 1) Find latest unreverted ship for this user.
    latest = await db.loop_outcomes.find_one(
        {"user_id": user["user_id"], "reverted": {"$ne": True}},
        sort=[("shipped_at", -1)],
    )
    if not latest:
        raise HTTPException(404, {
            "error":   "no_recent_ship",
            "message": "No recently-shipped loop found to revert.",
        })

    loop_id    = latest.get("loop_id")
    project_id = latest.get("project_id")
    commit_sha = latest.get("commit_sha")
    if not (loop_id and project_id and commit_sha):
        raise HTTPException(500, {
            "error": "outcome_row_incomplete",
            "message": ("The most recent ship row is missing loop_id/"
                        "project_id/commit_sha — cannot resolve revert "
                        "context. Contact support."),
        })

    # 2) Idempotence — refuse if already rolled back or in flight on
    #    the loop_sessions doc.
    sess = await db.loop_sessions.find_one(
        {"loop_id": loop_id, "user_id": user["user_id"]},
    )
    if not sess:
        raise HTTPException(404, {
            "error":   "session_not_found",
            "message": ("Loop session missing — cannot rollback "
                        "without repo/PAT context."),
        })
    if sess.get("rollback_sha"):
        raise HTTPException(409, "Loop already rolled back")
    rb_status = sess.get("rollback_status")
    if rb_status in ("queued", "running"):
        raise HTTPException(409, "Rollback already in progress")
    if rb_status == "failed":
        raise HTTPException(
            409, "Previous rollback failed — manual intervention required",
        )

    # 3) Load project + PAT via the same helpers cto_projects uses so
    #    encryption + fallback rules stay consistent with /loop/{id}/rollback.
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user["user_id"]},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0},
    )
    if not proj:
        raise HTTPException(404, {
            "error":   "project_not_found",
            "message": "Parent project not found — cannot resolve repo.",
        })
    # 2026-02-11 · Phase 3b (Bug 2 fix) — get_repo_token unifies PAT
    # + github_app auth so App-installed projects can rollback too.
    from routers.cto_projects import _user_gh_token
    from services.pat_vault import get_repo_token
    user_token = await get_repo_token(proj) \
        or await _user_gh_token(user["user_id"])
    if not user_token:
        raise HTTPException(400, {
            "error":   "no_github_pat",
            "message": ("No GitHub PAT on file for this project — open "
                        "Projects → Edit and add one before rolling back."),
        })

    # 4) Stage state + fire the REAL rollback background task.
    import time as _time
    await db.loop_sessions.update_one(
        {"loop_id": loop_id},
        {"$set": {"rollback_status":     "queued",
                  "rollback_started_at": _time.time(),
                  "rollback_commit_sha": commit_sha,
                  "rollback_triggered_by": "user_revert_last_ship"}},
    )
    from services.loop_rollback import run_rollback_bg
    bg.add_task(
        run_rollback_bg,
        db=db, loop_id=loop_id, project=proj,
        commit_sha=commit_sha, user_token=user_token,
    )
    logger.info(
        "[user_rollback] queued real revert for loop=%s user=%s sha=%s",
        loop_id, user["user_id"], commit_sha,
    )
    return {
        "ok":               True,
        "loop_id":          loop_id,
        "project_id":       project_id,
        "commit_sha":       commit_sha,
        "rollback_status":  "queued",
        "poll_hint":        f"/loop/{loop_id}",
    }
