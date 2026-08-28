"""
services/notifications.py — P2-A (2026-08-28), user-facing notification
bell (dashboard TopBar). Persistent items (payment_failed, ship_failed,
repo_revoked) stay unread until the user acts/dismisses (marks read);
info items (scan_done, ship_done, offer_claimed, kit_live) are
lower-urgency but use the exact same read/unread lifecycle — the
"persistent" flag only changes how the FRONTEND visually treats them
(kept prominent vs auto-fading), never whether they can be marked read.
"""
import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger("aurem.notifications")

PERSISTENT_TYPES = {"payment_failed", "ship_failed", "repo_revoked"}
VALID_TYPES = PERSISTENT_TYPES | {"scan_done", "ship_done", "offer_claimed", "kit_live"}


async def emit_notification(db, *, user_id: str, type: str, text: str,
                             project_id: Optional[str] = None) -> None:
    """Best-effort, never raises — a notification bug must never break
    the real event (a ship, a payment webhook, a scan) it's reporting on."""
    if db is None or not user_id:
        return
    try:
        await db.notifications.insert_one({
            "notif_id":    f"notif_{uuid.uuid4().hex[:12]}",
            "user_id":     user_id,
            "type":        type,
            "text":        text,
            "project_id":  project_id,
            "persistent":  type in PERSISTENT_TYPES,
            "read_at":     None,
            "created_at":  time.time(),
        })
    except Exception as e:                                     # noqa: BLE001
        logger.warning("emit_notification failed (type=%s user=%s): %r", type, user_id, e)


async def list_notifications(db, user_id: str, limit: int = 30) -> list[dict]:
    rows = await db.notifications.find({"user_id": user_id}) \
        .sort("created_at", -1).limit(limit).to_list(length=limit)
    for r in rows:
        r.pop("_id", None)
    return rows


async def unread_count(db, user_id: str) -> int:
    return await db.notifications.count_documents({"user_id": user_id, "read_at": None})


async def mark_read(db, user_id: str, notif_id: str) -> bool:
    r = await db.notifications.update_one(
        {"user_id": user_id, "notif_id": notif_id, "read_at": None},
        {"$set": {"read_at": time.time()}})
    return r.modified_count > 0


async def mark_all_read(db, user_id: str) -> int:
    r = await db.notifications.update_many(
        {"user_id": user_id, "read_at": None}, {"$set": {"read_at": time.time()}})
    return r.modified_count
