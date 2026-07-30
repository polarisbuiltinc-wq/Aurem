"""
services/scope_drift_guard.py — G3 · Scope-drift hard block (Iter 366)

At loop execute time, real file-write attempts get filtered through
`assert_write_allowed()`:
  - If path is outside `plan.files_to_change` → BLOCK + fail loop
  - If path matches any PROTECTED_PATH pattern → BLOCK unless the
    task spec explicitly names it AND trust >= L2 manual ship gate

Blocked writes are persisted to `loop_scope_blocks` for the /admin/qa
row and for Guard 20 incident correlation.
"""
from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

logger = logging.getLogger("aurem.scope_drift_guard")


# Paths a Loop write MUST NOT touch — auth core, payments, secrets,
# migrations, admin routers, CI workflows.
PROTECTED_PATH_PATTERNS = tuple(re.compile(p) for p in (
    r"backend/routers/admin.*\.py$",
    r"backend/routers/payments\.py$",
    r"backend/routers/auth\.py$",
    r"backend/routers/mcp\.py$",
    r"backend/services/vault.*\.py$",
    r"backend/services/stripe.*\.py$",
    r"backend/services/founder_alerts\.py$",
    r"backend/services/llm_cost_breaker\.py$",
    r"backend/services/scope_drift_guard\.py$",
    r"backend/services/db_indexes\.py$",
    r"backend/services/incident_log\.py$",
    r"backend/services/process_recovery\.py$",
    r"backend/services/retry_guard\.py$",
    r"backend/services/signup_guards\.py$",
    r"backend/services/subscription_tiers\.py$",
    r"backend/services/loop_beta\.py$",
    r"backend/main\.py$",
    r"\.env$", r"\.env\..*$",
    r"\.github/workflows/.*",
    r"backend/migrations/.*",
))


def _is_protected(path: str) -> bool:
    return any(rx.search(path) for rx in PROTECTED_PATH_PATTERNS)


async def _log_block(
    db, *, loop_id: str, path: str, reason: str,
    planner_files: Iterable[str] | None = None,
) -> None:
    if db is None:
        return
    try:
        await db.loop_scope_blocks.insert_one({
            "loop_id":         loop_id,
            "path":            path,
            "reason":          reason,
            "planner_files":   list(planner_files or []),
            "created_at":      datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.debug("[G3] loop_scope_blocks write failed: %r", e)


async def assert_write_allowed(
    db,
    *,
    loop_id: str,
    path: str,
    planner_files: Iterable[str] | None = None,
    trust_level: int = 1,
) -> None:
    """Raise ValueError(scope_drift_blocked) if the write is illegal.

    trust_level: 0=default, 1=user_confirmed, 2=founder-manually-approved.
    Only trust_level >= 2 unlocks a PROTECTED_PATH write, and only when
    that path is explicitly in planner_files."""
    planner = set(planner_files or [])

    # Normalise for comparison.
    p = (path or "").lstrip("./")

    # 1) Protected paths.
    if _is_protected(p):
        if trust_level < 2 or p not in planner:
            await _log_block(db, loop_id=loop_id, path=p,
                             reason="protected_path",
                             planner_files=planner)
            raise ValueError(
                f"scope_drift_blocked: {p} is on the PROTECTED path "
                f"list; requires manual founder ship gate."
            )

    # 2) Out-of-scope write (only enforced if planner declared files).
    if planner and p not in planner:
        await _log_block(db, loop_id=loop_id, path=p,
                         reason="out_of_scope",
                         planner_files=planner)
        raise ValueError(
            f"scope_drift_blocked: {p} not in planner-declared "
            f"files_to_change {sorted(planner)}"
        )


async def get_scope_block_stats(db, window_days: int = 7) -> dict:
    """QA row payload."""
    if db is None:
        return {"available": False}
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    try:
        n_total = await db.loop_scope_blocks.count_documents(
            {"created_at": {"$gte": since}}
        )
        pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {"_id": "$reason", "n": {"$sum": 1}}},
        ]
        by_reason = {}
        async for row in db.loop_scope_blocks.aggregate(pipeline):
            by_reason[row["_id"] or "?"] = int(row["n"])
        last = await db.loop_scope_blocks.find_one(
            {}, sort=[("created_at", -1)],
        )
        return {
            "available":       True,
            "window_days":     window_days,
            "blocks_in_window": n_total,
            "by_reason":       by_reason,
            "last_block":      last and {
                "loop_id":    last.get("loop_id"),
                "path":       last.get("path"),
                "reason":     last.get("reason"),
                "created_at": last.get("created_at").isoformat()
                              if last.get("created_at") else None,
            },
        }
    except Exception as e:
        return {"available": False, "error": str(e)[:200]}
