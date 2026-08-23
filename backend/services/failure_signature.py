"""services/failure_signature.py — 2026-08-25.

Detects when a CTO task fails with the SAME signature repeatedly on
the same project. A repeated identical signature means the failure is
deterministic (a real code/config bug) rather than a transient blip —
a blind Retry can never fix it, only a change to the task/code will.

Scope note (founder-approved, 2026-08-25): this is PREVIEW-ONLY
detection for now. Running this fleet-wide against Production requires
a read-only admin endpoint (mirroring existing `/admin/*` patterns),
NOT direct DB access — that is a separate, explicitly-approved step.

Best-effort throughout — bookkeeping here must never fail a task.
"""
from __future__ import annotations

import hashlib
import re
import time
import logging

logger = logging.getLogger(__name__)

_HEX_RE = re.compile(r"\b[0-9a-f]{6,}\b", re.I)
_WS_RE = re.compile(r"\s+")


def compute_signature(project_id: str, task_description: str,
                      error_category: str, error_text: str) -> str:
    """Stable short hash for (project, task, failure shape).

    Normalises away variable content (hex ids/SHAs, whitespace, case)
    so the SAME underlying bug hashes identically across retries even
    though timestamps/task_ids differ each time.
    """
    norm_task = _WS_RE.sub(" ", (task_description or "").strip().lower())[:200]
    norm_err = _HEX_RE.sub("#", (error_text or "")[:200].lower())
    norm_err = _WS_RE.sub(" ", norm_err)
    raw = f"{project_id}|{norm_task}|{error_category}|{norm_err}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


async def record_and_check(db, *, project_id: str, signature: str) -> dict:
    """Upsert the signature counter. Returns {repeat_count}. Never raises."""
    if db is None:
        return {"repeat_count": 1}
    try:
        from pymongo import ReturnDocument
        now = time.time()
        doc = await db.task_failure_signatures.find_one_and_update(
            {"project_id": project_id, "signature": signature},
            {
                "$inc": {"repeat_count": 1},
                "$set": {"last_seen": now},
                "$setOnInsert": {"first_seen": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0, "repeat_count": 1},
        )
        return {"repeat_count": (doc or {}).get("repeat_count", 1)}
    except Exception as e:                                    # noqa: BLE001
        logger.warning("failure_signature record_and_check best-effort failure: %r", e)
        return {"repeat_count": 1}


async def reset(db, *, project_id: str, signature: str) -> None:
    """Best-effort clear on a successful retry with the same shape (not
    currently wired — reserved for when checkpointed-retry lands)."""
    if db is None:
        return
    try:
        await db.task_failure_signatures.delete_one(
            {"project_id": project_id, "signature": signature})
    except Exception as e:                                    # noqa: BLE001
        logger.warning("failure_signature reset best-effort failure: %r", e)
