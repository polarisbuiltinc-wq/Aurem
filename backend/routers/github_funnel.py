"""
routers/github_funnel.py — GitHub Connect funnel telemetry (2026-08-01)

Tracks 5 real-user stages of the GitHub-connect flow so we can pinpoint
where the drop-off happens BEFORE guessing which specific UX bug to
fix. Data is collected from real new signups going forward; the
existing 41-user cohort is not backfilled (only forward events).

Stages (in order):
  1. cta_click        — user clicks "Connect GitHub" (frontend)
  2. oauth_redirect   — backend /connect redirects to github.com
  3. callback_received — backend /callback hit (success OR error)
  4. linked           — github.access_token stored in dev_users
  5. repo_selected    — user picks a repo (post-link action)

Anonymous ingestion (no auth required for /event) so pre-signup CTA
clicks are captured. Admin-only /stats aggregates counts per stage.

Mounted at /api/aurem-dev/funnel/github/*
"""
from __future__ import annotations
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/funnel/github", tags=["GitHub Funnel"])

# ── Canonical stage names ─────────────────────────────────────────────
STAGES = (
    "cta_click",
    "oauth_redirect",
    "callback_received",
    "linked",
    "repo_selected",
    # 2026-02-10 · Phase 2 · GitHub App install flow stages.
    "app_install_redirect",  # server: /github/app/install → GitHub
    "app_installed",         # server: /github/app/callback success
    "app_repo_selected",     # client: user picks a repo from installation
)

# Sources where the CTA lives — used to segment drop-off by entry point.
SOURCES = (
    "login",           # /login page GitHub button
    "signup",          # /signup page GitHub button
    "settings_card",   # GitHubCard on /settings
    "wizard",          # NewUserWizard step
    "projects",        # Projects page inline connect
    "unknown",         # fallback
)


class FunnelEvent(BaseModel):
    stage: str
    source: str = "unknown"
    session_id: str = Field(..., min_length=8, max_length=64)
    user_id: Optional[str] = None
    meta: Optional[dict] = None


async def track_server_side(
    stage: str,
    *,
    source: str = "unknown",
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    meta: Optional[dict] = None,
) -> None:
    """Fire-and-forget helper used by github_oauth.py at each
    backend milestone (oauth_redirect, callback_received, linked).

    Silent-fail: telemetry MUST never break the OAuth flow.
    """
    if stage not in STAGES:
        return
    db = get_db()
    if db is None:
        return
    try:
        doc = {
            "event_id":   uuid.uuid4().hex,
            "stage":      stage,
            "source":     source if source in SOURCES else "unknown",
            "session_id": session_id or f"srv:{uuid.uuid4().hex[:16]}",
            "user_id":    user_id,
            "meta":       meta or {},
            "ts":         time.time(),
            "created_at": datetime.now(timezone.utc),
            "origin":     "server",
        }
        await db.github_funnel_events.insert_one(doc)
    except Exception as e:
        # Never propagate — telemetry is best-effort.
        logger.warning(f"[funnel] server-side track failed: {e!r}")


@router.post("/event")
async def ingest_event(evt: FunnelEvent, request: Request) -> dict:
    """Anonymous ingestion endpoint. Frontend fires this at each
    client-side stage (cta_click, repo_selected). No auth required so
    logged-out users' CTA clicks on /login and /signup are captured.
    """
    if evt.stage not in STAGES:
        raise HTTPException(400, f"unknown stage: {evt.stage}")
    src = evt.source if evt.source in SOURCES else "unknown"
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    doc = {
        "event_id":   uuid.uuid4().hex,
        "stage":      evt.stage,
        "source":     src,
        "session_id": evt.session_id,
        "user_id":    evt.user_id,
        "meta":       evt.meta or {},
        "ts":         time.time(),
        "created_at": datetime.now(timezone.utc),
        "origin":     "client",
        # UA/host for later debugging — not PII-sensitive.
        "ua":         (request.headers.get("user-agent") or "")[:200],
    }
    await db.github_funnel_events.insert_one(doc)
    return {"ok": True, "event_id": doc["event_id"]}


@router.get("/stats")
async def funnel_stats(
    authorization: Optional[str] = Header(None),
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """Admin-only aggregate: counts per stage + per source in the
    last `days` window. Also computes conversion% between adjacent
    stages so the drop-off point is one glance away.
    """
    user = await current_dev(authorization)
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin only")
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    since = datetime.now(timezone.utc) - timedelta(days=days)
    match = {"created_at": {"$gte": since}}

    # Unique sessions that reached each stage — the funnel measure.
    per_stage: dict[str, int] = {s: 0 for s in STAGES}
    per_source_stage: dict[str, dict[str, int]] = {
        src: {s: 0 for s in STAGES} for src in SOURCES
    }

    pipeline = [
        {"$match": match},
        # Dedupe by (session_id, stage) so a single user firing the
        # same stage twice (e.g. rapid double-click) doesn't inflate.
        {"$group": {
            "_id":    {"session_id": "$session_id", "stage": "$stage",
                       "source": "$source"},
            "count":  {"$sum": 1},
        }},
    ]
    async for row in db.github_funnel_events.aggregate(pipeline):
        stage  = row["_id"]["stage"]
        source = row["_id"]["source"]
        if stage in per_stage:
            per_stage[stage] += 1
        if source in per_source_stage and stage in per_source_stage[source]:
            per_source_stage[source][stage] += 1

    # Compute stage-to-stage conversion %.
    conversions: list[dict] = []
    for i in range(len(STAGES) - 1):
        prev_s, next_s = STAGES[i], STAGES[i + 1]
        prev_n, next_n = per_stage[prev_s], per_stage[next_s]
        pct = (next_n / prev_n * 100.0) if prev_n > 0 else 0.0
        conversions.append({
            "from":     prev_s,
            "to":       next_s,
            "from_n":   prev_n,
            "to_n":     next_n,
            "conv_pct": round(pct, 1),
        })

    return {
        "ok":              True,
        "window_days":     days,
        "since":           since.isoformat(),
        "stages":          per_stage,
        "conversions":     conversions,
        "by_source":       per_source_stage,
        "total_events":    sum(per_stage.values()),
    }
