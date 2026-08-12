"""
routers/support.py — User-facing ticket submission + admin reply/resolve.

Schema (`cto_support` collection — same one the admin Support panel reads):
  { ticket_id, user_id, user_email, subject, body, status,
    source, created_at, updated_at, last_reply_at }

`cto_support_messages`:
  { ticket_id, sender ('user'|'admin'), message, ts }

Endpoints:
  · POST /support/tickets        — logged-in ticket create (subject optional)
  · POST /support/tickets/token  — public, HMAC-token-verified from email links
  · GET  /support/tickets        — list my tickets (logged in)
  · GET  /support/tickets/{id}   — one of my tickets (logged in)

Mounted under /api/aurem-dev (so user routes live at /support/* and
admin routes are reused from routers/admin_support.py).
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import require_db
from services.first50_campaign import support_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/support", tags=["Support"])


# Sources we accept. Kept as a soft allow-list so unknown values still
# insert (as "unknown") — never a 400 that would surface to a user
# who's already frustrated enough to open a ticket.
_KNOWN_SOURCES = {
    "in_app", "in_app_dashboard", "in_app_empty_state",
    "email_stage_0", "email_stage_3", "email_stage_7",
    "email_other", "landing", "unknown",
}


def _normalize_source(src: Optional[str]) -> str:
    s = (src or "unknown").strip().lower()[:40]
    return s if s in _KNOWN_SOURCES else "unknown"


def _derive_subject(body: str, source: str) -> str:
    """First non-empty line of body (capped) — subject-less UX for the
    user, still a scannable subject for the admin panel list view."""
    body = (body or "").strip()
    if not body:
        return f"({source})"
    first = body.splitlines()[0].strip()
    return (first[:80] + "…") if len(first) > 80 else first


async def _insert_ticket(db, *, user_id: str, user_email: str,
                         name: Optional[str], body: str, source: str,
                         subject: Optional[str] = None) -> str:
    ticket_id = f"tkt_{uuid.uuid4().hex[:12]}"
    now = time.time()
    subj = (subject or "").strip()[:200] or _derive_subject(body, source)
    body_clean = (body or "").strip()[:5000]
    await db.cto_support.insert_one({
        "ticket_id":     ticket_id,
        "user_id":       user_id,
        "user_email":    user_email,
        "user_name":     name,
        "subject":       subj,
        "body":          body_clean,
        "status":        "open",
        "source":        source,
        "created_at":    now,
        "updated_at":    now,
        "last_reply_at": now,
    })
    await db.cto_support_messages.insert_one({
        "ticket_id": ticket_id,
        "sender":    "user",
        "message":   body_clean,
        "ts":        now,
    })
    return ticket_id


class CreateTicket(BaseModel):
    subject: Optional[str] = None
    body: str
    source: Optional[str] = "in_app"


@router.post("/tickets")
async def create_ticket(
    body: CreateTicket,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Logged-in ticket create. Subject is optional — first line of the
    body becomes the subject if not provided (matches the popup UX
    which is subject-less)."""
    user = await current_dev(authorization)
    if not (body.body or "").strip():
        raise HTTPException(400, "message body is required")
    db = require_db()
    ticket_id = await _insert_ticket(
        db,
        user_id=user.get("user_id") or "",
        user_email=user.get("email") or "",
        name=user.get("name"),
        body=body.body,
        source=_normalize_source(body.source),
        subject=body.subject,
    )
    return {"ok": True, "ticket_id": ticket_id}


class CreateTicketByToken(BaseModel):
    t: str        # HMAC token
    e: str        # email
    body: str
    source: Optional[str] = "email_other"


@router.post("/tickets/token")
async def create_ticket_by_token(payload: CreateTicketByToken) -> dict:
    """Public, no-login ticket create — verified via HMAC token
    generated at email-send time (same secret + pattern as unsubscribe).
    Used by the "Need help?" link inside every campaign email."""
    email = (payload.e or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    if payload.t != support_token(email):
        raise HTTPException(403, "invalid token")
    if not (payload.body or "").strip():
        raise HTTPException(400, "message body is required")

    db = require_db()
    # Best-effort user lookup so admin sees the same user_id and any
    # existing name; never blocks the ticket write.
    dev = await db.dev_users.find_one(
        {"email": email},
        {"_id": 0, "user_id": 1, "name": 1},
    ) or {}
    ticket_id = await _insert_ticket(
        db,
        user_id=dev.get("user_id") or f"unauth-{email}",
        user_email=email,
        name=dev.get("name"),
        body=payload.body,
        source=_normalize_source(payload.source),
    )
    return {"ok": True, "ticket_id": ticket_id}


@router.get("/tickets")
async def list_my_tickets(authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = require_db()
    tickets = await db.cto_support.find(
        {"user_id": user.get("user_id")},
        {"_id": 0},
    ).sort("updated_at", -1).limit(50).to_list(50)
    return {"tickets": tickets}


@router.get("/tickets/{ticket_id}")
async def get_my_ticket(
    ticket_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = require_db()
    t = await db.cto_support.find_one(
        {"ticket_id": ticket_id, "user_id": user.get("user_id")},
        {"_id": 0},
    )
    if not t:
        raise HTTPException(404, "Ticket not found")
    msgs = await db.cto_support_messages.find(
        {"ticket_id": ticket_id}, {"_id": 0},
    ).sort("ts", 1).to_list(200)
    t["messages"] = msgs
    return t
