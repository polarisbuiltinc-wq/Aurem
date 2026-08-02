"""
routers/admin_health.py — Unified Health aggregator (Feb 2026)

Two endpoints backing the admin cockpit + notification bell + every
per-page health tile:

  GET  /admin/status/all           run every registered check_fn
  POST /admin/status/{id}/ack      acknowledge-and-mute a check

Aggregation contract (from services/health_registry.py):
  { checks: [
      { id, name, category, status: green|red|gray,
        detail, checked_at, red_since, acked_until },
    ],
    generated_at, took_ms, counts: {green, red, gray, total} }

TTL cache (5-10s, env-configurable) is deliberately short — the
whole point of the registry is LIVE data. Cache exists only so that
30 admin sessions polling every 30s don't hammer real infra 900
times a minute.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Depends

from cto_services.auth import require_admin_dep
from services.health_registry import all_checks, get_check, run_check_safely

# Import triggers registration of every adapter.
import services.health_checks  # noqa: F401  — side-effect registration

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/aurem-dev/admin/status",
    tags=["Admin-health"],
    dependencies=[Depends(require_admin_dep)],
)


# ── Cache ──────────────────────────────────────────────────────────

_STATUS_CACHE: dict = {"payload": None, "expires_at": 0.0}
_STATUS_CACHE_TTL_S = int(os.environ.get("HEALTH_STATUS_CACHE_TTL_S", "8"))


# ── Ack + red_since state (in Mongo so it survives restarts) ───────
#
# We deliberately keep the "when did this check first go red" and
# "acked-until" fields in the DB rather than in-memory. Founder
# acking a red at 3am must survive the next uvicorn worker cycle.

async def _get_state_collection():
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return None
    return db.health_check_state


async def _load_state(check_id: str) -> dict:
    """Return {red_since: iso|None, acked_until: iso|None}."""
    coll = await _get_state_collection()
    if coll is None:
        return {"red_since": None, "acked_until": None}
    row = await coll.find_one({"_id": check_id})
    return {
        "red_since":   (row or {}).get("red_since"),
        "acked_until": (row or {}).get("acked_until"),
    }


async def _persist_red_since(check_id: str, when: str) -> None:
    coll = await _get_state_collection()
    if coll is None:
        return
    await coll.update_one(
        {"_id": check_id},
        {"$set": {"red_since": when}},
        upsert=True,
    )


async def _clear_red_since(check_id: str) -> None:
    coll = await _get_state_collection()
    if coll is None:
        return
    await coll.update_one(
        {"_id": check_id},
        {"$set": {"red_since": None}},
    )


async def _persist_ack(check_id: str, until_iso: Optional[str]) -> None:
    coll = await _get_state_collection()
    if coll is None:
        raise HTTPException(503, "database unavailable — cannot persist ack")
    await coll.update_one(
        {"_id": check_id},
        {"$set": {"acked_until": until_iso}},
        upsert=True,
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ack_active(acked_until: Optional[str]) -> bool:
    if not acked_until:
        return False
    try:
        return datetime.fromisoformat(acked_until.replace("Z", "+00:00")) \
             > datetime.now(timezone.utc)
    except Exception:  # noqa: BLE001 — corrupt row treated as unacked
        return False


# ── Endpoints ──────────────────────────────────────────────────────

@router.get("/all")
async def status_all():
    """Run every registered check_fn in parallel, return the
    combined status snapshot. Short-TTL cached (default 8s)."""
    now = time.time()
    if _STATUS_CACHE["payload"] and now < _STATUS_CACHE["expires_at"]:
        return _STATUS_CACHE["payload"]

    t0 = time.time()
    checks = all_checks()
    results = await asyncio.gather(
        *[run_check_safely(c) for c in checks],
        return_exceptions=False,
    )

    rows: list[dict] = []
    now_iso = _iso_now()
    for check, res in zip(checks, results):
        state = await _load_state(check.id)
        status = res["status"]
        red_since = state["red_since"]
        # Roll red_since forward: first time we see red → stamp it;
        # green/gray transitions clear it.
        if status == "red" and not red_since:
            red_since = now_iso
            await _persist_red_since(check.id, red_since)
        elif status != "red" and red_since:
            red_since = None
            await _clear_red_since(check.id)

        rows.append({
            "id":         check.id,
            "name":       check.name,
            "category":   check.category,
            "status":     status,
            "detail":     res.get("detail", ""),
            "checked_at": res.get("checked_at"),
            "red_since":  red_since,
            "acked_until": state["acked_until"],
            "ack_active":  _ack_active(state["acked_until"]),
        })

    counts = {
        "green": sum(1 for r in rows if r["status"] == "green"),
        "red":   sum(1 for r in rows if r["status"] == "red"),
        "gray":  sum(1 for r in rows if r["status"] == "gray"),
        "total": len(rows),
    }
    # Health % excludes gray (per 3-state discipline: gray is
    # "not-set-up-yet" — neither passing nor failing).
    denom = counts["green"] + counts["red"]
    counts["health_pct"] = round(100.0 * counts["green"] / denom, 1) if denom else None

    payload = {
        "generated_at": now_iso,
        "took_ms":      int((time.time() - t0) * 1000),
        "counts":       counts,
        "checks":       rows,
    }
    _STATUS_CACHE["payload"]    = payload
    _STATUS_CACHE["expires_at"] = now + _STATUS_CACHE_TTL_S
    return payload


@router.post("/{check_id}/ack")
async def ack_check(check_id: str, until: Optional[str] = None):
    """Acknowledge-and-mute a check until an ISO timestamp.

    Body/query param `until` (ISO-8601). Pass empty/null to clear
    a prior ack. Acked checks still appear in /all but with
    `ack_active: true`; they're excluded from the notification-bell
    badge and the cockpit "Needs Attention" list.
    """
    if not get_check(check_id):
        raise HTTPException(404, f"unknown check id: {check_id}")
    if until:
        try:
            datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, "until must be ISO-8601 UTC")
    await _persist_ack(check_id, until or None)
    # Invalidate the aggregator cache so the next /all reflects it.
    _STATUS_CACHE["payload"] = None
    _STATUS_CACHE["expires_at"] = 0.0
    return {"ok": True, "check_id": check_id, "acked_until": until or None}


# ═══════════════════════════════════════════════════════════════
# Notification bell endpoints (Feb 2026)
#
# UI short-polls /notifications every 10-15s to render the badge.
# All rows live in the health_notifications collection populated by
# services/health_notifier.py.
# ═══════════════════════════════════════════════════════════════

@router.get("/notifications")
async def list_notifications(limit: int = 30):
    """Newest-first list. `unread_count` drives the bell badge."""
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return {"available": False, "notifications": [], "unread_count": 0}
    limit = max(1, min(int(limit or 30), 200))
    rows = await db.health_notifications.find({}) \
        .sort("created_at", -1).limit(limit).to_list(None)
    for r in rows:
        r.pop("_id", None)
    unread = await db.health_notifications.count_documents({"read": False})
    return {
        "available":    True,
        "notifications": rows,
        "unread_count":  unread,
    }


@router.post("/notifications/mark-read")
async def mark_notifications_read():
    """Bell UI 'mark all as read' — clears the badge without deleting
    history."""
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")
    r = await db.health_notifications.update_many(
        {"read": False}, {"$set": {"read": True}}
    )
    return {"ok": True, "modified": r.modified_count}


__all__ = ["router"]
