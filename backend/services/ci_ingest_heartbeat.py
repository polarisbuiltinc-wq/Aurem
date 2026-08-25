"""
services/ci_ingest_heartbeat.py — 2026-08-24 (Pillar 3, Production-Readiness)

Expected-vs-actual heartbeat for the G1/G15 CI-result ingest pipeline
(routers/synthetic_checks_ci.py). Closes a real gap found during the
Inventory Sweep: `services/health_checks.py`'s G1/G15 adapters only
ever check the CONTENT of the last run (green/red), never its AGE —
so a pipeline that silently stopped ingesting (e.g. production missing
AUREM_CI_INGEST_TOKEN, per PRD "Finding B") would sit GREEN forever on
stale data with nobody alerted.

CI runs on every push to ci.yml — both g1_route_sweep and g15_dep_scan
should therefore produce a fresh `synthetic_checks` row at least as
often as EXPECTED_MAX_GAP_HOURS, or something upstream is broken
(wrong token, ingest endpoint down, workflow itself failing before
the ingest step runs).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os

from services.founder_alerts import send_founder_alert

logger = logging.getLogger("ci_ingest_heartbeat")

HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("CI_HEARTBEAT_INTERVAL_SECONDS", str(6 * 3600)))
STARTUP_DELAY_SECONDS = int(os.environ.get("CI_HEARTBEAT_STARTUP_DELAY_SECONDS", "300"))

# A repo with active development pushes at least daily; allow 2x
# buffer over a 24h push cadence before calling it stale.
EXPECTED_MAX_GAP_HOURS = int(os.environ.get("CI_HEARTBEAT_MAX_GAP_HOURS", "48"))

_KINDS = ("g1_route_sweep", "g15_dep_scan")


async def _check_kind_status(db, kind: str, now: dt.datetime) -> dict:
    """Last-seen age for one CI ingest `kind` vs EXPECTED_MAX_GAP_HOURS."""
    try:
        last = await db.synthetic_checks.find_one(
            {"kind": kind}, sort=[("finished_at", -1)],
        )
    except Exception as e:
        return {"error": str(e)[:200]}
    if not last:
        return {"last_seen_at": None, "age_hours": None,
                 "stale": True, "reason": "never_ingested"}
    finished = last.get("finished_at")
    if finished and finished.tzinfo is None:
        finished = finished.replace(tzinfo=dt.timezone.utc)
    age_hours = (now - finished).total_seconds() / 3600 if finished else None
    return {
        "last_seen_at": finished.isoformat() if finished else None,
        "age_hours": round(age_hours, 1) if age_hours is not None else None,
        "stale": age_hours is None or age_hours > EXPECTED_MAX_GAP_HOURS,
    }


async def heartbeat_status(db) -> dict:
    """Real read: last-seen age per kind vs EXPECTED_MAX_GAP_HOURS.
    Returns {available, kinds: {kind: {last_seen_at, age_hours, stale}}}."""
    if db is None:
        return {"available": False, "reason": "no_db"}
    now = dt.datetime.now(dt.timezone.utc)
    out = {"available": True, "checked_at": now.isoformat(),
           "expected_max_gap_hours": EXPECTED_MAX_GAP_HOURS, "kinds": {}}
    for kind in _KINDS:
        out["kinds"][kind] = await _check_kind_status(db, kind, now)
    return out


async def run_heartbeat_check(db) -> dict:
    """One check cycle: compute status, fire a deduped founder alert
    per stale kind. Alert dedup key includes the UTC date so it fires
    at most once per kind per day, not on every 6h cron tick."""
    status = await heartbeat_status(db)
    if not status.get("available"):
        return status
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    for kind, info in status["kinds"].items():
        if not info.get("stale"):
            continue
        try:
            await send_founder_alert(
                db,
                source_key=f"ci_ingest_heartbeat:{kind}:{today}",
                title=f"CI ingest heartbeat: {kind} has gone stale",
                detail=(
                    f"No new `{kind}` row in synthetic_checks for "
                    f"{info.get('age_hours', 'an unknown number of')} hours "
                    f"(expected within {EXPECTED_MAX_GAP_HOURS}h). Likely "
                    f"causes: AUREM_CI_INGEST_TOKEN misconfigured/missing "
                    f"where this ingest call runs, the ingest endpoint is "
                    f"down, or the CI job itself is failing before it "
                    f"reaches the ingest step."
                ),
                level="warning", guard="CI_HEARTBEAT",
            )
        except Exception as e:
            logger.warning("ci_ingest_heartbeat: alert send failed for %s: %r", kind, e)
    return status


async def ci_ingest_heartbeat_cron(db_getter=None) -> None:
    try:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
    except asyncio.CancelledError:
        raise
    while True:
        try:
            db = db_getter() if db_getter else None
            if db is None:
                logger.warning("ci_ingest_heartbeat_cron: no db available — skipping this cycle")
            else:
                await run_heartbeat_check(db)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("ci_ingest_heartbeat_cron loop error: %r", e)
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
