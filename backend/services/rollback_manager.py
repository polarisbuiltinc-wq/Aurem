"""
services/rollback_manager.py — G12 · One-click rollback (Iter 366)

Founder-gated endpoint that reverts a live deploy to the previous
known-good SHA using existing `deploy_logger` events.

Public API (called from routers/admin.py):
  get_rollback_candidates(db) -> list[{sha, deployed_at, status}]
    — recent successful deploys, most-recent first.
  execute_rollback(db, target_sha) -> {ok, ...}
    — writes a `rollback_trigger` row so the deployer daemon /
      hosted-deploy scheduler picks it up on the next tick. The
      actual container flip is driven by Emergent's deployer; we
      only stage the intent + audit trail.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger("aurem.rollback_manager")


async def get_rollback_candidates(db, limit: int = 10) -> List[dict]:
    if db is None:
        return []
    out: List[dict] = []
    try:
        async for d in db.deploy_events.find(
            {"status": "success"},
            {"_id": 0, "sha": 1, "created_at": 1, "job_id": 1,
             "status": 1},
        ).sort("created_at", -1).limit(limit):
            out.append({
                "sha":         (d.get("sha") or "")[:12],
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
) -> dict:
    """Persist a rollback intent. Actual container swap is driven by
    the deployer daemon reading rollback_trigger rows."""
    if db is None:
        return {"ok": False, "reason": "no_db"}
    if not target_sha or len(target_sha) < 6:
        return {"ok": False, "reason": "invalid_sha"}
    # Confirm the target sha exists in deploy history.
    exists = await db.deploy_events.find_one(
        {"sha": {"$regex": f"^{target_sha}"}, "status": "success"},
        {"_id": 0, "sha": 1, "created_at": 1},
    )
    if not exists:
        return {"ok": False, "reason": "sha_not_in_deploy_history",
                "target_sha": target_sha}
    doc = {
        "target_sha":   target_sha,
        "full_sha":     exists.get("sha"),
        "triggered_by": triggered_by,
        "reason":       (reason or "")[:400],
        "status":       "pending",
        "created_at":   datetime.now(timezone.utc),
        "created_ts":   time.time(),
    }
    try:
        res = await db.rollback_trigger.insert_one(doc)
        doc["_id"] = str(res.inserted_id)
    except Exception as e:
        return {"ok": False, "reason": "db_write_failed", "err": str(e)[:200]}
    logger.warning(
        "[G12] rollback staged: sha=%s by=%s reason=%s",
        target_sha, triggered_by, reason,
    )
    # Fire a founder alert so nobody misses the manual rollback.
    try:
        from services.founder_alerts import send_founder_alert
        await send_founder_alert(
            db,
            source_key=f"rollback:{target_sha}",
            title=f"Rollback triggered to {target_sha}",
            detail=(f"Triggered by {triggered_by}. "
                    f"Reason: {reason or 'not specified'}. "
                    "Deployer daemon will pick this up on the next tick."),
            level="critical", guard="G12",
        )
    except Exception:
        pass
    return {"ok": True, **doc}


async def rollback_status(db) -> dict:
    if db is None:
        return {"available": False}
    try:
        last = await db.rollback_trigger.find_one(
            {}, sort=[("created_at", -1)],
        )
        return {
            "available":     True,
            "last_rollback": last and {
                "target_sha":   last.get("target_sha"),
                "status":       last.get("status"),
                "triggered_by": last.get("triggered_by"),
                "created_at":   last.get("created_at").isoformat()
                              if last.get("created_at") else None,
            },
        }
    except Exception as e:
        return {"available": False, "error": str(e)[:200]}
