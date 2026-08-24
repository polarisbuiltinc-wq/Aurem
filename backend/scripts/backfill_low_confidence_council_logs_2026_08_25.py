"""
scripts/backfill_low_confidence_council_logs_2026_08_25.py

One-time, idempotent retroactive cleanup for Engineering Gap follow-up
(2026-08-25): existing `ora_council_logs` rows written BEFORE this
session's fixes (Point 6, services/ora_council_retriever.py) have no
`low_confidence` field, so they default to eligible-for-recall even
when they are exactly the kind of exchange the new `_quality_filter`
gate now excludes going forward — a fallback-message turn, or a
prose "Ship via CTO"-style mismatch. Without this backfill, those
historical rows keep polluting the few-shot recall corpus until they
naturally age out of `_MAX_CORPUS`.

Two independent signals, either one marks a row `low_confidence=True`:
  1. `final_output` is exactly `response_confidence.FALLBACK_MESSAGE`
     — the canned "I couldn't find a confident answer..." text. This
     is never a useful example, and (unlike signal 2) is a 100%
     certain match with zero false positives.
  2. `response_seems_mismatched(user_message, final_output)` is True
     under the WIDENED detector from this session's Item-2 fix — this
     retroactively re-applies the now-stricter check, catching
     historical "Ship via CTO in prose" style mismatches that were
     logged before that widened detector (or before /chat/send had
     any confidence check at all) existed.

Dry-run by default. Pass --apply to actually write.

Usage:
    python -m scripts.backfill_low_confidence_council_logs_2026_08_25          # dry run
    python -m scripts.backfill_low_confidence_council_logs_2026_08_25 --apply  # real run
"""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from services.response_confidence import FALLBACK_MESSAGE, response_seems_mismatched  # noqa: E402


async def run(apply: bool) -> dict:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    result = {
        "candidates_scanned": 0,
        "fallback_message_matches": 0,
        "mismatch_detector_matches": 0,
        "total_flagged": 0,
        "dry_run": not apply,
    }
    try:
        query = {
            "user_message": {"$exists": True, "$ne": ""},
            "final_output": {"$exists": True, "$ne": ""},
            "low_confidence": {"$ne": True},
        }
        cursor = db["ora_council_logs"].find(
            query, {"_id": 1, "user_message": 1, "final_output": 1, "low_confidence": 1},
        )
        async for doc in cursor:
            result["candidates_scanned"] += 1

            user_message = doc.get("user_message") or ""
            final_output = doc.get("final_output") or ""
            is_fallback = final_output.strip() == FALLBACK_MESSAGE.strip()
            is_mismatch = (
                not is_fallback
                and response_seems_mismatched(user_message, final_output)
            )
            if not (is_fallback or is_mismatch):
                continue

            if is_fallback:
                result["fallback_message_matches"] += 1
            else:
                result["mismatch_detector_matches"] += 1
            result["total_flagged"] += 1

            if apply:
                await db["ora_council_logs"].update_one(
                    {"_id": doc["_id"]},
                    {"$set": {
                        "low_confidence": True,
                        "low_confidence_backfilled": True,
                    }},
                )
    finally:
        client.close()
    return result


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    out = asyncio.run(run(apply))
    print(out)
    if not apply:
        print("\nDRY RUN — no writes made. Re-run with --apply to fix.")
