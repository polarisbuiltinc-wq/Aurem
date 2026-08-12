"""
routers/email_unsubscribe.py — CAN-SPAM / GDPR compliant one-click
unsubscribe endpoint. No login required. HMAC-token verified.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cto_services.db import get_db
from services.first50_campaign import unsub_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Email unsubscribe"])


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(t: str = "", e: str = "",
                        request: Request = None) -> HTMLResponse:
    """One-click unsubscribe. Idempotent."""
    email = (e or "").lower().strip()
    if not email or not t or t != unsub_token(email):
        return HTMLResponse(
            "<h2>Invalid unsubscribe link</h2>"
            "<p>The link is malformed or expired. Reply to any email from us "
            "and we'll remove you manually.</p>",
            status_code=400,
        )
    db = get_db()
    if db is not None:
        ip = ""
        try:
            ip = request.client.host if request and request.client else ""
        except Exception:
            pass
        await db.email_unsubscribes.update_one(
            {"email": email},
            {"$setOnInsert": {
                "email":            email,
                "unsubscribed_at":  datetime.now(timezone.utc),
                "source":           "first50_drip_footer",
                "ip":               ip,
            }},
            upsert=True,
        )
        logger.info("email_unsubscribe: %s opted out", email)
    return HTMLResponse(
        f"<!doctype html><html><head><title>Unsubscribed</title></head>"
        f"<body style='font-family:-apple-system,sans-serif;max-width:520px;"
        f"margin:80px auto;padding:24px;color:#111;text-align:center'>"
        f"<h2 style='color:#22c55e'>&#10003; You're unsubscribed</h2>"
        f"<p><b>{email}</b> won't receive any more campaign emails from us.</p>"
        f"<p style='color:#666;font-size:13px'>Transactional emails (password "
        f"reset, security alerts) are separate and still work — those aren't "
        f"campaigns. If you want a full account deletion instead, reply to "
        f"any email and we'll handle it manually.</p></body></html>"
    )
