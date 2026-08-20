"""
routers/admin_public.py — Iter 358.

The ONE intentionally-public endpoint that historically lived under the
/admin prefix: the frontend console-error sink. It must stay reachable
without auth (logged-out users hit console errors too), so it cannot
live on the now router-gated admin router.

Kept at the SAME URL (/admin/errors/report) so no frontend change is
needed — this router mounts with prefix "/admin" and NO admin
dependency, and only exposes this single write-only endpoint that
returns no data.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel

from cto_services.db import get_db

router = APIRouter(prefix="/admin", tags=["Admin-public"])


class ErrorReport(BaseModel):
    message:   str
    stack:     str = ""
    url:       str = ""
    timestamp: str = ""
    type:      str = "console_error"


@router.post("/errors/report")
async def report_error(body: ErrorReport, request: Request) -> dict:
    """Public — no auth. Frontend posts every console.error here.
    Write-only: dedupes by (message, url), returns only {ok}. No admin
    data is ever read or returned, so keeping it unauthenticated is
    safe (abuse throttling is Guard 14 territory, not a data-leak risk).
    """
    db = get_db()
    if db is None:
        return {"ok": False, "error": "db_unavailable"}
    now = datetime.now(timezone.utc)
    msg   = (body.message or "")[:4_000]
    stack = (body.stack   or "")[:16_000]
    url   = (body.url     or "")[:1_000]
    if not msg.strip():
        return {"ok": False, "error": "empty_message"}
    update = {
        "$inc": {"count": 1},
        "$set": {
            "last_seen":  now.isoformat(),
            "last_seen_at": now,
            "stack":      stack,
            "type":       body.type or "console_error",
            "user_agent": (request.headers.get("user-agent") or "")[:500],
        },
        "$setOnInsert": {
            "message":    msg,
            "url":        url,
            "first_seen": now.isoformat(),
            "resolved":   False,
            "autofix_status": "idle",
        },
    }
    await db.frontend_errors.update_one(
        {"message": msg, "url": url}, update, upsert=True)
    return {"ok": True}
