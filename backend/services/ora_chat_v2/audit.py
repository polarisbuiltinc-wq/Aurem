"""
services/ora_chat_v2/audit.py — Admin ORA Chat rebuild, P4.

`ora_chat_actions` collection: every propose/approve/reject/execute/
fail event, logged. This is the audit trail the "Recent ORA actions"
list on the chat page reads (read-only).
"""
from __future__ import annotations

import time
import uuid


async def log_event(db, *, admin_id: str, action_id: str, params: dict,
                     proposed_by: str, event_type: str,
                     proposal_id: str | None = None,
                     approved_ts: float | None = None,
                     result: dict | None = None,
                     error: str | None = None) -> str:
    """event_type in (proposed, approved, rejected, executed, failed)."""
    proposal_id = proposal_id or uuid.uuid4().hex[:12]
    await db.ora_chat_actions.insert_one({
        "ts": time.time(),
        "admin_id": admin_id,
        "action_id": action_id,
        "params": params or {},
        "proposed_by": proposed_by,
        "event_type": event_type,
        "proposal_id": proposal_id,
        "approved_ts": approved_ts,
        "result": result,
        "error": error,
    })
    return proposal_id


async def recent_actions(db, limit: int = 20) -> list:
    cur = db.ora_chat_actions.find({}, {"_id": 0}).sort("ts", -1).limit(limit)
    return [row async for row in cur]


async def get_proposal(db, proposal_id: str) -> dict | None:
    """Latest event for a proposal_id (used to fetch the pending
    action_id/params when approving/rejecting)."""
    cur = db.ora_chat_actions.find(
        {"proposal_id": proposal_id}, {"_id": 0}
    ).sort("ts", 1)
    rows = [r async for r in cur]
    if not rows:
        return None
    merged = dict(rows[0])
    for r in rows[1:]:
        merged["event_type"] = r["event_type"]
    return merged
