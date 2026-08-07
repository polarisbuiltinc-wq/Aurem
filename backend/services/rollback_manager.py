"""
services/rollback_manager.py — G12 · Founder-triggered rollback (REAL)

Founder-gated rollback used by /admin/qa/guard12-rollback/trigger.
Reverts a shipped loop's commit by SHA via the real
`services/loop_rollback.run_rollback()` workhorse.

Public API (called from routers/admin_qa.py):
  get_rollback_candidates(db) -> list[{sha, deployed_at, ...}]
    — recent successful deploys, most-recent first (from deploy_events).
  execute_rollback(db, target_sha, triggered_by, reason, bg) -> dict
    — resolves target_sha → loop_outcomes → user/loop/project context,
      then fires run_rollback() in the passed BackgroundTasks queue.
      Returns real status, NOT fake success.
  rollback_status(db) -> dict
    — read-only summary of the last rollback attempt for the guard
      dashboard.

Iter 367 (audit fix) — Previously wrote to a `rollback_trigger`
collection expecting a "deployer daemon" to pick it up. No such
daemon exists in the codebase; the endpoint returned 200 while
doing nothing. Now:
  • target_sha is resolved to a loop_outcomes row (must exist).
  • The row's user_id + loop_id + project_id + PAT drive a real
    github_api_writer.revert_commit() via loop_rollback.run_rollback().
  • If no loop maps to target_sha, we return 404-shape ({ok: False,
    reason: "sha_not_shipped"}) rather than a fake queued state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import BackgroundTasks

logger = logging.getLogger("aurem.rollback_manager")


async def get_rollback_candidates(db, limit: int = 10) -> List[dict]:
    """Recent successful platform deploys (from deploy_events) — the
    admin dashboard lists these so the operator can copy a SHA into
    the trigger endpoint."""
    if db is None:
        return []
    out: List[dict] = []
    try:
        async for d in db.deploy_events.find(
            {"status": "success"},
            {"_id": 0, "sha": 1, "commit_sha": 1, "created_at": 1,
             "job_id": 1, "status": 1},
        ).sort("created_at", -1).limit(limit):
            sha = d.get("commit_sha") or d.get("sha") or ""
            out.append({
                "sha":         sha[:12],
                "full_sha":    sha,
                "job_id":      d.get("job_id"),
                "deployed_at": d.get("created_at").isoformat()
                              if d.get("created_at") else None,
            })
    except Exception as e:
        logger.warning("[G12] rollback candidates query failed: %r", e)
    return out


async def execute_rollback(
    db,
    *,
    target_sha:    str,
    triggered_by:  str,
    reason:        Optional[str] = "",
    bg:            Optional[BackgroundTasks] = None,
) -> dict:
    """Resolve target_sha → loop context, then fire a real revert.

    Returns:
      {ok: True,  loop_id, commit_sha, rollback_status: "queued", ...}
        on successful staging (background task registered).
      {ok: False, reason: <str>, ...}
        with a real reason when no loop maps to the SHA or context
        cannot be resolved.
    """
    if db is None:
        return {"ok": False, "reason": "no_db"}
    tsha = (target_sha or "").strip()
    if not tsha or len(tsha) < 6:
        return {"ok": False, "reason": "invalid_sha"}

    # ── 1) Resolve target_sha → loop_outcomes row ─────────────────
    # The founder may pass a 7-char short SHA; loop_outcomes stores
    # full SHAs, so match by prefix.
    outcome = None
    try:
        outcome = await db.loop_outcomes.find_one(
            {"commit_sha": {"$regex": f"^{tsha}"},
             "reverted": {"$ne": True}},
            sort=[("shipped_at", -1)],
        )
    except Exception as e:
        return {"ok": False, "reason": "outcome_lookup_failed",
                "detail": str(e)[:200]}
    if not outcome:
        return {"ok": False, "reason": "sha_not_shipped",
                "target_sha": tsha,
                "hint": ("This SHA does not map to any shipped loop. "
                         "Platform-level (Emergent deploy pipeline) "
                         "rollback is not automatable from this "
                         "endpoint — contact Emergent Support.")}

    loop_id    = outcome.get("loop_id")
    user_id    = outcome.get("user_id")
    project_id = outcome.get("project_id")
    commit_sha = outcome.get("commit_sha")

    # ── 2) Load project + PAT ─────────────────────────────────────
    proj = None
    try:
        proj = await db.cto_projects.find_one(
            {"project_id": project_id, "user_id": user_id},
            {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
             "repo_index_blocks": 0, "last_commit_diff": 0},
        )
    except Exception as e:
        return {"ok": False, "reason": "project_lookup_failed",
                "detail": str(e)[:200]}
    if not proj:
        return {"ok": False, "reason": "project_not_found",
                "loop_id": loop_id, "user_id": user_id,
                "project_id": project_id}

    try:
        from routers.cto_projects import _decrypt_pat, _user_gh_token
        user_token = (await _decrypt_pat(user_id, proj.get("github_token"))
                      or await _user_gh_token(user_id))
    except Exception as e:
        return {"ok": False, "reason": "pat_resolve_failed",
                "detail": str(e)[:200]}
    if not user_token:
        return {"ok": False, "reason": "no_github_pat",
                "loop_id": loop_id, "user_id": user_id,
                "hint": "User has no GitHub PAT on file for this project."}

    # ── 3) Idempotence via loop_sessions.rollback_status ──────────
    try:
        sess = await db.loop_sessions.find_one({"loop_id": loop_id})
    except Exception:
        sess = None
    if sess and sess.get("rollback_sha"):
        return {"ok": False, "reason": "already_rolled_back",
                "loop_id": loop_id,
                "existing_revert_sha": sess.get("rollback_sha")}
    if sess and sess.get("rollback_status") in ("queued", "running"):
        return {"ok": False, "reason": "rollback_in_flight",
                "loop_id": loop_id,
                "current_status": sess.get("rollback_status")}

    # ── 4) Stage + fire the real background rollback ──────────────
    import time as _time
    try:
        await db.loop_sessions.update_one(
            {"loop_id": loop_id},
            {"$set": {"rollback_status":     "queued",
                      "rollback_started_at": _time.time(),
                      "rollback_commit_sha": commit_sha,
                      "rollback_triggered_by": (
                          f"admin_g12:{triggered_by}"),
                      "rollback_reason": (reason or "")[:400]}},
        )
    except Exception as e:
        return {"ok": False, "reason": "state_write_failed",
                "detail": str(e)[:200]}

    from services.loop_rollback import run_rollback, run_rollback_bg
    if bg is not None:
        bg.add_task(
            run_rollback_bg,
            db=db, loop_id=loop_id, project=proj,
            commit_sha=commit_sha, user_token=user_token,
        )
    else:
        # No BackgroundTasks passed → schedule via asyncio directly.
        # Every caller SHOULD pass bg, but this preserves the
        # invariant that we NEVER return ok=True without staging.
        # Uses raw `run_rollback` since asyncio.create_task surfaces
        # exceptions via `.exception()` — no need for safe_bg's swallow.
        import asyncio
        asyncio.create_task(
            run_rollback(
                db=db, loop_id=loop_id, project=proj,
                commit_sha=commit_sha, user_token=user_token,
            ),
            name=f"g12-rollback:{loop_id}",
        )

    logger.warning(
        "[G12] real rollback staged: loop=%s sha=%s by=%s reason=%s",
        loop_id, commit_sha, triggered_by, reason,
    )
    # Fire a founder alert so nobody misses the manual rollback.
    try:
        from services.founder_alerts import send_founder_alert
        await send_founder_alert(
            db,
            source_key=f"rollback:{commit_sha[:12]}",
            title=f"Rollback triggered for loop {loop_id}",
            detail=(f"Reverting {commit_sha[:12]}. "
                    f"Triggered by {triggered_by}. "
                    f"Reason: {reason or 'not specified'}."),
            level="critical", guard="G12",
        )
    except Exception:
        pass
    return {
        "ok":              True,
        "loop_id":         loop_id,
        "user_id":         user_id,
        "project_id":      project_id,
        "commit_sha":      commit_sha,
        "rollback_status": "queued",
        "poll_hint":       f"/loop/{loop_id}",
        "created_at":      datetime.now(timezone.utc).isoformat(),
    }


async def rollback_status(db) -> dict:
    """Dashboard read: most-recent rollback attempt across all users.
    Reads loop_sessions since rollback_trigger is deprecated."""
    if db is None:
        return {"available": False}
    try:
        last = await db.loop_sessions.find_one(
            {"rollback_status": {"$exists": True}},
            sort=[("rollback_started_at", -1)],
        )
        if not last:
            return {"available": True, "last_rollback": None}
        return {
            "available": True,
            "last_rollback": {
                "loop_id":        last.get("loop_id"),
                "commit_sha":     last.get("rollback_commit_sha"),
                "status":         last.get("rollback_status"),
                "revert_sha":     last.get("rollback_sha"),
                "triggered_by":   last.get("rollback_triggered_by"),
                "started_at":     last.get("rollback_started_at"),
                "completed_at":   last.get("rollback_completed_at"),
                "error":          last.get("rollback_error"),
            },
        }
    except Exception as e:
        return {"available": False, "error": str(e)[:200]}
