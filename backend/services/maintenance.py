"""System Maintenance / Outage Tracker (2026-08).

Two independent concerns living in one small module:

  1. Manual planned-maintenance flag — admin-controlled banner shown
     to every visitor immediately (e.g. right before a deploy).
  2. Automatic outage detection — a persisted heartbeat is bumped
     every 60s while the process is alive (piggybacked on the
     existing loop_housekeeping tick). On boot, if the gap between
     "last heartbeat" and "now" exceeds `outage_threshold_s`, the
     backend was unreachable for that long (deploy restart or crash)
     — log it as a resolved incident so the admin tracker shows
     count + duration + reason without needing an external monitor.

The manual-maintenance state is cached in-memory (`_CACHE`) so the
public `/maintenance/status` endpoint never needs a DB round-trip on
the hot path and keeps answering even if Mongo is briefly unhappy.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_OUTAGE_THRESHOLD_S = 30

_CACHE: dict = {
    "manual_enabled": False,
    "message": "",
    "window": "",
    "outage_threshold_s": DEFAULT_OUTAGE_THRESHOLD_S,
    "updated_at": None,
    "updated_by": None,
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_maintenance_cache() -> dict:
    """Sync, no DB call — safe for the public status endpoint hot path."""
    return dict(_CACHE)


async def load_maintenance_state(db) -> dict:
    """Hydrate `_CACHE` from Mongo at boot. Best-effort — keeps
    in-memory defaults if the DB isn't reachable yet."""
    if db is None:
        return dict(_CACHE)
    try:
        row = await db.maintenance_state.find_one({"_id": "singleton"})
        if row:
            for k in ("manual_enabled", "message", "window",
                      "outage_threshold_s", "updated_at", "updated_by"):
                if k in row and row[k] is not None:
                    _CACHE[k] = row[k]
    except Exception as e:  # noqa: BLE001
        logger.warning("[maintenance] load_maintenance_state failed: %r", e)
    return dict(_CACHE)


async def set_maintenance_state(db, *, manual_enabled: Optional[bool] = None,
                                 message: Optional[str] = None,
                                 window: Optional[str] = None,
                                 outage_threshold_s: Optional[int] = None,
                                 updated_by: Optional[str] = None) -> dict:
    """Partial update — only fields passed (non-None) change."""
    updates: dict = {}
    if manual_enabled is not None:
        updates["manual_enabled"] = bool(manual_enabled)
    if message is not None:
        updates["message"] = message[:500]
    if window is not None:
        updates["window"] = window[:200]
    if outage_threshold_s is not None:
        updates["outage_threshold_s"] = max(5, min(int(outage_threshold_s), 600))
    updates["updated_at"] = _iso_now()
    updates["updated_by"] = updated_by or "admin"

    _CACHE.update(updates)

    if db is not None:
        try:
            await db.maintenance_state.update_one(
                {"_id": "singleton"}, {"$set": updates}, upsert=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("[maintenance] set_maintenance_state persist failed: %r", e)
    return dict(_CACHE)


async def write_heartbeat(db) -> None:
    """Best-effort persisted heartbeat — piggybacks the existing 60s
    loop_housekeeping tick. Never raises."""
    if db is None:
        return
    now = time.time()
    try:
        await db.system_heartbeat.update_one(
            {"_id": "singleton"},
            {"$set": {"last_beat_ts": now, "last_beat_iso": _iso_now()}},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[maintenance] write_heartbeat failed: %r", e)


async def read_last_heartbeat(db) -> Optional[float]:
    if db is None:
        return None
    try:
        row = await db.system_heartbeat.find_one({"_id": "singleton"})
        return (row or {}).get("last_beat_ts")
    except Exception as e:  # noqa: BLE001
        logger.warning("[maintenance] read_last_heartbeat failed: %r", e)
        return None


async def record_boot_gap_incident(db, *, gap_s: float, last_beat_iso: str,
                                    boot_iso: str) -> None:
    """Log an already-over outage detected purely from the boot-time
    heartbeat gap. Reason is intentionally generic ("auto_boot_gap")
    since we can't distinguish a graceful deploy restart from a crash
    from this signal alone — the admin can eyeball the duration."""
    if db is None:
        return
    try:
        await db.outage_incidents.insert_one({
            "incident_id": f"out_{uuid.uuid4().hex[:10]}",
            "started_at": last_beat_iso,
            "ended_at": boot_iso,
            "duration_s": round(gap_s, 1),
            "reason": "auto_boot_gap",
            "detail": f"Backend was unreachable for ~{round(gap_s)}s "
                      f"(likely a deploy restart or crash) before this boot.",
            "source": "auto_boot_gap",
            "resolved": True,
            "created_at": boot_iso,
        })
        logger.warning("[maintenance] outage logged: %.0fs gap before boot", gap_s)
    except Exception as e:  # noqa: BLE001
        logger.warning("[maintenance] record_boot_gap_incident failed: %r", e)


async def list_outage_incidents(db, limit: int = 100) -> list[dict]:
    if db is None:
        return []
    try:
        rows = await db.outage_incidents.find({}, {"_id": 0}) \
            .sort("started_at", -1).limit(limit).to_list(limit)
        return rows
    except Exception as e:  # noqa: BLE001
        logger.warning("[maintenance] list_outage_incidents failed: %r", e)
        return []


async def outage_stats(db) -> dict:
    out = {"count_all": 0, "count_30d": 0, "total_downtime_s_30d": 0.0,
           "avg_duration_s_30d": None}
    if db is None:
        return out
    try:
        out["count_all"] = await db.outage_incidents.count_documents({})
        cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        cur = db.outage_incidents.find(
            {"started_at": {"$gte": cutoff_iso}}, {"_id": 0, "duration_s": 1})
        durations = [d.get("duration_s", 0) async for d in cur]
        out["count_30d"] = len(durations)
        out["total_downtime_s_30d"] = round(sum(durations), 1)
        if durations:
            out["avg_duration_s_30d"] = round(sum(durations) / len(durations), 1)
    except Exception as e:  # noqa: BLE001
        logger.warning("[maintenance] outage_stats failed: %r", e)
    return out


__all__ = [
    "get_maintenance_cache", "load_maintenance_state", "set_maintenance_state",
    "write_heartbeat", "read_last_heartbeat", "record_boot_gap_incident",
    "list_outage_incidents", "outage_stats", "DEFAULT_OUTAGE_THRESHOLD_S",
]
