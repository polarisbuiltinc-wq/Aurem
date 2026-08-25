"""admin_support.py — Support tickets, error triage, admin alerts.

Extracted from routers/admin.py during the Phase 2 architecture split
(2026-02-11). Contains 9 handler(s) + helper(s):

  GET  /admin/support           GET  /admin/errors           GET  /admin/alerts
  POST /admin/support/{ticket_id}/reply       POST /admin/errors/{error_id}/autofix
  POST /admin/support/{ticket_id}/resolve     POST /admin/errors/{error_id}/resolve
  POST /admin/alerts/{alert_id}/dismiss

Every handler + helper is COPIED VERBATIM from the pre-split admin.py.
"""
from __future__ import annotations

import logging
import os
import asyncio
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Depends
from pydantic import BaseModel

from cto_services.auth import current_dev, require_admin_dep
from cto_services.db import get_db, require_db
from services.usage import get_usage
# Iter 212m-71 — 60 s TTL cache for the heavy admin aggregations
# (activation funnel, dev_users buckets, etc.). Founders click around
# the admin panel rapidly; without this every click fires 5+ heavy
# aggregations against Mongo.
from services.admin_analytics_cache import (
    cached_agg,
    invalidate as _cache_invalidate,
    mongo_swr_cache,
)
from services.admin_error_autofix import run_error_autofix

logger = logging.getLogger(__name__)
# Iter 358 — router-level admin gate (defense-in-depth). EVERY route on
# this router is denied to non-founders at the router boundary, so a new
# endpoint added later is protected by default. Individual handlers keep
# their inline `await _require_admin(...)` too (harmless redundancy).
# The one intentionally-public sink (/admin/errors/report) lives on the
# separate, un-gated routers/admin_public.py at the same URL.

router = APIRouter(
    prefix="/admin", tags=["Admin-support"],
    dependencies=[Depends(require_admin_dep)],
)

from routers._admin_common import _require_admin  # noqa: E402


@router.get("/support")
async def list_support_tickets(
    status: str = "",
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    q: dict = {}
    if status:
        q["status"] = status
    tickets = await db.cto_support.find(q, {"_id": 0}).sort(
        "updated_at", -1
    ).limit(100).to_list(100)
    # Iter 212m-70 — N+1 fix. Was a find() per ticket. Now: one $in
    # query pulls every message for every ticket on the page, then we
    # bucket them in Python. cto_support_messages already has the
    # [(ticket_id, 1), (ts, 1)] compound index, so the single batch
    # query hits the index and returns sorted.
    ticket_ids = [t.get("ticket_id") for t in tickets if t.get("ticket_id")]
    msgs_by_ticket: dict[str, list[dict]] = {tid: [] for tid in ticket_ids}
    if ticket_ids:
        cur = db.cto_support_messages.find(
            {"ticket_id": {"$in": ticket_ids}}, {"_id": 0},
        ).sort([("ticket_id", 1), ("ts", 1)])
        # 200 messages × 100 tickets ceiling — same per-ticket cap as
        # the legacy loop, just batched.
        async for m in cur.limit(20_000):
            tid = m.get("ticket_id")
            if tid in msgs_by_ticket and len(msgs_by_ticket[tid]) < 200:
                msgs_by_ticket[tid].append(m)
    for t in tickets:
        t["messages"] = msgs_by_ticket.get(t.get("ticket_id"), [])
    return {"tickets": tickets}


class SupportReply(BaseModel):
    message: str


@router.post("/support/{ticket_id}/reply")
async def admin_reply(
    ticket_id: str,
    body: SupportReply,
    authorization: Optional[str] = Header(None),
):
    user = await _require_admin(authorization)
    db = require_db()
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(400, "Empty message")
    now = time.time()
    await db.cto_support_messages.insert_one({
        "ticket_id": ticket_id,
        "sender": "admin",
        "admin_email": user.get("email"),
        "message": msg,
        "ts": now,
    })
    r = await db.cto_support.update_one(
        {"ticket_id": ticket_id},
        {"$set": {"status": "pending_user", "updated_at": now, "last_reply_at": now}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Ticket not found")

    # Iter 388u · Support Reply UX Fix (Option A) — Fire admin-reply
    # notification email so the user actually sees the reply. Was a
    # black hole until now: reply went into Mongo, user never got
    # anything. Best-effort: failure logged, never rolls back the
    # reply which is already durable.
    ticket = await db.cto_support.find_one(
        {"ticket_id": ticket_id},
        {"_id": 0, "user_email": 1, "user_name": 1},
    )
    if ticket and ticket.get("user_email"):
        try:
            from services.support_email import send_reply_notification
            ok, err = await send_reply_notification(
                user_email=ticket["user_email"],
                user_name=ticket.get("user_name"),
                ticket_id=ticket_id,
                admin_message=msg,
            )
            if not ok:
                logger.warning(
                    "support_email: notification failed ticket=%s err=%s",
                    ticket_id, err,
                )
            return {"ok": True, "email_notified": ok,
                    "email_error": err if not ok else None}
        except Exception as _e:  # noqa: BLE001
            logger.warning("support_email: dispatch crashed ticket=%s err=%r",
                           ticket_id, _e)
            return {"ok": True, "email_notified": False,
                    "email_error": str(_e)[:200]}
    return {"ok": True, "email_notified": False,
            "email_error": "no user_email on ticket"}


@router.post("/support/{ticket_id}/resolve")
async def admin_resolve(
    ticket_id: str,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    r = await db.cto_support.update_one(
        {"ticket_id": ticket_id},
        {"$set": {"status": "resolved", "resolved_at": time.time(),
                  "updated_at": time.time()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Ticket not found")
    return {"ok": True, "status": "resolved"}


@router.get("/alerts")
async def list_alerts(
    status: str = "active",
    authorization: Optional[str] = Header(None),
):
    """List integration top-up alerts. `status` filter accepts
    `active` (default), `resolved`, `dismissed`, or `all`."""
    await _require_admin(authorization)
    db = require_db()
    query: dict = {}
    if status != "all":
        query["status"] = status
    rows = await db.topup_alerts.find(
        query, {"_id": 0}
    ).sort("first_seen", -1).limit(100).to_list(100)
    counts = {
        "active":    await db.topup_alerts.count_documents({"status": "active"}),
        "critical":  await db.topup_alerts.count_documents(
            {"status": "active", "severity": "critical"}
        ),
        "warning":   await db.topup_alerts.count_documents(
            {"status": "active", "severity": "warning"}
        ),
    }
    return {"alerts": rows, "counts": counts}


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(
    alert_id: str,
    authorization: Optional[str] = Header(None),
):
    """Manually dismiss an alert — admin acknowledged + actioned. Does
    NOT prevent the same alert from firing again tomorrow if the
    integration is still in the same state (the dedupe key is per-day)."""
    await _require_admin(authorization)
    db = require_db()
    r = await db.topup_alerts.update_one(
        {"alert_id": alert_id},
        {"$set": {"status": "dismissed", "dismissed_at": time.time()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Alert not found")
    return {"ok": True, "alert_id": alert_id, "status": "dismissed"}


@router.get("/errors")
async def list_errors(
    authorization: Optional[str] = Header(None),
    include_resolved: bool = False,
    limit: int = 200,
) -> dict:
    """Admin only — list errors sorted by count desc."""
    await _require_admin(authorization)
    db = require_db()
    q: dict = {} if include_resolved else {"resolved": {"$ne": True}}
    cursor = (db.frontend_errors.find(q)
                                 .sort("count", -1)
                                 .limit(min(max(limit, 1), 500)))
    items = []
    async for d in cursor:
        items.append({
            "id":            str(d.get("_id")),
            "message":       d.get("message", ""),
            "stack":         d.get("stack", "")[:2_000],
            "url":           d.get("url", ""),
            "type":          d.get("type", "console_error"),
            "count":         int(d.get("count", 0)),
            "first_seen":    d.get("first_seen", ""),
            "last_seen":     d.get("last_seen", ""),
            "resolved":      bool(d.get("resolved", False)),
            "autofix_status": d.get("autofix_status", "idle"),
            "user_agent":    (d.get("user_agent") or "")[:200],
        })
    return {"ok": True, "errors": items, "total": len(items)}


@router.post("/errors/{error_id}/autofix")
async def autofix_error(
    error_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Admin only — kick ORA off to investigate + fix the error.

    Fires `chat_with_tools` in the background so this endpoint returns
    fast. Status moves `idle → queued → done|failed` on the error doc.
    """
    admin = await _require_admin(authorization)
    db = require_db()
    from bson import ObjectId
    try:
        oid = ObjectId(error_id)
    except Exception as _bie:
        raise HTTPException(status_code=400, detail="bad_error_id") from _bie

    doc = await db.frontend_errors.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="error_not_found")

    await db.frontend_errors.update_one(
        {"_id": oid},
        {"$set": {"autofix_status": "queued",
                  "autofix_started": datetime.now(timezone.utc).isoformat()}},
    )

    msg   = doc.get("message", "")
    stack = doc.get("stack", "")[:3_000]
    url   = doc.get("url", "")
    prompt = (
        "A user-facing JS error is firing in production:\n\n"
        f"  message: {msg}\n"
        f"  url:     {url}\n\n"
        f"```\n{stack}\n```\n\n"
        "Investigate the root cause and ship a fix. Read the relevant "
        "files first, then make the change."
    )

    asyncio.create_task(run_error_autofix(
        db, admin_user_id=admin.get("user_id"),
        error_id=error_id, oid=oid, prompt=prompt,
    ))
    return {"ok": True, "queued": True, "error_id": error_id}


@router.post("/errors/{error_id}/resolve")
async def resolve_error(
    error_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Admin only — mark this error as resolved (hides it from the list)."""
    await _require_admin(authorization)
    db = require_db()
    from bson import ObjectId
    try:
        oid = ObjectId(error_id)
    except Exception as _bie:
        raise HTTPException(status_code=400, detail="bad_error_id") from _bie
    r = await db.frontend_errors.update_one(
        {"_id": oid},
        {"$set": {"resolved": True,
                  "resolved_at": datetime.now(timezone.utc).isoformat()}},
    )
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="error_not_found")
    return {"ok": True, "resolved": True}
