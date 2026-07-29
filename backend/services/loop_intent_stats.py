"""
services/loop_intent_stats.py — Iter 350 · Intent-gate observability.

Hourly-bucketed counters in `loop_intent_stats` (one doc per UTC hour):
  chat_redirect   — read-only query intercepted, answered via chat
  loop_triggered  — a real loop engine start (gate passed)
  timeout_failed  — plan phase died on a timeout (LLM 30s cap or budget)

Both writes are best-effort — stats must never break the hot path.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("aurem.loop.intent_stats")

BUCKETS = ("chat_redirect", "loop_triggered", "timeout_failed")


def _hour_key(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H")


async def record_intent_stat(db, bucket: str) -> None:
    if db is None or bucket not in BUCKETS:
        return
    try:
        await db.loop_intent_stats.update_one(
            {"hour_key": _hour_key()},
            {"$inc": {bucket: 1},
             "$setOnInsert": {
                 "created_at": datetime.now(timezone.utc).isoformat(),
             }},
            upsert=True,
        )
    except Exception as e:                              # noqa: BLE001
        logger.debug("intent stat write skipped (%s): %r", bucket, e)


async def get_intent_stats(db, hours: int = 24) -> dict:
    now = datetime.now(timezone.utc)
    keys = [_hour_key(now - timedelta(hours=i)) for i in range(hours)]
    docs = {}
    async for d in db.loop_intent_stats.find(
            {"hour_key": {"$in": keys}}, {"_id": 0}):
        docs[d["hour_key"]] = d
    rows, totals = [], {b: 0 for b in BUCKETS}
    for k in sorted(keys):
        d = docs.get(k) or {}
        row = {"hour_key": k}
        for b in BUCKETS:
            v = int(d.get(b) or 0)
            row[b] = v
            totals[b] += v
        rows.append(row)
    denom = totals["chat_redirect"] + totals["loop_triggered"]
    return {
        "window_hours": hours,
        "totals": totals,
        "redirect_rate": (
            round(totals["chat_redirect"] / denom, 3) if denom else None),
        "hourly": rows,
    }
