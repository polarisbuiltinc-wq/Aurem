"""
services/llm_cost_breaker.py — G13 · LLM cost circuit breaker (Iter 366)

Hourly + daily USD caps across ALL LLM providers. Cap hit → subsequent
calls raise HTTPException(429 llm_cost_cap_hit) + fire a G10 founder
alert (max 1/6h dedup).

Per-loop cap: any single loop_id crossing LLM_COST_CAP_PER_LOOP is
auto-killed and marked FAILED with resume_reason="llm_cost_cap".

Env:
  LLM_COST_CAP_HOURLY    default 2.00  ($)
  LLM_COST_CAP_DAILY     default 10.00 ($)
  LLM_COST_CAP_PER_LOOP  default 0.50  ($)

Collections used:
  llm_cost_ledger — {ts, provider, cost_usd, user_id, loop_id, model}

Wiring: `record_cost(...)` is called from services/llm.py after every
call_llm returns with a `cost_usd` estimate. `assert_within_cap()` is
called BEFORE the call.
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger("aurem.llm_cost_breaker")


def _envf(k: str, d: float) -> float:
    try:    return float(os.environ.get(k, str(d)))
    except (ValueError, TypeError): return d


LLM_COST_CAP_HOURLY   = _envf("LLM_COST_CAP_HOURLY",   2.00)
LLM_COST_CAP_DAILY    = _envf("LLM_COST_CAP_DAILY",   10.00)
LLM_COST_CAP_PER_LOOP = _envf("LLM_COST_CAP_PER_LOOP", 0.50)


async def _sum_cost_since(db, since: datetime,
                           loop_id: Optional[str] = None) -> float:
    if db is None:
        return 0.0
    match: dict = {"ts": {"$gte": since}}
    if loop_id:
        match["loop_id"] = loop_id
    try:
        pipeline = [
            {"$match": match},
            {"$group": {"_id": None, "sum": {"$sum": "$cost_usd"}}},
        ]
        async for row in db.llm_cost_ledger.aggregate(pipeline):
            return float(row.get("sum") or 0.0)
    except Exception as e:
        logger.debug("[G13] sum_cost query failed: %r", e)
    return 0.0


async def assert_within_cap(
    db, *, loop_id: Optional[str] = None,
    est_cost_usd: float = 0.0,
) -> None:
    """Raise 429 if the projected spend (current spend + estimated cost
    of THIS call) would exceed any cap."""
    if db is None:
        return
    now  = datetime.now(timezone.utc)
    h1   = now - timedelta(hours=1)
    d1   = now - timedelta(days=1)
    spend_h = await _sum_cost_since(db, h1)
    spend_d = await _sum_cost_since(db, d1)

    cost = float(est_cost_usd or 0.0)
    if spend_h + cost > LLM_COST_CAP_HOURLY:
        await _fire_cap_alert(db, "hourly", spend_h, LLM_COST_CAP_HOURLY)
        raise HTTPException(429, {
            "error": "llm_cost_cap_hit",
            "cap":   "hourly", "limit": LLM_COST_CAP_HOURLY,
            "spent_last_hour": round(spend_h, 4),
            "message": ("Hourly LLM spend cap reached — new requests "
                        "temporarily blocked. Retry in ~1h."),
        })
    if spend_d + cost > LLM_COST_CAP_DAILY:
        await _fire_cap_alert(db, "daily", spend_d, LLM_COST_CAP_DAILY)
        raise HTTPException(429, {
            "error": "llm_cost_cap_hit",
            "cap":   "daily", "limit": LLM_COST_CAP_DAILY,
            "spent_last_24h": round(spend_d, 4),
            "message": ("Daily LLM spend cap reached — new requests "
                        "blocked until UTC midnight."),
        })
    if loop_id:
        spend_l = await _sum_cost_since(db, h1 - timedelta(hours=23),
                                          loop_id=loop_id)
        if spend_l + cost > LLM_COST_CAP_PER_LOOP:
            # Signal loop_engine to auto-kill this loop.
            try:
                await db.loop_sessions.update_one(
                    {"loop_id": loop_id},
                    {"$set": {"resume_reason": "llm_cost_cap"}},
                )
            except Exception:
                pass
            raise HTTPException(429, {
                "error": "llm_cost_cap_hit",
                "cap":   "per_loop", "limit": LLM_COST_CAP_PER_LOOP,
                "loop_id":  loop_id,
                "loop_spend_usd": round(spend_l, 4),
                "message": ("This loop hit the per-run cost cap "
                            f"(${LLM_COST_CAP_PER_LOOP:.2f}) and was "
                            "auto-stopped."),
            })


async def record_cost(
    db, *,
    provider:    str,
    cost_usd:    float,
    user_id:     Optional[str] = None,
    loop_id:     Optional[str] = None,
    model:       Optional[str] = None,
) -> None:
    """Best-effort — never raises. Called AFTER every LLM call returns."""
    if db is None or cost_usd is None:
        return
    try:
        await db.llm_cost_ledger.insert_one({
            "ts":       datetime.now(timezone.utc),
            "provider": provider,
            "cost_usd": float(cost_usd or 0.0),
            "user_id":  user_id,
            "loop_id":  loop_id,
            "model":    model,
        })
    except Exception as e:
        logger.debug("[G13] record_cost write failed: %r", e)


async def _fire_cap_alert(db, cap_kind: str, spend: float, cap: float) -> None:
    try:
        from services.founder_alerts import send_founder_alert
        await send_founder_alert(
            db,
            source_key=f"llm_cost_cap:{cap_kind}",
            title=f"LLM {cap_kind} spend cap hit (${spend:.2f} / ${cap:.2f})",
            detail=(f"The {cap_kind} LLM spend cap has been reached. "
                    f"New calls are being rejected with HTTP 429 until "
                    f"the window rolls over. Verify no runaway loop / "
                    f"malicious traffic before raising the cap."),
            level="critical", guard="G13",
        )
    except Exception:
        pass


async def spend_summary(db) -> dict:
    """QA panel snapshot — current spend vs each cap."""
    if db is None:
        return {"available": False}
    now = datetime.now(timezone.utc)
    return {
        "available":         True,
        "hourly_spent":      round(await _sum_cost_since(db, now - timedelta(hours=1)), 4),
        "hourly_cap":        LLM_COST_CAP_HOURLY,
        "daily_spent":       round(await _sum_cost_since(db, now - timedelta(days=1)), 4),
        "daily_cap":         LLM_COST_CAP_DAILY,
        "per_loop_cap":      LLM_COST_CAP_PER_LOOP,
    }
