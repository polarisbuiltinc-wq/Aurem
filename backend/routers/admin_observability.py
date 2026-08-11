"""
routers/admin_observability.py — 2026-02-11 · Phase 1 hotspot audit companion

Exposes live circuit-breaker state per external service so a founder
can spot upstream degradation without tailing logs.

Pairs naturally with `services/http/client.py` — the shared HTTP
wrapper that landed in the same session. Every outbound HTTP call
through `ext_request()` records success/failure into
`services.retry_guard`'s per-dependency `CircuitBreaker`. This
endpoint surfaces those breaker snapshots.

Endpoints:
  GET  /admin/observability/breakers      — snapshot + recent transitions
  GET  /admin/observability/breakers/{dep}  — single-dep detail

Zero write endpoints intentionally — breakers self-heal via the
half-open probe mechanism; forced manual reset is a footgun.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from cto_services.auth import require_admin_dep
from services.retry_guard import (
    get_breaker, snapshot_all, recent_transitions, trip_counts_7d,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/aurem-dev/admin/observability",
    tags=["Admin-observability"],
    dependencies=[Depends(require_admin_dep)],
)


@router.get("/breakers")
async def all_breakers() -> dict:
    """Return live breaker snapshot for every known external dep +
    the last 50 state transitions + 7-day trip counts from Mongo.

    Frontend can poll this every 10-30s to render a cockpit card:
      dep | state | consecutive_fails | trip_count_7d | last_error
    """
    from cto_services.db import get_db
    db = get_db()
    trip_7d = await trip_counts_7d(db) if db is not None else {}

    breakers = snapshot_all()
    # Merge 7d trip counts into each snapshot for the UI.
    for dep, snap in breakers.items():
        snap["trip_count_7d"] = trip_7d.get(dep, 0)

    # Summary counts for a top-of-card badge.
    counts = {"closed": 0, "open": 0, "half_open": 0}
    for snap in breakers.values():
        counts[snap["state"]] = counts.get(snap["state"], 0) + 1

    return {
        "breakers":    breakers,          # {dep: snapshot_dict}
        "counts":      counts,            # {closed, open, half_open}
        "transitions": recent_transitions(limit=50),
        "healthy":     counts["open"] == 0,
    }


@router.get("/breakers/{dep}")
async def one_breaker(dep: str) -> dict:
    """Detail view for a single external dep — same snapshot shape as
    `all_breakers` but scoped to one dep, with the transition log
    filtered to that dep for a per-service audit trail."""
    if not dep or not dep.replace("_", "").isalnum():
        raise HTTPException(400, "invalid dep name")
    br = get_breaker(dep)
    from cto_services.db import get_db
    db = get_db()
    trip_7d = await trip_counts_7d(db) if db is not None else {}
    snap = br.snapshot()
    snap["trip_count_7d"] = trip_7d.get(dep, 0)
    return {
        "breaker":     snap,
        "transitions": [t for t in recent_transitions(limit=200)
                        if t.get("dep") == dep][-50:],
    }
