"""
routers/notifications_bell.py — P2-A (2026-08-28), user-facing
notification bell.

  GET  /notifications             list (newest first) + unread_count
  POST /notifications/{id}/read   mark one read
  POST /notifications/read-all    mark all read
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from cto_services.auth import current_dev
from cto_services.db import get_db
from services import notifications as notif

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("")
async def list_my_notifications(authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    db = get_db()
    items = await notif.list_notifications(db, user["user_id"])
    count = await notif.unread_count(db, user["user_id"])
    return {"ok": True, "items": items, "unread_count": count}


@router.post("/{notif_id}/read")
async def mark_one_read(notif_id: str, authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    db = get_db()
    ok = await notif.mark_read(db, user["user_id"], notif_id)
    if not ok:
        raise HTTPException(404, "notification not found or already read")
    return {"ok": True}


@router.post("/read-all")
async def mark_all_read(authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    db = get_db()
    n = await notif.mark_all_read(db, user["user_id"])
    return {"ok": True, "marked": n}
