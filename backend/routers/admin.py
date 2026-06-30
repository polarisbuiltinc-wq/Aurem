"""
routers/admin.py — Admin panel endpoints.

All routes require a JWT with `is_admin: true`. The admin user is whoever
matches the email in env `ADMIN_EMAIL`; on login the existing auth router
sets `is_admin=true` for that user.

Mounted under /api/aurem-dev/admin/* by main.py.
"""
from __future__ import annotations

import logging
import os
import asyncio
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db, require_db
from services.usage import get_usage
# Iter 212m-71 — 60 s TTL cache for the heavy admin aggregations
# (activation funnel, dev_users buckets, etc.). Founders click around
# the admin panel rapidly; without this every click fires 5+ heavy
# aggregations against Mongo.
from services.admin_analytics_cache import (
    cached_agg,
    invalidate as _cache_invalidate,
    mongo_swr_cache,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


async def _require_admin(authorization: Optional[str]) -> dict:
    user = await current_dev(authorization)
    if user.get("is_admin"):
        return user
    # Stale-JWT escape hatch: the JWT might be from before the user was
    # promoted (e.g. founder allow-list added after their last login).
    # Trust the live DB row over the cached claim.
    db = get_db()
    if db is not None:
        row = await db.dev_users.find_one(
            {"user_id": user.get("user_id")},
            {"is_admin": 1, "tier": 1, "email": 1, "is_unlimited": 1},
        )
        if row and (row.get("is_admin") or row.get("tier") == "founder"):
            user["is_admin"] = True
            user["tier"] = row.get("tier") or user.get("tier")
            user["is_unlimited"] = bool(row.get("is_unlimited"))
            user["email"] = row.get("email") or user.get("email")
            return user
    raise HTTPException(403, "Admin access required")


# ── Auth check ──────────────────────────────────────────────────────────
@router.get("/me")
async def admin_me(authorization: Optional[str] = Header(None)):
    user = await _require_admin(authorization)
    return {"email": user.get("email"), "user_id": user.get("user_id"),
            "is_admin": True}


# ── Iter 210 — Audit feed (CitationGuard + ToolExecutor signals) ─────
@router.get("/audit")
async def audit_feed(
    limit:      int = 100,
    user_id:    Optional[str] = None,
    project_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    """Read the `ora_audit` collection. Backed by `audit_log.list_turns`.
    Used by the admin panel's Audit tab to surface every ORA turn,
    citation-guard triggers, and tool-error signals.
    """
    await _require_admin(authorization)
    from services.audit_log import list_turns
    rows = await list_turns(
        user_id=user_id,
        project_id=project_id,
        limit=max(1, min(int(limit or 100), 500)),
    )
    return {"ok": True, "rows": rows, "count": len(rows)}





# ── Dashboard ──────────────────────────────────────────────────────────
@router.get("/dashboard")
async def dashboard(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    day_ago = now - 86400

    total_users = await db.dev_users.count_documents({})
    total_tasks = await db.cto_tasks.count_documents({})
    tasks_today = await db.cto_tasks.count_documents({"created_at": {"$gte": day_ago}})
    failed_tasks = await db.cto_tasks.count_documents({"status": "failed"})
    done_tasks = await db.cto_tasks.count_documents({"status": "done"})
    total_projects = await db.cto_projects.count_documents({})
    total_sessions = await db.chat_sessions.count_documents({})

    recent_tasks = await db.cto_tasks.find(
        {}, {"_id": 0, "steps": 0, "rollback_steps": 0}
    ).sort("created_at", -1).limit(5).to_list(5)

    recent_users = await db.dev_users.find(
        {}, {"_id": 0, "password": 0, "password_hash": 0, "github.access_token": 0}
    ).sort("created_at", -1).limit(5).to_list(5)

    return {
        "total_users": total_users,
        "total_tasks": total_tasks,
        "tasks_today": tasks_today,
        "failed_tasks": failed_tasks,
        "done_tasks": done_tasks,
        "success_rate": round((done_tasks / max(total_tasks, 1)) * 100, 1),
        "total_projects": total_projects,
        "total_sessions": total_sessions,
        "recent_tasks": recent_tasks,
        "recent_users": recent_users,
    }



# ─── Iter 212m-153 — Production observability endpoint ────────────────
# Reads LIVE from the existing collections — no mock, no cache.  Mongo
# aggregations do the math in the DB.  Returns a single JSON snapshot
# for the SystemStatsPage admin dashboard.

@router.get("/system-stats")
async def admin_system_stats(
    window_hours: int = 24,
    authorization: Optional[str] = Header(None),
):
    """Aggregates the 4 observability collections written by recent
    iters (parliament_log, intent_classifications, quality_scores,
    plus syntax_gate metrics derived from chat.py logs)."""
    await _require_admin(authorization)
    db = require_db()
    now_ts  = time.time()
    cutoff  = now_ts - (max(1, min(window_hours, 24 * 30)) * 3600)
    cutoff_24h = now_ts - 86400

    # ── Parliament ────────────────────────────────────────────────
    parl_total = await db.parliament_log.count_documents(
        {"event": "aggregate", "ts": {"$gte": cutoff}}
    )
    parl_success = await db.parliament_log.count_documents(
        {"event": "aggregate", "status": "success",
         "ts": {"$gte": cutoff}}
    )
    parl_review = await db.parliament_log.count_documents(
        {"event": "aggregate", "status": "manual_review",
         "ts": {"$gte": cutoff}}
    )
    parl_cb_opens_24h = await db.parliament_log.count_documents(
        {"event": "circuit_open_fallback", "ts": {"$gte": cutoff_24h}}
    )
    # Council A member-win distribution.
    council_win_pipeline = [
        {"$match": {"event": "aggregate", "status": "success",
                    "council": "A", "ts": {"$gte": cutoff}}},
        {"$group": {"_id": "$winner", "n": {"$sum": 1}}},
    ]
    win_rows = await db.parliament_log.aggregate(council_win_pipeline).to_list(20)
    council_A_wins: dict[str, int] = {
        "A1-conservative": 0, "A2-balanced": 0, "A3-creative": 0,
    }
    for row in win_rows:
        key = row.get("_id") or "unknown"
        if key in council_A_wins:
            council_A_wins[key] = row["n"]
    avg_score_rows = await db.parliament_log.aggregate([
        {"$match": {"event": "aggregate", "status": "success",
                    "ts": {"$gte": cutoff}}},
        {"$project": {
            "top_score": {"$max": {
                "$map": {"input": {"$ifNull": ["$scores", []]},
                         "as": "s", "in": "$$s.score"}
            }}
        }},
        {"$group": {"_id": None, "avg": {"$avg": "$top_score"}}},
    ]).to_list(1)
    avg_score = round((avg_score_rows[0].get("avg") or 0.0), 3) \
        if avg_score_rows else 0.0
    success_pct = round(100 * parl_success / parl_total, 1) \
        if parl_total else 0.0

    # ── Intent Gateway ────────────────────────────────────────────
    intent_pipeline = [
        {"$match": {"ts": {"$gte": cutoff}}},
        {"$group": {"_id": "$tier", "n": {"$sum": 1},
                    "avg_conf": {"$avg": "$confidence"}}},
    ]
    intent_rows = await db.intent_classifications.aggregate(intent_pipeline).to_list(10)
    tier_dist = {"casual": 0, "query": 0, "agentic": 0, "clarify": 0}
    confs: list[float] = []
    for r in intent_rows:
        k = r.get("_id") or "unknown"
        if k in tier_dist:
            tier_dist[k] = r["n"]
        if r.get("avg_conf") is not None:
            confs.append(float(r["avg_conf"]))
    avg_conf = round(sum(confs) / len(confs), 3) if confs else 0.0
    llm_fallback_count = await db.intent_classifications.count_documents(
        {"method": "llm", "ts": {"$gte": cutoff}}
    )
    intent_total = sum(tier_dist.values())
    llm_fallback_pct = round(100 * llm_fallback_count / intent_total, 1) \
        if intent_total else 0.0

    # ── Tool Router (derived from intent_classifications + parliament_log) ─
    # We don't have a dedicated tool_router_log yet — the orchestrator
    # only emits a stdout log line.  Use a best-effort: distribution of
    # tiers acts as a proxy for groups (agentic → code), and we report
    # the average tools-injected as a derived constant for now.
    tool_calls_by_group = {
        "code":    tier_dist.get("agentic", 0),
        "query":   tier_dist.get("query", 0),
        "casual":  tier_dist.get("casual", 0),
        "web":     0,
        "deploy":  0,
        "debug":   0,
        "clarify": tier_dist.get("clarify", 0),
    }

    # ── Quality (will populate once Iter 212m-154 ships) ──────────
    q_count = await db.quality_scores.count_documents(
        {"timestamp_ts": {"$gte": cutoff}}
    ) if "quality_scores" in (await db.list_collection_names()) else 0
    q_avg = 0.0
    q_low = 0
    q_alerts_unacked = 0
    if q_count > 0:
        q_avg_rows = await db.quality_scores.aggregate([
            {"$match": {"timestamp_ts": {"$gte": cutoff}}},
            {"$group": {"_id": None, "avg": {"$avg": "$score"}}},
        ]).to_list(1)
        q_avg = round(q_avg_rows[0].get("avg") or 0.0, 3) if q_avg_rows else 0.0
        q_low = await db.quality_scores.count_documents(
            {"timestamp_ts": {"$gte": cutoff}, "score": {"$lt": 0.45}}
        )
        try:
            q_alerts_unacked = await db.quality_alerts.count_documents(
                {"acknowledged": False}
            )
        except Exception:
            q_alerts_unacked = 0

    return {
        "ok": True,
        "window_hours": window_hours,
        "parliament": {
            "total_runs":                  parl_total,
            "success_rate_pct":            success_pct,
            "avg_score":                   avg_score,
            "council_A_win_by_member":     council_A_wins,
            "circuit_breaker_opens_24h":   parl_cb_opens_24h,
            "manual_review_queue_count":   parl_review,
        },
        "tool_router": {
            "calls_by_group":      tool_calls_by_group,
            "avg_tools_injected":  None,    # populated