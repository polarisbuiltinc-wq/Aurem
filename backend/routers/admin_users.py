"""admin_users.py — User CRUD, grants, suspend, funnel/insights analytics.

Extracted from routers/admin.py during Phase 2 architecture split (2026-02-11).
Contains 20 handler(s)/helper(s):

  GET  /admin/me                     GET  /admin/github-sync
  GET  /admin/users                  GET  /admin/users/{user_id}
  POST /admin/users/{user_id}/grant-tokens
  POST /admin/users/{user_id}/enable-loop-beta
  POST /admin/users/{user_id}/suspend
  POST /admin/users/email-offer
  GET  /admin/funnel                 GET  /admin/insights/activation-funnel
  GET  /admin/insights/activation-funnel/stage-users
  GET  /admin/insights/first-message-sample
  GET  /admin/insights/user-patterns
  POST /admin/dev-users/backfill-created-at
  GET  /admin/dev-users/created-at-health
  POST /admin/loop-beta/kill-switch  GET  /admin/loop-beta/status

Every handler + helper is COPIED VERBATIM from the pre-split admin.py.
"""
from __future__ import annotations

import logging
import os
import asyncio
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Depends
from pydantic import BaseModel

from cto_services.auth import current_dev, require_admin_dep
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
# Iter 358 — router-level admin gate (defense-in-depth). EVERY route on
# this router is denied to non-founders at the router boundary, so a new
# endpoint added later is protected by default. Individual handlers keep
# their inline `await _require_admin(...)` too (harmless redundancy).
# The one intentionally-public sink (/admin/errors/report) lives on the
# separate, un-gated routers/admin_public.py at the same URL.

router = APIRouter(
    prefix="/admin", tags=["Admin-users"],
    dependencies=[Depends(require_admin_dep)],
)

from routers._admin_common import _require_admin  # noqa: E402
# 2026-02-11 · Phase 2 split fix — helper still lives in pre-split
# admin.py stub. Re-import so handlers resolve it at runtime.
from routers.admin import _compute_activation_funnel, _compute_stage_users  # noqa: E402


@router.get("/me")
async def admin_me(authorization: Optional[str] = Header(None)):
    user = await _require_admin(authorization)
    return {"email": user.get("email"), "user_id": user.get("user_id"),
            "is_admin": True}


@router.get("/github-sync")
async def github_sync_status(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    from routers.version import _COMMIT_SHA, _BUILT_AT
    from services.github_sync import get_github_sync
    return await get_github_sync(_COMMIT_SHA, _BUILT_AT, db=get_db())


@router.get("/users")
async def list_users(
    search: str = "",
    window: str = "all",
    authorization: Optional[str] = Header(None),
):
    """List users with optional search + signup-time window filter.

    `window` accepts: `24h`, `7d`, `30d`, `all`. Bucket counts for all
    three windows are returned in the same payload so the UI can render
    filter pills without an extra request.
    """
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    buckets = {
        "24h": now - 86_400,
        "7d":  now - 7 * 86_400,
        "30d": now - 30 * 86_400,
    }
    # Iter 212m-222 — the three signup paths historically wrote
    # `created_at` inconsistently: /auth/signup + /auth/google/session
    # wrote a `datetime`, /auth/github/callback wrote NOTHING at all.
    # Admin filters use float epoch, so BSON type-order + missing
    # fields together made most legacy users invisible. All three
    # writers now emit `time.time()` (float) but Mongo still has
    # datetime-typed rows from before the fix.
    #
    # Read-path tolerance: use `$or` on both types so a `datetime`-
    # typed row still matches the numeric window bound, and cast the
    # datetime cutoff to a `datetime.utcfromtimestamp` for the
    # datetime branch. Missing-field rows can't be reliably windowed;
    # they surface in `window="all"` and in the total count.
    from datetime import datetime as _dt, timezone as _tz
    def _window_query(cutoff: float) -> dict:
        _cutoff_dt = _dt.fromtimestamp(cutoff, tz=_tz.utc)
        return {"$or": [
            {"created_at": {"$gte": cutoff}},        # new float format
            {"created_at": {"$gte": _cutoff_dt}},    # legacy datetime rows
        ]}

    # Always compute the three bucket counts (cheap — one count_documents
    # each, all over an indexed `created_at`). These power the filter
    # pills in the admin UI.
    # Iter 212m-70 — N+1 fix. Was 3 separate count_documents calls
    # over the same indexed `created_at` field. Collapse into a single
    # aggregation pipeline: one round-trip, one index scan, all three
    # buckets returned together.
    # Iter 212m-222 — the pipeline now normalises both created_at
    # types into an epoch double before bucketing, so legacy datetime
    # rows are counted correctly.
    bucket_counts: dict[str, int] = {"24h": 0, "7d": 0, "30d": 0}
    try:
        pipeline = [
            {"$addFields": {
                "_created_ts": {
                    "$switch": {
                        "branches": [
                            {"case": {"$eq": [{"$type": "$created_at"}, "double"]},
                             "then": "$created_at"},
                            {"case": {"$eq": [{"$type": "$created_at"}, "long"]},
                             "then": "$created_at"},
                            {"case": {"$eq": [{"$type": "$created_at"}, "int"]},
                             "then": "$created_at"},
                            {"case": {"$eq": [{"$type": "$created_at"}, "date"]},
                             "then": {"$divide": [{"$toLong": "$created_at"}, 1000]}},
                        ],
                        "default": None,
                    }
                }
            }},
            {"$match": {"_created_ts": {"$gte": buckets["30d"]}}},
            {"$project": {
                "_id": 0,
                "is_24h": {"$gte": ["$_created_ts", buckets["24h"]]},
                "is_7d":  {"$gte": ["$_created_ts", buckets["7d"]]},
            }},
            {"$group": {
                "_id":     None,
                "in_24h":  {"$sum": {"$cond": ["$is_24h", 1, 0]}},
                "in_7d":   {"$sum": {"$cond": ["$is_7d",  1, 0]}},
                "in_30d":  {"$sum": 1},
            }},
        ]
        agg = await db.dev_users.aggregate(pipeline).to_list(1)
        if agg:
            bucket_counts["24h"] = int(agg[0].get("in_24h") or 0)
            bucket_counts["7d"]  = int(agg[0].get("in_7d")  or 0)
            bucket_counts["30d"] = int(agg[0].get("in_30d") or 0)
    except Exception as e:
        logger.warning("list_users bucket aggregation failed: %r", e)
    try:
        bucket_counts["all"] = await db.dev_users.count_documents({})
    except Exception:
        bucket_counts["all"] = 0

    query: dict = {}
    if search:
        query = {"$or": [
            {"email": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
        ]}
    if window in buckets:
        # Merge the window filter into the query. If a search filter
        # was already using $or, wrap with $and so both constraints apply.
        window_q = _window_query(buckets[window])
        if "$or" in query:
            query = {"$and": [query, window_q]}
        else:
            query.update(window_q)

    users = await db.dev_users.find(
        query, {"_id": 0, "password": 0, "password_hash": 0, "github.access_token": 0}
    ).sort("created_at", -1).limit(100).to_list(100)

    # Iter 120 — flatten 300 round-trips (3 count_documents per user) into
    # 3 grouped aggregations. Critical for /admin/users responsiveness
    # under load; the old pattern was an N+1 hotspot flagged by the
    # deployment agent and a candidate cause for OOM/timeout on Atlas
    # free tier where connection slots are limited.
    uids = [u.get("user_id", "") for u in users]
    if uids:
        async def _counts(coll_name: str) -> dict:
            coll = getattr(db, coll_name)
            cur = coll.aggregate([
                {"$match": {"user_id": {"$in": uids}}},
                {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
            ])
            return {row["_id"]: row["n"] async for row in cur}
        proj_counts = await _counts("cto_projects")
        task_counts = await _counts("cto_tasks")
        sess_counts = await _counts("chat_sessions")
    else:
        proj_counts = task_counts = sess_counts = {}

    for u in users:
        uid = u.get("user_id", "")
        u["project_count"] = proj_counts.get(uid, 0)
        u["task_count"]    = task_counts.get(uid, 0)
        u["session_count"] = sess_counts.get(uid, 0)
    return {"users": users, "bucket_counts": bucket_counts}


@router.get("/users/{user_id}")
async def get_user(user_id: str, authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    user = await db.dev_users.find_one(
        {"user_id": user_id},
        {"_id": 0, "password": 0, "password_hash": 0, "github.access_token": 0},
    )
    if not user:
        raise HTTPException(404, "User not found")
    user["projects"] = await db.cto_projects.find(
        {"user_id": user_id},
        {"_id": 0, "github_token": 0},
    ).to_list(50)
    user["recent_tasks"] = await db.cto_tasks.find(
        {"user_id": user_id},
        {"_id": 0, "steps": 0, "rollback_steps": 0},
    ).sort("created_at", -1).limit(20).to_list(20)
    user["project_count"] = len(user["projects"])
    user["task_count"] = await db.cto_tasks.count_documents({"user_id": user_id})
    user["session_count"] = await db.chat_sessions.count_documents({"user_id": user_id})
    # Live token budget (plan + admin-granted bonus - used)
    try:
        user["usage"] = await get_usage(user_id)
    except Exception as e:
        logger.warning(f"usage lookup failed for {user_id}: {e}")
        user["usage"] = None
    # Recent admin grants for this user
    user["token_grants"] = await db.cto_token_grants.find(
        {"user_id": user_id}, {"_id": 0},
    ).sort("granted_at", -1).limit(20).to_list(20)

    # 2026-08-20 — Funnel nudge emails sent to this user (stage, sent
    # time, click-through), from services/funnel_nudge_cron.py's
    # `onboarding_emails` audit collection.
    emails_sent_raw = await db.onboarding_emails.find(
        {"user_id": user_id, "campaign": "funnel_stage_nudge"},
        {"_id": 0, "user_id": 0, "email": 0},
    ).sort("sent_at", -1).to_list(50)
    user["emails_sent"] = [
        {
            "stage":       e.get("stage"),
            "sent_at":     e.get("sent_at").timestamp() if hasattr(e.get("sent_at"), "timestamp") else e.get("sent_at"),
            "sent_ok":     bool(e.get("sent_ok")),
            "clicked_at":  e.get("clicked_at").timestamp() if hasattr(e.get("clicked_at"), "timestamp") else e.get("clicked_at"),
            "click_count": e.get("click_count", 0),
        }
        for e in emails_sent_raw
    ]

    # ── 2026-02-12 · Admin user-detail expansion ──────────────────────
    # Founder asked for 3 additions on the admin user detail page:
    #   (1) Email User button — served client-side via mailto: with the
    #       fields we already return above (name, email, projects, tier,
    #       created_at). No backend work needed for that.
    #   (2) Activity Logs section — merged timeline from existing sources
    #       (funnel_events, cto_tasks, cto_token_grants, email verify used_at,
    #        promo_first50_claimed_at). No new logging surface.
    #   (3) Active Offers / Promo Status section — surfaces the user's
    #       First-50 promo state + any user_seo_claims (founder offer)
    #       plus a `tier_source` (paid / promo / free / founder).
    #
    # Both new sections are STRICTLY reads from existing collections — no
    # schema drift, no double-write risk.
    # ──────────────────────────────────────────────────────────────────

    # (2) Activity timeline — merge from existing sources, newest first.
    timeline: list[dict] = []
    # Signup / login / verify / first_task / first_ship funnel events.
    async for ev in db.funnel_events.find(
        {"user_id": user_id}, {"_id": 0},
    ).sort("ts_epoch", -1).limit(60):
        ts = ev.get("ts_epoch") or ev.get("created_at")
        if hasattr(ts, "timestamp"):
            ts = ts.timestamp()
        timeline.append({
            "kind":     "funnel",
            "type":     ev.get("event_type", ""),
            "at":       ts,
            "detail":   ev.get("metadata", {}),
        })
    # 2026-08-20 — GitHub-OAuth signup/link milestones (oauth_redirect,
    # callback_received, linked) were written to a SEPARATE collection
    # (`github_funnel_events`, see routers/github_funnel.py) that this
    # merge never queried — so any user who signed up via "Continue
    # with GitHub" (promoted on the signup page as the fastest path)
    # always showed "No activity recorded yet" here regardless of what
    # they'd actually done, making this exact investigation misleading.
    async for ev in db.github_funnel_events.find(
        {"user_id": user_id}, {"_id": 0},
    ).sort("ts", -1).limit(30):
        timeline.append({
            "kind":     "github_oauth",
            "type":     ev.get("stage", ""),
            "at":       ev.get("ts"),
            "detail":   {"source": ev.get("source"), **(ev.get("meta") or {})},
        })
    # 2026-08-20 — Project-connect success events. `cto_projects` rows
    # already exist (fetched above as `user["projects"]`) but were
    # never surfaced in this chronological timeline — a real project
    # add was invisible here unless it also happened to trigger a task.
    for p in user["projects"]:
        ts = p.get("created_at")
        if hasattr(ts, "timestamp"):
            ts = ts.timestamp()
        timeline.append({
            "kind":     "project",
            "type":     "project_connected",
            "at":       ts,
            "detail":   {
                "project_id": p.get("project_id"),
                "owner":      p.get("github_owner"),
                "repo":       p.get("github_repo"),
                "auth_method": p.get("auth_method"),
            },
        })
    # Tasks (task run + status).
    for t in user["recent_tasks"]:
        ts = t.get("created_at")
        if hasattr(ts, "timestamp"):
            ts = ts.timestamp()
        timeline.append({
            "kind":     "task",
            "type":     f"task_{t.get('status', 'unknown')}",
            "at":       ts,
            "detail":   {
                "task":       (t.get("task") or "")[:120],
                "status":     t.get("status"),
                "commit_sha": t.get("commit_sha"),
            },
        })
    # Admin token grants.
    for g in user["token_grants"]:
        timeline.append({
            "kind":     "admin",
            "type":     "token_grant",
            "at":       g.get("granted_at"),
            "detail":   {
                "tokens":     g.get("tokens"),
                "reason":     g.get("reason"),
                "granted_by": g.get("granted_by"),
            },
        })
    # Email verification click.
    ver = await db.email_verifications.find_one(
        {"user_id": user_id, "used_at": {"$ne": None}},
        {"_id": 0, "used_at": 1},
        sort=[("used_at", -1)],
    )
    if ver:
        used_at = ver.get("used_at")
        if hasattr(used_at, "timestamp"):
            used_at = used_at.timestamp()
        timeline.append({
            "kind": "auth", "type": "email_verified",
            "at": used_at, "detail": {},
        })
    # Promo First-50 claim (from dev_users itself).
    if user.get("promo_first50_claimed"):
        claim_ts = user.get("promo_first50_claimed_at")
        if hasattr(claim_ts, "timestamp"):
            claim_ts = claim_ts.timestamp()
        timeline.append({
            "kind": "offer", "type": "promo_first50_claimed",
            "at": claim_ts, "detail": {
                "pro_expires_at": (
                    user["pro_expires_at"].timestamp()
                    if hasattr(user.get("pro_expires_at"), "timestamp")
                    else user.get("pro_expires_at")
                ),
            },
        })
    # Sort merged timeline (drop rows with unusable ts).
    timeline = [t for t in timeline if isinstance(t.get("at"), (int, float))]
    timeline.sort(key=lambda x: x["at"], reverse=True)
    user["activity_timeline"] = timeline[:80]

    # (3) Active Offers / Promo Status — single source of truth.
    now_ts = time.time()
    pro_exp = user.get("pro_expires_at")
    pro_exp_epoch = (
        pro_exp.timestamp() if hasattr(pro_exp, "timestamp") else pro_exp
    )
    pro_active = bool(
        pro_exp_epoch and isinstance(pro_exp_epoch, (int, float))
        and pro_exp_epoch > now_ts
    )
    tier = user.get("tier") or "free"
    # tier_source discriminator — is Pro from promo or a real Stripe sub?
    if tier == "founder":
        tier_source = "founder"
    elif user.get("promo_first50_claimed") and pro_active:
        tier_source = "promo_first50"   # ← don't dangle offers at these users
    elif user.get("stripe_subscription_id") or user.get("subscription_id"):
        tier_source = "paid_subscription"
    elif tier != "free":
        tier_source = "paid_or_unknown"
    else:
        tier_source = "free"

    # Founder-offer per-user claims (user_seo_claims collection).
    seo_claims_raw = await db.user_seo_claims.find(
        {"user_id": user_id}, {"_id": 0},
    ).sort("created_at", -1).limit(20).to_list(20)
    seo_claims = []
    for c in seo_claims_raw:
        created_at = c.get("created_at")
        if hasattr(created_at, "timestamp"):
            created_at = created_at.timestamp()
        seo_claims.append({
            "claim_id":   c.get("claim_id"),
            "repo_id":    c.get("repo_id"),
            "site_url":   c.get("site_url"),
            "fix_status": c.get("fix_status"),
            "created_at": created_at,
        })

    user["offers"] = {
        "tier":        tier,
        "tier_source": tier_source,
        "first50": {
            "claimed":         bool(user.get("promo_first50_claimed")),
            "claimed_at":      (
                user["promo_first50_claimed_at"].timestamp()
                if hasattr(user.get("promo_first50_claimed_at"), "timestamp")
                else user.get("promo_first50_claimed_at")
            ),
            "pro_expires_at":  pro_exp_epoch,
            "pro_active":      pro_active,
            "days_left":       (
                round((pro_exp_epoch - now_ts) / 86400, 1)
                if pro_active else None
            ),
        },
        "founder_offer": {
            "claim_count":   len(seo_claims),
            "active_claims": [c for c in seo_claims
                              if c.get("fix_status") not in
                                 ("cancelled", "shipped", "failed")],
            "all_claims":    seo_claims,
        },
    }

    # (4) Support tickets — last 20 for this user. Same collection the
    # admin Support panel reads, projected down to the fields the user
    # detail UI shows.
    tickets_cur = db.cto_support.find(
        {"user_id": user_id},
        {"_id": 0, "ticket_id": 1, "subject": 1, "status": 1, "source": 1,
         "created_at": 1, "updated_at": 1, "last_reply_at": 1},
    ).sort("updated_at", -1).limit(20)
    user["support_tickets"] = await tickets_cur.to_list(20)

    return user


class GrantTokensBody(BaseModel):
    tokens: int
    reason: str = ""


@router.post("/users/{user_id}/grant-tokens")
async def grant_tokens(
    user_id: str,
    body: GrantTokensBody,
    authorization: Optional[str] = Header(None),
):
    """Admin manually credits a user with bonus tokens.

    Bonus tokens are tracked SEPARATELY on `dev_users.tokens_granted` and added
    on top of the plan limit (see `services.usage.get_usage`). Every grant is
    appended to `cto_token_grants` for audit.
    """
    admin = await _require_admin(authorization)
    db = require_db()
    if not isinstance(body.tokens, int) or body.tokens <= 0:
        raise HTTPException(400, "tokens must be a positive integer")
    if body.tokens > 10_000_000:
        raise HTTPException(400, "tokens grant too large (max 10M)")
    target = await db.dev_users.find_one({"user_id": user_id}, {"user_id": 1})
    if not target:
        raise HTTPException(404, "User not found")

    now = time.time()
    await db.dev_users.update_one(
        {"user_id": user_id},
        {"$inc": {"tokens_granted": body.tokens}, "$set": {"updated_at": now}},
    )
    await db.cto_token_grants.insert_one({
        "user_id": user_id,
        "tokens": body.tokens,
        "reason": (body.reason or "").strip()[:500],
        "granted_by": admin.get("email") or admin.get("user_id"),
        "granted_at": now,
    })
    usage = await get_usage(user_id)
    return {"ok": True, "granted": body.tokens, "usage": usage}


class SuspendBody(BaseModel):
    suspend: bool


class LoopBetaBody(BaseModel):
    enabled: bool


@router.post("/users/{user_id}/enable-loop-beta")
async def enable_loop_beta(
    user_id: str,
    body: LoopBetaBody,
    authorization: Optional[str] = Header(None),
):
    """Iter 364 · Phase-2 — admin toggles `loop_beta_enabled` on a
    single dev_users doc so a Pro/Team user can pass the tiered gate
    in `routers/loop.py::start_loop`. Founders always bypass the flag."""
    admin = await _require_admin(authorization)
    db = require_db()
    target = await db.dev_users.find_one(
        {"user_id": user_id}, {"_id": 0, "user_id": 1, "email": 1, "tier": 1},
    )
    if not target:
        raise HTTPException(404, "User not found")
    now = time.time()
    await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {
            "loop_beta_enabled":         bool(body.enabled),
            "loop_beta_updated_at":      now,
            "loop_beta_updated_by":      admin.get("email") or admin.get("user_id"),
        }},
    )
    return {
        "ok":                True,
        "user_id":           user_id,
        "email":             target.get("email"),
        "tier":              target.get("tier"),
        "loop_beta_enabled": bool(body.enabled),
    }


class KillSwitchBody(BaseModel):
    enabled: bool
    reason:  Optional[str] = ""


@router.post("/loop-beta/kill-switch")
async def toggle_loop_kill_switch(
    body: KillSwitchBody,
    authorization: Optional[str] = Header(None),
):
    """Iter 364 · Phase-3 — flip the DB-backed kill switch. When ON,
    every user (including founders) sees a 403 on /loop/start until
    it's flipped OFF. Env var LOOP_MODE_KILL_SWITCH=true is an
    orthogonal override (env wins if set)."""
    admin = await _require_admin(authorization)
    db = require_db()
    from services import loop_beta as _lb
    await _lb.set_kill_switch(
        db, on=bool(body.enabled),
        reason=(body.reason or "").strip()[:400] or (
            f"manual flip by {admin.get('email') or admin.get('user_id')}"),
    )
    return {
        "ok":                     True,
        "kill_switch_enabled":    bool(body.enabled),
        "env_override_wins":      bool(os.environ.get("LOOP_MODE_KILL_SWITCH", "").lower()
                                        in ("1", "true", "yes", "on")),
    }


@router.get("/loop-beta/status")
async def loop_beta_status(authorization: Optional[str] = Header(None)):
    """Iter 364 · Phase-3 — dashboard snapshot for the admin QA UI.
    Iter 212m-182 · Guard 21 — also returns gate-parity telemetry so a
    future /loop/start vs /chat/stream drift (the 212m-181 bug class)
    surfaces here instead of waiting on a founder report."""
    await _require_admin(authorization)
    db = require_db()
    from services import loop_beta as _lb
    n_active = await db.loop_sessions.count_documents({
        "state": {"$in": list(_lb._ACTIVE_STATES)},
    })
    n_beta   = await db.dev_users.count_documents({"loop_beta_enabled": True})
    n_stuck  = await _lb.count_stuck_loops(db)
    row = await db.system_flags.find_one({"key": "loop_mode_kill_switch"}) or {}
    gate_parity = await _lb.gate_parity_check(db)
    return {
        "kill_switch_db":        bool(row.get("value")),
        "kill_switch_env":       os.environ.get("LOOP_MODE_KILL_SWITCH", ""),
        "kill_switch_reason":    row.get("reason"),
        "beta_users":            n_beta,
        "active_loops":          n_active,
        "stuck_last_10min":      n_stuck,
        "stuck_trip_threshold":  _lb.LOOP_STUCK_TRIP_THRESHOLD,
        "max_concurrent_per_user": _lb.LOOP_MAX_CONCURRENT_PER_USER,
        "maxx_daily_cap":        _lb.MAXX_DAILY_TASK_CAP,
        "gate_parity":           gate_parity,
    }


@router.get("/funnel")
async def admin_funnel_dashboard(
    days: int = 30,
    authorization: Optional[str] = Header(None),
):
    """Iter 365 · Phase 3 — signup / activation / retention funnel.

    Returns:
      signups            : count of users who completed signup in the window
      first_chat_pct     : % of those who ever sent a chat
      first_ship_pct     : % who ever shipped a task (REAL activation KPI)
      time_to_first_ship_median_h : median hours from signup to first ship
      d7_retention_pct   : % who did anything on day 7+
      d30_retention_pct  : % who did anything on day 30+
      event_counts       : total funnel_events row count by type in the window
    """
    await _require_admin(authorization)
    db = require_db()
    days = max(1, min(int(days or 30), 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_epoch = since.timestamp()

    # Signup cohort in the window (from dev_users.created_at float epoch).
    cohort_cursor = db.dev_users.find(
        {"created_at": {"$gte": since_epoch}},
        {"_id": 0, "user_id": 1, "email": 1, "created_at": 1,
         "first_chat_at": 1, "first_loop_at": 1, "first_ship_at": 1,
         "is_admin": 1, "is_unlimited": 1, "tier": 1},
    )
    cohort = []
    async for u in cohort_cursor:
        if u.get("is_admin") or u.get("is_unlimited") or u.get("tier") == "founder":
            continue          # exclude internal accounts from funnel math
        cohort.append(u)

    n = len(cohort)
    if n == 0:
        return {
            "window_days":                days,
            "signups":                    0,
            "first_chat_pct":             0.0,
            "first_ship_pct":             0.0,
            "time_to_first_ship_median_h": None,
            "d7_retention_pct":           0.0,
            "d30_retention_pct":          0.0,
            "event_counts":               {},
            "nudge_stages":               {"stuck": {}, "nudges_sent": {}, "nudges_sent_total": 0,
                                            "nudges_clicked": {}, "nudges_clicked_total": 0},
        }

    n_chat = sum(1 for u in cohort if u.get("first_chat_at"))
    n_ship = sum(1 for u in cohort if u.get("first_ship_at"))

    # Median time-to-first-ship (in hours).
    ttfs = sorted(
        (u["first_ship_at"] - u["created_at"]) / 3600
        for u in cohort
        if u.get("first_ship_at") and u.get("created_at")
    )
    if ttfs:
        mid = len(ttfs) // 2
        median = ttfs[mid] if len(ttfs) % 2 else (ttfs[mid - 1] + ttfs[mid]) / 2
    else:
        median = None

    # Retention — did the user have ANY funnel event or chat/loop
    # activity ≥N days after signup.
    now_epoch = time.time()
    d7_hits  = 0
    d30_hits = 0
    for u in cohort:
        signup_ts = u.get("created_at") or 0
        marks = [
            u.get("first_chat_at") or 0,
            u.get("first_loop_at") or 0,
            u.get("first_ship_at") or 0,
        ]
        # We use max(last activity marker) as a proxy for "still active".
        latest = max(marks) if any(marks) else 0
        age_days = (now_epoch - signup_ts) / 86400 if signup_ts else 0
        if age_days >= 7 and latest and (latest - signup_ts) >= 7 * 86400:
            d7_hits += 1
        if age_days >= 30 and latest and (latest - signup_ts) >= 30 * 86400:
            d30_hits += 1
    # Denominators: only cohort users old enough to have crossed the mark.
    n_eligible_7  = sum(1 for u in cohort
                        if (now_epoch - (u.get("created_at") or 0)) / 86400 >= 7)
    n_eligible_30 = sum(1 for u in cohort
                        if (now_epoch - (u.get("created_at") or 0)) / 86400 >= 30)

    # Event counts from funnel_events for extra observability.
    ev_counts: dict[str, int] = {}
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {"_id": "$event_type", "n": {"$sum": 1}}},
        ]
        async for row in db.funnel_events.aggregate(pipeline):
            ev_counts[row["_id"] or "?"] = int(row["n"])
    except Exception:
        pass

    def _pct(hits: int, total: int) -> float:
        return round((hits / total) * 100, 1) if total else 0.0

    # 2026-08-20 — stage-aware nudge visibility (stuck-stage counts +
    # nudges actually sent). Independent of the `days` window above.
    nudge_stats = {"stuck": {}, "nudges_sent": {}, "nudges_sent_total": 0,
                   "nudges_clicked": {}, "nudges_clicked_total": 0}
    try:
        from services.funnel_nudge_cron import stage_counts
        nudge_stats = await stage_counts(db)
    except Exception:
        pass

    return {
        "window_days":                days,
        "signups":                    n,
        "first_chat_pct":             _pct(n_chat, n),
        "first_ship_pct":             _pct(n_ship, n),
        "time_to_first_ship_median_h": round(median, 2) if median else None,
        "d7_retention_pct":           _pct(d7_hits, n_eligible_7),
        "d30_retention_pct":          _pct(d30_hits, n_eligible_30),
        "event_counts":               ev_counts,
        "nudge_stages":               nudge_stats,
    }


@router.post("/users/{user_id}/suspend")
async def toggle_suspend(
    user_id: str,
    body: SuspendBody,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    status = "suspended" if body.suspend else "active"
    r = await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {"status": status, "status_changed_at": time.time()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "User not found")
    return {"ok": True, "status": status}


@router.delete("/users/{user_id}")
async def admin_delete_user(
    user_id: str,
    authorization: Optional[str] = Header(None),
):
    """Hard-delete a user and cascade across owned collections."""
    actor = await _require_admin(authorization)
    db = require_db()

    # Look up the target — also gives us the email for the founder check
    # and audit log.
    target = await db.dev_users.find_one({"user_id": user_id}, {"_id": 0, "email": 1, "name": 1})
    if not target:
        raise HTTPException(404, "User not found")

    target_email = (target.get("email") or "").strip().lower()
    # Refuse to delete a founder. Founders are baked into FOUNDER_EMAILS
    # at deploy time; wiping one would brick login + billing.
    founder_list = [
        e.strip().lower() for e in
        (os.environ.get("FOUNDER_EMAILS") or "").split(",") if e.strip()
    ]
    if target_email and target_email in founder_list:
        raise HTTPException(403, f"Refusing to delete founder account ({target_email})")
    # Belt + suspenders — never let an admin delete themselves either.
    if user_id == actor.get("user_id"):
        raise HTTPException(403, "Cannot delete your own account from this UI")

    # Iter 388t · GDPR self-serve delete refactor.  Reuse the shared
    # cascade helper so admin-initiated + self-serve delete follow the
    # exact same purge path — including Stripe subscription cancel and
    # GitHub App revocation which the old inline cascade skipped.
    from services.user_deletion import cascade_delete_user_data
    report = await cascade_delete_user_data(db, user_id)
    deletions = report.get("deletions") or {}

    logger.info(
        "user deleted by admin=%s target_user=%s target_email=%s "
        "stripe_cancelled=%s github_revoked=%d deletions=%s",
        actor.get("email"), user_id, target_email,
        report.get("stripe_cancelled"),
        len(report.get("github_revoked") or []),
        deletions,
    )
    return {
        "ok":               True,
        "user_id":          user_id,
        "email":            target_email,
        "deletions":        deletions,
        "stripe_cancelled": report.get("stripe_cancelled"),
        "github_revoked":   report.get("github_revoked"),
    }


@router.post("/users/email-offer")
async def admin_send_user_offer(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Send a one-off offer email to a list of users.

      Request body:
        {
          "user_ids": ["uid_..", "uid_.."],   # required, max 500
          "subject":  "Special offer for you",
          "body_html": "<p>Hi {{name}}, ...</p>",   # supports {{name}} and {{email}}
          "from": "ORA <ora@aurem.live>",   # optional, falls back to DIGEST_FROM
          "reply_to": "polarisbuiltinc@gmail.com",  # optional, falls back to REPLY_TO_EMAIL env or the current support inbox
        }

    Returns: {sent, failed, dry_run, recipients[]}
    """
    actor = await _require_admin(authorization)
    db = require_db()

    user_ids = (body or {}).get("user_ids") or []
    subject  = ((body or {}).get("subject") or "").strip()
    body_html = ((body or {}).get("body_html") or "").strip()
    from_addr = ((body or {}).get("from") or "").strip()
    # 2026-02-12 · reply_to fallback chain: caller > REPLY_TO_EMAIL env >
    # empty (skip header). Never falls back to a hardcoded no-MX address.
    from services.email_reply_to import get_reply_to
    reply_to = (((body or {}).get("reply_to") or "").strip()
                or get_reply_to() or "")

    if not isinstance(user_ids, list) or not user_ids:
        raise HTTPException(400, "user_ids[] required (non-empty)")
    if len(user_ids) > 500:
        raise HTTPException(400, "Too many recipients (max 500 per batch)")
    if not subject:
        raise HTTPException(400, "subject required")
    if not body_html:
        raise HTTPException(400, "body_html required")

    # Resolve emails. Project only what we need.
    cursor = db.dev_users.find(
        {"user_id": {"$in": user_ids}},
        {"_id": 0, "user_id": 1, "email": 1, "name": 1},
    )
    targets: list[dict] = []
    async for u in cursor:
        if u.get("email"):
            targets.append(u)

    if not targets:
        raise HTTPException(404, "No valid emails found for the given user_ids")

    resend_key = os.environ.get("RESEND_API_KEY") or ""
    sender = from_addr or os.environ.get("DIGEST_FROM") or os.environ.get(
        "RESEND_FROM_EMAIL"
    ) or "AUREM <onboarding@resend.dev>"

    # When the API key is missing we record what *would* have been sent
    # so the UI can still display recipient counts (and ops can see what
    # was queued during a Resend outage).
    if not resend_key:
        logger.warning(
            "email-offer dry-run (no RESEND_API_KEY): admin=%s recipients=%d subject=%r",
            actor.get("email"), len(targets), subject,
        )
        return {
            "ok":         True,
            "dry_run":    True,
            "sent":       0,
            "failed":     0,
            "recipients": [t["email"] for t in targets],
            "reply_to":   reply_to,
            "note":       "RESEND_API_KEY not configured — no emails actually sent.",
        }

    # Fire all sends concurrently. Per-recipient template substitution.
    import httpx
    from services.http import ext_client

    async def _send_one(target: dict) -> tuple[str, bool, str]:
        name = (target.get("name") or "").strip() or "there"
        email = target["email"]
        personalized = (body_html
                        .replace("{{name}}", name)
                        .replace("{{email}}", email))
        try:
            async with ext_client("resend", timeout=httpx.Timeout(15.0)) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {resend_key}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "from":    sender,
                        "to":      [email],
                        "subject": subject,
                        "html":    personalized,
                        **({"reply_to": reply_to} if reply_to else {}),
                    },
                )
            if resp.status_code < 300:
                return (email, True, "")
            return (email, False, f"http_{resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            return (email, False, str(e)[:200])

    results = await asyncio.gather(
        *[_send_one(t) for t in targets],
        return_exceptions=False,
    )
    sent = sum(1 for _, ok, _ in results if ok)
    failed = [{"email": e, "error": err}
              for e, ok, err in results if not ok]

    # Persist a record so support can audit what we promised users.
    try:
        await db.email_offers.insert_one({
            "_id":        f"offer_{int(time.time())}_{actor.get('user_id', 'anon')}",
            "admin_id":   actor.get("user_id"),
            "admin_email": actor.get("email"),
            "subject":    subject,
            "body_html":  body_html,
            "from":       sender,
            "reply_to":   reply_to,
            "recipient_count": len(targets),
            "sent_count":      sent,
            "failed_count":    len(failed),
            "failed":     failed[:50],   # cap for storage hygiene
            "created_at": time.time(),
        })
    except Exception as e:
        logger.warning("email-offer ledger insert failed: %r", e)

    logger.info(
        "email-offer sent by admin=%s recipients=%d sent=%d failed=%d subject=%r",
        actor.get("email"), len(targets), sent, len(failed), subject,
    )
    return {
        "ok":         True,
        "dry_run":    False,
        "sent":       sent,
        "failed":     len(failed),
        "failed_detail": failed[:20],
        "recipients": [t["email"] for t in targets],
        "reply_to":   reply_to,
    }


@router.get("/insights/dora")
async def dora_metrics_endpoint(
    period_days: int = 30, env: str = "production",
    authorization: Optional[str] = Header(None),
) -> dict:
    """2026-08-24 · Guard 22 — Phase 5.3 blueprint gap (was 1/10, not
    built at all). 4 standard DORA metrics computed purely from
    existing collections (deploy_events, rollback_attempts,
    incidents) — no new event infra."""
    await _require_admin(authorization)
    db = require_db()
    from services.dora_metrics import compute_dora
    period_days = max(1, min(period_days, 365))
    return await compute_dora(db, period_days=period_days, env=env or None)


@router.get("/insights/slo")
async def slo_metrics_endpoint(
    period_days: int = 7,
    authorization: Optional[str] = Header(None),
) -> dict:
    """2026-08-26 — Blueprint Phase 5.3 gap ("decide SLOs before you
    need them for a postmortem"). Two declared SLOs (chat response,
    ship completion) with real rolling p50/p95 computed purely from
    existing collections (health_endpoint_latency, cto_tasks) — no
    new event infra. See services/slo_metrics.py for target rationale."""
    await _require_admin(authorization)
    db = require_db()
    from services.slo_metrics import compute_slo
    period_days = max(1, min(period_days, 90))
    return await compute_slo(db, period_days=period_days)


@router.post("/github-app/repair-orphaned-installations")
async def repair_orphaned_installations(
    dry_run: bool = True,
    authorization: Optional[str] = Header(None),
) -> dict:
    """2026-08-26 — root-cause backfill for the reconnect bug (see
    `routers/cto_projects.py::update_project` + `services/github_app.py
    ::verify_installation_for_repo`): the PATCH reconnect path used to
    set `auth_method="github_app"` without ever setting
    `installation_active`, which is the exact flag `PatRequiredCTA.jsx`
    gates the "not connected" banner on — a real installation could
    work while the banner never cleared.

    Finds every `cto_projects` row with `auth_method="github_app"`,
    a real `installation_id`, and `installation_active` missing/false
    (the orphaned-link pattern) — the fix going forward stops new
    ones; this repairs ones that already exist. For each, re-verifies
    live GitHub access (same helper as a real reconnect) before
    flipping the flag — never blindly trusts the stored ID.

    `dry_run=True` (default) only reports what WOULD change — nothing
    is written. Call with `dry_run=false` to actually repair.
    """
    await _require_admin(authorization)
    db = require_db()
    from services.github_app import verify_installation_for_repo

    orphans = await db.cto_projects.find({
        "auth_method": "github_app",
        "installation_id": {"$exists": True, "$ne": None},
        "$or": [
            {"installation_active": {"$exists": False}},
            {"installation_active": False},
            {"installation_active": None},
        ],
    }, {
        "_id": 0, "project_id": 1, "user_id": 1, "github_owner": 1,
        "github_repo": 1, "installation_id": 1, "name": 1,
    }).to_list(1000)

    repaired, still_broken = [], []
    for proj in orphans:
        ok, err_code, err_msg = await verify_installation_for_repo(
            db, user_id=proj["user_id"],
            installation_id=int(proj["installation_id"]),
            owner=proj.get("github_owner") or "", repo=proj.get("github_repo") or "",
        )
        row = {
            "project_id":      proj["project_id"],
            "name":            proj.get("name"),
            "user_id":         proj["user_id"],
            "installation_id": proj.get("installation_id"),
        }
        if ok:
            if not dry_run:
                await db.cto_projects.update_one(
                    {"project_id": proj["project_id"]},
                    {"$set": {"installation_active": True}},
                )
            repaired.append(row)
        else:
            still_broken.append({**row, "error_code": err_code, "error": err_msg})

    return {
        "dry_run":        dry_run,
        "scanned":        len(orphans),
        "repaired":       repaired,
        "repaired_count": len(repaired),
        "still_broken":   still_broken,
        "still_broken_count": len(still_broken),
    }



@router.get("/insights/activation-funnel")
async def activation_funnel(
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)

    # Iter 212m-154 — Mongo-backed Stale-While-Revalidate cache.
    #
    # The previous 60 s in-process cache (iter 212m-71) helped warm
    # reads but did NOTHING for cold-start admin loads — every fresh
    # uvicorn worker / pod restart paid the full ~6 s aggregation
    # cost on the first request, which the frontend AbortController
    # cancelled with HTTP 499 (caught in iter 212m-153 prod QA).
    #
    # SWR pattern: persist the last-known-good funnel result in a
    # Mongo doc.  Every read returns it in <50 ms even when stale.
    # When past the 5-minute TTL, a background task refreshes the
    # doc without blocking the request.  First-ever boot (no doc)
    # is capped at 4 s — anything slower returns a "warming" skeleton.
    return await mongo_swr_cache(
        key="admin:activation_funnel:v1",
        ttl_seconds=300.0,         # 5 min freshness target
        builder=_compute_activation_funnel,
        hard_timeout=4.0,          # never block the frontend past abort threshold
    )


@router.get("/insights/activation-funnel/stage-users")
async def activation_funnel_stage_users(
    stage: str,
    authorization: Optional[str] = Header(None),
):
    """2026-08-20 — clickable drill-down for the Activation Funnel:
    the real users currently stuck at one specific stage, with email,
    name, when they reached that stage, and how long they've been
    stuck there (longest-stuck first). Not cached (on-demand click,
    not a hot path)."""
    await _require_admin(authorization)
    return await _compute_stage_users(stage)


@router.get("/insights/first-message-sample")
async def first_message_sample(
    limit: int = 15,
    authorization: Optional[str] = Header(None),
):
    """2026-08-22 — one-off investigation endpoint: for real (non-test)
    users who HAVE sent at least one chat message, what did their very
    first message (across all their sessions) look like? Answers "do
    users know what to type, or do they send vague 1-word messages?"

    Not cached (on-demand, low-traffic diagnostic, not a hot path)."""
    await _require_admin(authorization)
    db = require_db()
    from services.test_accounts import is_test_email as is_test

    limit = max(1, min(int(limit or 15), 100))

    real_ids = [
        u["user_id"]
        async for u in db.dev_users.find(
            {}, {"_id": 0, "user_id": 1, "email": 1},
        )
        if not is_test(u.get("email")) and u.get("user_id")
    ]
    if not real_ids:
        return {"ok": True, "count": 0, "stats": {}, "samples": []}

    rows = await db.chat_sessions.aggregate([
        {"$match": {"user_id": {"$in": real_ids}, "turns.0": {"$exists": True}}},
        {"$unwind": "$turns"},
        {"$match": {"turns.role": "user"}},
        {"$sort": {"user_id": 1, "turns.ts": 1}},
        {"$group": {
            "_id": "$user_id",
            "first_ts":      {"$first": "$turns.ts"},
            "first_content": {"$first": "$turns.content"},
        }},
    ]).to_list(5000)

    lengths = sorted(len((r.get("first_content") or "")) for r in rows)
    n = len(lengths)
    stats = {}
    if n:
        short_le_15 = sum(1 for l in lengths if l <= 15)
        stats = {
            "count":        n,
            "min_len":      lengths[0],
            "max_len":      lengths[-1],
            "median_len":   lengths[n // 2],
            "mean_len":     round(sum(lengths) / n, 1),
            "short_le_15_count": short_le_15,
            "short_le_15_pct":   round(100.0 * short_le_15 / n, 1),
        }

    rows.sort(key=lambda r: r.get("first_ts") or 0, reverse=True)
    samples = [
        {"length": len(r.get("first_content") or ""),
         "content": (r.get("first_content") or "")[:300]}
        for r in rows[:limit]
    ]

    return {"ok": True, "stats": stats, "samples": samples}


@router.get("/insights/user-patterns")
async def user_patterns_insights(
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()

    from collections import Counter

    # Pull every pattern doc. The collection is small by design (1 doc
    # per (user_id, project_id) tuple) so a full scan is fine; can be
    # replaced with a server-side $unwind aggregation if it grows.
    docs = await db.ora_patterns.find(
        {},
        {"_id": 0, "user_id": 1, "hot_files": 1,
         "stack_signals": 1, "session_count": 1},
    ).to_list(5000)

    file_counter: Counter[str] = Counter()
    stack_counter: Counter[str] = Counter()
    user_ids: set[str] = set()
    total_sessions = 0

    for d in docs:
        uid = d.get("user_id")
        if uid:
            user_ids.add(uid)
        total_sessions += int(d.get("session_count") or 0)
        for f in (d.get("hot_files") or []):
            if isinstance(f, str) and f:
                file_counter[f] += 1
        for s in (d.get("stack_signals") or []):
            if isinstance(s, str) and s:
                stack_counter[s] += 1

    top_files = [
        {"file": f, "user_count": n}
        for f, n in file_counter.most_common(10)
    ]
    top_stack = [
        {"signal": s, "count": n}
        for s, n in stack_counter.most_common(20)
    ]

    return {
        "ok": True,
        "top_files":           top_files,
        "stack_distribution":  top_stack,
        "users_with_patterns": len(user_ids),
        "total_sessions":      total_sessions,
        "records":             len(docs),
    }


@router.post("/dev-users/backfill-created-at")
async def admin_backfill_dev_users_created_at(
    authorization: Optional[str] = Header(None),
) -> dict:
    """P0 recovery endpoint (founder-only) — repair legacy `dev_users`
    rows whose `created_at` is either datetime-typed OR missing.
    Same logic as the startup task in `main.py:_backfill_dev_users_created_at`.

    Returns:
        {
            ok: True,
            datetime_fixed: int,        # rows converted from date → float
            missing_filled: int,        # rows where created_at was absent
            still_pending:   int,       # rows still on legacy shape (should be 0)
            total_users:     int,
        }
    """
    await _require_admin(authorization)
    db = require_db()
    _now = time.time()

    # 1. datetime → float (server-side coercion via aggregation pipeline).
    r1 = await db.dev_users.update_many(
        {"created_at": {"$type": "date"}},
        [{"$set": {"created_at":
            {"$divide": [{"$toLong": "$created_at"}, 1000]}}}],
    )
    # 2. Missing → best-effort backfill. Prefer github/google connected_at.
    r2 = await db.dev_users.update_many(
        {"created_at": {"$exists": False}},
        [{"$set": {"created_at": {
            "$ifNull": [
                "$github.connected_at",
                {"$ifNull": ["$google.connected_at", _now]},
            ]
        }}}],
    )
    # 3. Verify nothing is left on the legacy shape.
    still_datetime = await db.dev_users.count_documents(
        {"created_at": {"$type": "date"}},
    )
    still_missing = await db.dev_users.count_documents(
        {"created_at": {"$exists": False}},
    )
    total = await db.dev_users.count_documents({})

    logger.info(
        "[P0 manual backfill] datetime_fixed=%d missing_filled=%d "
        "still_pending=%d total=%d",
        r1.modified_count, r2.modified_count,
        still_datetime + still_missing, total,
    )
    return {
        "ok":             True,
        "datetime_fixed": r1.modified_count,
        "missing_filled": r2.modified_count,
        "still_pending":  still_datetime + still_missing,
        "total_users":    total,
    }


@router.get("/dev-users/created-at-health")
async def admin_dev_users_created_at_health(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Cheap read-only probe — reports the shape distribution of
    `created_at` across the `dev_users` collection so the founder can
    verify the P0 fix landed cleanly on production.

    Healthy shape: `by_type.double + by_type.int/long == total_users`
    and both `datetime_typed` + `missing_field` are zero.
    """
    await _require_admin(authorization)
    db = require_db()
    pipeline = [
        {"$group": {
            "_id": {
                "$cond": [
                    {"$eq": [{"$type": "$created_at"}, "missing"]}, "missing",
                    {"$type": "$created_at"},
                ]
            },
            "n": {"$sum": 1},
        }},
    ]
    by_type: dict = {}
    async for row in db.dev_users.aggregate(pipeline):
        by_type[row["_id"] or "unknown"] = int(row["n"])
    total = sum(by_type.values())
    return {
        "ok":              True,
        "total_users":     total,
        "by_type":         by_type,
        "datetime_typed":  by_type.get("date", 0),
        "missing_field":   by_type.get("missing", 0),
        "healthy": (by_type.get("date", 0) == 0
                    and by_type.get("missing", 0) == 0),
    }


@router.get("/users/{user_id}/chat-sessions")
async def admin_list_chat_sessions(
    user_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    """2026-08-25 — read-only admin lookup so a founder can find the
    right `session_id` to inspect for a support/incident investigation
    (e.g. tracing a reported mismatched-answer bug), without needing
    direct DB access. Returns newest-first, capped at 50."""
    await _require_admin(authorization)
    db = require_db()
    rows = await db.chat_sessions.find(
        {"user_id": user_id},
        {"_id": 0, "session_id": 1, "title": 1, "created_at": 1,
         "updated_at": 1, "project_id": 1, "turns": 1},
    ).sort("updated_at", -1).limit(50).to_list(50)
    for r in rows:
        r["turn_count"] = len(r.pop("turns", None) or [])
    return {"ok": True, "sessions": rows}


@router.get("/chat-sessions/{session_id}")
async def admin_get_chat_session(
    session_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    """2026-08-25 — read-only admin lookup of a single session's full
    `turns` array. Founder-only (same guard as every other /admin/*
    route). Used for incident investigation, e.g. confirming/denying a
    reported "ORA answered an unrelated question" mismatch by
    inspecting the actual turn history and timestamps."""
    await _require_admin(authorization)
    db = require_db()
    doc = await db.chat_sessions.find_one(
        {"session_id": session_id}, {"_id": 0},
    )
    if not doc:
        raise HTTPException(404, "Session not found")
    return {"ok": True, "session": doc}

