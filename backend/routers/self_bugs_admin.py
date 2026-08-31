"""
routers/self_bugs_admin.py — Item 3 (2026-08-31)

READ-ONLY admin dashboard for `services/self_bug.py`'s structured
self-bug ledger (`ora_self_bugs`) + the learned-recurrence counter
(`self_bug_learned`). Founder-gated via `require_admin`, same pattern
as routers/suggestions.py's admin endpoints. No mutate/delete route —
this is deliberately read-only; the ledger itself is the audit trail.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header

from cto_services.auth import require_admin
from cto_services.db import require_db
from services.self_bug import SELF_BUG_TYPES, signature

router = APIRouter(prefix="/admin/self-bugs", tags=["Admin — Self-Repair"])


@router.get("/list")
async def list_self_bugs(
    type: Optional[str] = None,
    limit: int = 200,
    authorization: Optional[str] = Header(None),
):
    """Read-only listing, sorted by recurrence (times_seen) desc so
    the "this keeps happening" bugs bubble to the top — the whole
    point of P7's learned-recurrence counter. `type` optionally
    filters to one of SELF_BUG_TYPES."""
    await require_admin(authorization)
    db = require_db()
    q: dict = {}
    if type in SELF_BUG_TYPES:
        q["type"] = type
    limit = max(1, min(limit, 500))

    learned_by_sig: dict[str, dict] = {}
    async for doc in db.self_bug_learned.find({}, {"_id": 0}):
        learned_by_sig[doc.get("signature", "")] = doc

    rows = []
    async for r in db.ora_self_bugs.find(q, {"_id": 0}).sort("ts", -1).limit(limit):
        sig = signature(r.get("type", ""), r.get("context") or {})
        learned = learned_by_sig.get(sig, {})
        ts = r.get("ts")
        rows.append({
            "type":             r.get("type"),
            "source":           r.get("source"),
            "what_user_saw":    r.get("what_user_saw"),
            "likely_cause":     r.get("likely_cause"),
            "confidence":       r.get("confidence"),
            "severity":         r.get("severity"),
            "proposed_fix":     r.get("proposed_fix"),
            "signature":        sig,
            "times_seen":       learned.get("times_seen", 1),
            "last_seen":        (
                datetime.fromtimestamp(learned["last_seen"], tz=timezone.utc).isoformat()
                if learned.get("last_seen") else None
            ),
            "ts":               (
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
            ),
        })
    # Recurring bugs bubble up first (times_seen desc); most-recent
    # breaks ties (stable sort: sort by ts desc first, then by
    # times_seen desc — ties keep their ts-desc relative order).
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    rows.sort(key=lambda r: r["times_seen"], reverse=True)
    return {"self_bugs": rows, "count": len(rows), "types": sorted(SELF_BUG_TYPES)}
