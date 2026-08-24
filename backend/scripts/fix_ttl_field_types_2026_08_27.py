"""
scripts/fix_ttl_field_types_2026_08_27.py

One-time, idempotent backfill for the TTL-field-type audit (2026-08-27).

Root cause: several collections have a Mongo TTL index (expireAfterSeconds)
on a field, but the application was writing that field as `time.time()`
(float epoch) or `.isoformat()` (string) instead of a real BSON `Date`.
MongoDB's TTL monitor only ever inspects Date (or array-of-Date) fields —
it silently ignores numbers and strings, so those rows never expire no
matter how old they get. The writer-side bugs are fixed separately in:
  services/loop_safety.py, services/loop_independent_verifier.py,
  services/loop_audit_log.py, services/loop_rollback.py,
  services/loop_engine.py, routers/loop.py, routers/oauth.py,
  routers/cto_projects.py

This script repairs the EXISTING rows written before that fix landed, so
they finally become eligible for TTL cleanup instead of sitting as
permanent garbage. Safe to re-run — every step only touches docs whose
field is still the wrong type / missing.

Dry-run by default. Pass --apply to actually write.

Usage:
    python -m scripts.fix_ttl_field_types_2026_08_27          # dry run
    python -m scripts.fix_ttl_field_types_2026_08_27 --apply  # real run
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


def _parse_any_ts(value) -> "datetime | None":
    """Best-effort: turn a float epoch, ISO string, or existing datetime
    into a tz-aware UTC datetime. Returns None if unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


async def _backfill_wrong_type(db, coll: str, field: str, apply: bool) -> dict:
    """Convert every doc where `field` exists but is NOT a datetime into
    a real datetime, parsed from whatever it currently holds."""
    query = {field: {"$exists": True, "$not": {"$type": "date"}}}
    total = await db[coll].count_documents(query)
    converted = 0
    unparseable = 0
    if total and apply:
        cursor = db[coll].find(query, {"_id": 1, field: 1})
        async for doc in cursor:
            dt = _parse_any_ts(doc.get(field))
            if dt is None:
                unparseable += 1
                continue
            await db[coll].update_one({"_id": doc["_id"]}, {"$set": {field: dt}})
            converted += 1
    return {"collection": coll, "field": field, "candidates": total,
            "converted": converted if apply else None,
            "unparseable": unparseable if apply else None, "dry_run": not apply}


async def _backfill_missing_from(db, coll: str, missing_field: str,
                                 source_field: str, apply: bool) -> dict:
    """For docs lacking `missing_field` entirely, derive it from
    `source_field` (parsed to a real datetime) so the TTL index on
    `missing_field` finally has something to act on."""
    query = {missing_field: {"$exists": False}, source_field: {"$exists": True}}
    total = await db[coll].count_documents(query)
    converted = 0
    unparseable = 0
    if total and apply:
        cursor = db[coll].find(query, {"_id": 1, source_field: 1})
        async for doc in cursor:
            dt = _parse_any_ts(doc.get(source_field))
            if dt is None:
                unparseable += 1
                continue
            await db[coll].update_one(
                {"_id": doc["_id"]}, {"$set": {missing_field: dt}})
            converted += 1
    return {"collection": coll, "field": f"{missing_field} (from {source_field})",
            "candidates": total, "converted": converted if apply else None,
            "unparseable": unparseable if apply else None, "dry_run": not apply}


async def _delete_stale_oauth_states(db, apply: bool) -> dict:
    """oauth_states TTL index targets `created_at`, but ~28 legacy rows
    (written before the writer-side fix that added `created_at`) only
    have `ts`. They are, by design, always well past the 5-10 min OAuth
    flow window — safe to hard-delete rather than backfill, since a
    genuinely abandoned OAuth state carries no value."""
    query = {"created_at": {"$exists": False}}
    total = await db.oauth_states.count_documents(query)
    deleted = 0
    if total and apply:
        res = await db.oauth_states.delete_many(query)
        deleted = res.deleted_count
    return {"collection": "oauth_states", "action": "delete_legacy_no_created_at",
            "candidates": total, "deleted": deleted if apply else None,
            "dry_run": not apply}


async def run(apply: bool) -> list[dict]:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    results = []
    try:
        results.append(await _backfill_wrong_type(db, "loop_failures", "occurred_at", apply))
        results.append(await _backfill_wrong_type(db, "loop_verification_log", "created_at", apply))
        results.append(await _backfill_wrong_type(db, "loop_run_log", "created_at", apply))
        results.append(await _backfill_missing_from(db, "loop_run_log", "created_at", "ts", apply))
        results.append(await _backfill_missing_from(db, "loop_events", "created_at", "ts", apply))
        results.append(await _backfill_wrong_type(db, "warm_start_jobs", "started_at", apply))
        results.append(await _backfill_wrong_type(db, "oauth_codes", "expires_at", apply))
        results.append(await _backfill_wrong_type(
            db, "api_keys", "expires_at", apply))  # scoped by TTL's partialFilter(source=oauth) at read time
        # 2026-08-27 — testing_agent live-DB probe caught a THIRD
        # loop_sessions.updated_at writer this audit missed on the
        # first pass (routers/loop.py:1081 cancel path + a fourth in
        # services/loop_rollback.py:123 SSE-emit path) — both now
        # fixed writer-side; this repairs the rows they already wrote.
        results.append(await _backfill_wrong_type(db, "loop_sessions", "updated_at", apply))
        results.append(await _delete_stale_oauth_states(db, apply))
    finally:
        client.close()
    return results


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    out = asyncio.run(run(apply))
    for row in out:
        print(row)
    if not apply:
        print("\nDRY RUN — no writes made. Re-run with --apply to fix.")
