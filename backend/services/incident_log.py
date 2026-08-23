"""Guard 20 — automated postmortem / incident log (Iter 363, ships last).

Every RED/critical alert from ANY guard (1-19) auto-creates an
`incidents` entry: what broke, when detected, which guard caught it,
root cause (filled on resolve), resolution, follow-up. When the
underlying alert clears, the linked incident is auto-resolved and its
MTTR (detected → resolved) is computed.

Single entry-points so every guard funnels through here:
  open_incident(db, guard, title, detail, source_key, ...)
  resolve_incident(db, source_key, resolution, ...)

Both are best-effort — incident bookkeeping must never crash the guard
that is trying to report a problem.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


async def open_incident(db, *, guard: str, title: str, detail: str,
                        source_key: str, severity: str = "critical",
                        follow_up: str = "") -> dict | None:
    """Create an OPEN incident, deduped by (source_key, status=open).
    `source_key` links the incident to the alert that will resolve it."""
    if db is None:
        return None
    now = time.time()
    try:
        # 2026-08-25 — root-cause fix: the previous find-then-insert had
        # a TOCTOU race — concurrent callers (e.g. several boots landing
        # close together) could both see "no open incident yet" and both
        # `insert_one`, colliding on the `uniq_open_source_key` index and
        # throwing E11000 (caught below, non-fatal, but silently dropped
        # the recurrence bump). A single atomic pipeline-style upsert
        # removes the race entirely: first caller creates the row, every
        # later one in the same open window just bumps recurrence. A
        # plain `$set`+`$inc`+`$setOnInsert` upsert was tried first but
        # MongoDB rejects `$inc` and `$setOnInsert` targeting the same
        # field ("ConflictingUpdateOperators") — a pipeline update has
        # no such restriction, so `$ifNull` does the same "-1 then +1 =
        # 0 for a brand-new doc" trick in one `$set` stage instead.
        from pymongo import ReturnDocument
        incident_id = f"inc_{uuid.uuid4().hex[:10]}"
        doc = await db.incidents.find_one_and_update(
            {"source_key": source_key, "status": "open"},
            [{"$set": {
                "incident_id":     {"$ifNull": ["$incident_id", incident_id]},
                "guard":           {"$ifNull": ["$guard", guard]},
                "severity":        {"$ifNull": ["$severity", severity]},
                "title":           {"$ifNull": ["$title", title[:200]]},
                "detail":          {"$ifNull": ["$detail", detail[:800]]},
                "source_key":      {"$ifNull": ["$source_key", source_key]},
                "status":          {"$ifNull": ["$status", "open"]},
                "detected_at":     {"$ifNull": ["$detected_at", now]},
                "detected_at_iso": {"$ifNull": ["$detected_at_iso", _iso(now)]},
                "root_cause":      {"$ifNull": ["$root_cause", None]},
                "resolution":      {"$ifNull": ["$resolution", None]},
                "follow_up":       {"$ifNull": ["$follow_up", follow_up[:400]]},
                "resolved_at":     {"$ifNull": ["$resolved_at", None]},
                "mttr_s":          {"$ifNull": ["$mttr_s", None]},
                "recurrence":      {"$add": [{"$ifNull": ["$recurrence", -1]}, 1]},
                "last_seen":       now,
                "last_seen_iso":   _iso(now),
            }}],
            upsert=True,
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0},
        )
        if doc.get("recurrence", 0) == 0:
            logger.warning("[G20] incident opened [%s] %s (guard=%s)",
                           doc["incident_id"], title[:80], guard)
        return doc
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[G20] open_incident best-effort failure: %r", e)
        return None


async def resolve_incident(db, *, source_key: str, resolution: str,
                           root_cause: str | None = None) -> bool:
    """Resolve the OPEN incident for `source_key`; compute MTTR."""
    if db is None:
        return False
    now = time.time()
    try:
        inc = await db.incidents.find_one(
            {"source_key": source_key, "status": "open"}, {"_id": 0})
        if not inc:
            return False
        mttr = round(now - inc.get("detected_at", now), 1)
        upd = {"status": "resolved", "resolved_at": now,
               "resolved_at_iso": _iso(now), "resolution": resolution[:400],
               "mttr_s": mttr}
        if root_cause:
            upd["root_cause"] = root_cause[:400]
        await db.incidents.update_one(
            {"incident_id": inc["incident_id"]}, {"$set": upd})
        logger.info("[G20] incident resolved [%s] mttr=%.0fs",
                    inc["incident_id"], mttr)
        return True
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[G20] resolve_incident best-effort failure: %r", e)
        return False


async def list_incidents(db, *, status: str = "all", limit: int = 100) -> list[dict]:
    if db is None:
        return []
    query: dict = {}
    if status != "all":
        query["status"] = status
    try:
        return await db.incidents.find(query, {"_id": 0}) \
            .sort("detected_at", -1).limit(limit).to_list(limit)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[G20] list_incidents failure: %r", e)
        return []


async def incident_stats(db) -> dict:
    """QA tab payload: open count, resolved-30d count, MTTR (30d mean)."""
    out = {"open": 0, "resolved_30d": 0, "mttr_30d_s": None, "total": 0}
    if db is None:
        return out
    try:
        out["open"] = await db.incidents.count_documents({"status": "open"})
        out["total"] = await db.incidents.count_documents({})
        cutoff = time.time() - 30 * 86400
        cur = db.incidents.find(
            {"status": "resolved", "resolved_at": {"$gte": cutoff},
             "mttr_s": {"$ne": None}},
            {"_id": 0, "mttr_s": 1})
        mttrs = [d["mttr_s"] async for d in cur]
        out["resolved_30d"] = len(mttrs)
        if mttrs:
            out["mttr_30d_s"] = round(sum(mttrs) / len(mttrs), 1)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[G20] incident_stats failure: %r", e)
    return out
