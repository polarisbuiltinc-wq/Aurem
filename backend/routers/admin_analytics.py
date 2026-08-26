"""admin_analytics.py — Analytics, telemetry, observability, dashboard, council, loop metrics.

Extracted from routers/admin.py during Phase 2 architecture split (2026-02-11).
Contains 42 handler(s)/helper(s):

  GET  /admin/dashboard    /admin/audit    /admin/pulse    /admin/system-stats
  GET  /admin/token-pnl   /admin/agent-tokens   /admin/overview-metrics
  GET  /admin/mcp-usage   /admin/graph-status   /admin/agent-performance
  GET  /admin/loop-metrics /admin/loop-token-metrics /admin/loop-inspect/{loop_id}
  GET  /admin/speed-diagnostic /admin/scope-drift-audit
  GET  /admin/eval-quality /admin/mode-telemetry /admin/product-analytics
  GET  /admin/vanguard/stats /admin/vanguard/recent
  GET  /admin/warm-start-stats /admin/skills-usage /admin/skills/status
  POST /admin/skills/* (web-search, fetch-url, firecrawl-*, search-and-summarize)
  GET  /admin/council/* /admin/ora/*  POST /admin/qa/cleanup-e2e-sessions
  POST /admin/seo/run
  GET  /admin/digest /admin/learning-health

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
    prefix="/admin", tags=["Admin-analytics"],
    dependencies=[Depends(require_admin_dep)],
)

from routers._admin_common import _require_admin  # noqa: E402
# 2026-02-11 · Phase 2 split fix — helpers still live in the pre-split
# admin.py stub. Re-import here so handler bodies still resolve them.
# (Testing agent iter364 caught these as latent NameError bugs.)
from routers.admin import _bucket_label, _run_skill, _email_hint  # noqa: E402


@router.post("/qa/cleanup-e2e-sessions")
async def cleanup_e2e_sessions(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    from services.test_accounts import E2E_SESSION_PREFIX_RE
    res = await db.chat_sessions.delete_many(
        {"session_id": E2E_SESSION_PREFIX_RE})
    return {"ok": True, "deleted": res.deleted_count}


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


@router.get("/pulse")
async def business_pulse(authorization: Optional[str] = Header(None)):
    """Feb 2026 — cockpit Business Pulse endpoint. Returns BOTH raw
    and organic-only counts, plus explicit env tag so a preview
    reading is never mistaken for a production reading again.

    Organic filter uses services.synthetic_filter (single source of
    truth also consumed by G2 marketing-truth guard). Env tag comes
    from services.env_context — same helper backs the cockpit
    'PREVIEW DATA' badge.

    Feb 2026 · prod-hang fix — original implementation ran 8 sequential
    `count_documents(...)` calls; if any one query stalled (missing
    index on a large collection) the whole endpoint hung forever.
    Now runs them concurrently via asyncio.gather with a hard 10s
    outer timeout so a slow query surfaces as a fast error, not a
    permanently pending request."""
    await _require_admin(authorization)
    db = require_db()
    import asyncio
    from services.synthetic_filter import synthetic_mongo_filter
    from services.env_context import env_stamp

    org_filter = synthetic_mongo_filter()
    gh_filter  = {"github.access_token": {"$exists": True, "$nin": [None, ""]}}
    paid_tiers = ["basic", "pro", "maxx", "founder"]
    paid_raw_filter = {"tier": {"$in": paid_tiers}}

    from datetime import datetime, timezone, timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    paid_new_raw_filter = {**paid_raw_filter, "created_at": {"$gte": since}}

    try:
        (
            total_users_raw, total_users_organic,
            gh_raw, gh_organic,
            paid_users_raw, paid_users_organic,
            paid_new_30d_raw, paid_new_30d_organic,
        ) = await asyncio.wait_for(
            asyncio.gather(
                db.dev_users.count_documents({}),
                db.dev_users.count_documents(org_filter),
                db.dev_users.count_documents(gh_filter),
                db.dev_users.count_documents({**org_filter, **gh_filter}),
                db.dev_users.count_documents(paid_raw_filter),
                db.dev_users.count_documents({**org_filter, **paid_raw_filter}),
                db.dev_users.count_documents(paid_new_raw_filter),
                db.dev_users.count_documents({**org_filter, **paid_new_raw_filter}),
            ),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            504, "business pulse timed out after 10s — check Mongo indexes on dev_users"
        )

    def _pct(num, denom):
        return round(100.0 * num / denom, 1) if denom else 0.0

    return {
        # Environment identification — added Feb 2026 after
        # preview/prod denominator confusion audit.
        **env_stamp(),
        # Raw (unfiltered) — same as before.
        "raw": {
            "total_users":         total_users_raw,
            "github_connected":    gh_raw,
            "github_connect_pct":  _pct(gh_raw, total_users_raw),
            "paid_users":          paid_users_raw,
            "paid_new_30d":        paid_new_30d_raw,
        },
        # Organic — synthetic test/bot rows excluded. This is the
        # denominator the cockpit renders.
        "organic": {
            "total_users":         total_users_organic,
            "github_connected":    gh_organic,
            "github_connect_pct":  _pct(gh_organic, total_users_organic),
            "paid_users":          paid_users_organic,
            "paid_new_30d":        paid_new_30d_organic,
        },
        # Convenience mirror for the current cockpit code that reads
        # top-level fields; will migrate to explicit organic.* soon.
        "total_users":         total_users_organic,
        "github_connected":    gh_organic,
        "github_connect_pct":  _pct(gh_organic, total_users_organic),
        "paid_users":          paid_users_organic,
        "paid_new_30d":        paid_new_30d_organic,
    }


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
            "avg_tools_injected":  None,    # populated by future iter
            "top_signals":         [],
        },
        "intent_gateway": {
            "tier_distribution":     tier_dist,
            "avg_confidence":        avg_conf,
            "llm_fallback_rate_pct": llm_fallback_pct,
        },
        "syntax_gate": {
            # Derived counts will populate when we add a dedicated
            # `syntax_gate_log` collection (Iter 212m-155+).
            "total_checks":     0,
            "blocked_commits":  0,
            "block_rate_pct":   0.0,
            "by_language":      {"py": 0, "ts": 0, "js": 0},
        },
        "quality": {
            "avg_score_24h":         q_avg,
            "low_score_count":       q_low,
            "drift_alerts_unacked":  q_alerts_unacked,
            "top_flags":             [],
        },
    }


@router.get("/council/stats")
async def council_stats(authorization: Optional[str] = Header(None)):
    """Lightweight aggregate for AdminOverview — total council rows +
    last-30-days slice + Claude correction rate + per-mode breakdown.
    No PII."""
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    cutoff = now - (30 * 86400)

    total = await db.ora_council_logs.count_documents({})
    last_30d = await db.ora_council_logs.count_documents(
        {"timestamp": {"$gte": cutoff}}
    )
    # Per-mode breakdown (used by AdminOverview metric grid).
    mode_a = await db.ora_council_logs.count_documents({"mode": "A"})
    mode_b = await db.ora_council_logs.count_documents({"mode": "B"})
    mode_c = await db.ora_council_logs.count_documents({"mode": "C"})
    mode_d = await db.ora_council_logs.count_documents({"mode": "D"})
    mode_e = await db.ora_council_logs.count_documents({"mode": "E"})
    mode_f = await db.ora_council_logs.count_documents({"mode": "F"})
    lint_blocked = await db.ora_council_logs.count_documents(
        {"lint_blocked": True}
    )
    corrected = await db.ora_council_logs.count_documents(
        {"mode": "C", "claude_corrected": True}
    )
    correction_rate = (
        round((corrected / mode_c) * 100, 1) if mode_c else 0.0
    )
    return {
        "total":           total,
        "total_logs":      total,        # AdminOverview reads this key too
        "last_30d":        last_30d,
        "mode_a":          mode_a,
        "mode_b":          mode_b,
        "mode_c":          mode_c,
        "mode_d":          mode_d,
        "mode_e":          mode_e,
        "mode_f":          mode_f,
        "code_rows":       mode_c,
        "corrections":     corrected,
        "corrected":       corrected,
        "lint_blocked":    lint_blocked,
        "correction_rate": correction_rate,
        "ready_for_finetune": total >= 1000,
    }


@router.get("/council/health")
async def council_health(authorization: Optional[str] = Header(None)):
    """Live status of Council A's LongCat primary and its GLM-5.2
    fallback path.

    Rationale — Production ran silently on the GLM-5.2 fallback for
    an unknown time before it surfaced as an Ask Advisor bug
    (`<tool_call>read_repo_file)("README.md")` malformed emissions).
    This endpoint gives the admin dashboard a live badge so future
    degradations are visible within the 15 min re-probe window.

    Returns the in-memory snapshot plus the last 20 persisted probe
    rows for a small trailing history so the on-call can see whether
    the degradation is a hard-fail (repeated errors) or intermittent.
    """
    await _require_admin(authorization)
    from services.llm import (
        _LONGCAT_LAST_PROBE, LONGCAT_ENABLED, LONGCAT_LIVE, _LONGCAT_MODEL,
        _GLM_MODEL, council_a_primary_model,
    )
    db = require_db()
    history = await db.council_health_probes.find(
        {"council": "A"}, {"_id": 0},
    ).sort("checked_at", -1).limit(20).to_list(20)
    return {
        "council":         "A",
        "primary_intended": _LONGCAT_MODEL,
        "primary_actual":   council_a_primary_model(),
        "fallback":         _GLM_MODEL,
        "enabled":          LONGCAT_ENABLED,
        "live":             LONGCAT_LIVE,
        "degraded":         (LONGCAT_ENABLED and not LONGCAT_LIVE),
        "last_probe":       _LONGCAT_LAST_PROBE,
        "history":          history,
        "checked_at":       time.time(),
    }


@router.post("/council/reprobe")
async def council_reprobe(authorization: Optional[str] = Header(None)):
    """Force an immediate LongCat re-probe. Founder-only.
    Returns the fresh probe snapshot so the caller can render the new
    badge without a second round-trip. Rate-limit: at most 1 call
    every 3 s per pod (simple in-memory guard) to prevent probe spam.
    """
    await _require_admin(authorization)
    from services.llm import probe_longcat_availability, _LONGCAT_LAST_PROBE
    import time as _time
    global _COUNCIL_REPROBE_LAST_AT              # noqa: PLW0603
    now = _time.time()
    if now - _COUNCIL_REPROBE_LAST_AT < 3.0:
        return {
            "ok":       False,
            "throttled": True,
            "wait_s":   round(3.0 - (now - _COUNCIL_REPROBE_LAST_AT), 2),
            "snapshot": _LONGCAT_LAST_PROBE,
        }
    _COUNCIL_REPROBE_LAST_AT = now
    live = await probe_longcat_availability()
    return {
        "ok":        True,
        "live":      live,
        "snapshot":  _LONGCAT_LAST_PROBE,
        "reprobed_at": now,
    }


@router.get("/council-health")
async def council_health_alias(authorization: Optional[str] = Header(None)):
    """Alias for GET /admin/council/health (kebab-case spelling)."""
    return await council_health(authorization=authorization)


@router.get("/ora-learning/weekly-summary")
async def ora_learning_weekly_summary(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 153 — top low-confidence patterns from the last 7 days of
    ora_learning_logs. Powers the AdminOverview learning card."""
    await _require_admin(authorization)
    db = require_db()
    cutoff = time.time() - 7 * 86400
    cur = db.ora_learning_logs.aggregate([
        {"$match": {"ts": {"$gte": cutoff}}},
        {"$group": {"_id": "$reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 10},
    ])
    rows = await cur.to_list(50)
    total = sum(r.get("n", 0) for r in rows)
    return {
        "ok": True,
        "window_days": 7,
        "total": total,
        "patterns": [{"reason": r["_id"], "count": r["n"]} for r in rows],
    }


@router.get("/token-pnl")
async def token_pnl(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    day_ago = now - 86400
    month_ago = now - 86400 * 30

    # Iter 2026-08-27 — ROOT FIX: this previously aggregated
    # `cto_tasks.tokens_used`/`agent_used`, which is ALWAYS empty for
    # this purpose (cto_tasks only carries per-task status metadata,
    # never real per-call model/cost attribution — same root cause the
    # 2026-08-26 fix to /admin/agent-performance already diagnosed and
    # fixed for that endpoint). Real per-call LLM cost lives in
    # `customer_chat_cost` (cost_usd, model, ts) — the same ledger
    # `admin_bi.py::_fetch_inference_metrics` and the now-fixed
    # /admin/agent-performance already use. Switched to the same real
    # source so the cockpit's "AI cost (mo)" card stops showing $0
    # while Agent Performance shows real spend from the same 30d window.
    month_pipe = [
        {"$match": {"ts": {"$gte": month_ago}}},
        {"$group": {"_id": "$model", "cost": {"$sum": "$cost_usd"},
                    "n": {"$sum": 1}}},
    ]
    month_by_agent: dict[str, float] = {}
    month_calls_by_agent: dict[str, int] = {}
    async for d in db.customer_chat_cost.aggregate(month_pipe):
        agent = d.get("_id") or "unknown"
        month_by_agent[agent] = round(float(d.get("cost") or 0), 4)
        month_calls_by_agent[agent] = int(d.get("n") or 0)

    day_pipe = [
        {"$match": {"ts": {"$gte": day_ago}}},
        {"$group": {"_id": "$model", "cost": {"$sum": "$cost_usd"},
                    "n": {"$sum": 1}}},
    ]
    day_by_agent: dict[str, float] = {}
    day_calls_by_agent: dict[str, int] = {}
    async for d in db.customer_chat_cost.aggregate(day_pipe):
        agent = d.get("_id") or "unknown"
        day_by_agent[agent] = round(float(d.get("cost") or 0), 4)
        day_calls_by_agent[agent] = int(d.get("n") or 0)

    ai_cost_month = round(sum(month_by_agent.values()), 2)
    ai_cost_today = round(sum(day_by_agent.values()), 2)

    done_month = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": month_ago}, "status": "done"}
    )
    done_today = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": day_ago}, "status": "done"}
    )
    chat_month = await db.chat_sessions.count_documents(
        {"updated_at": {"$gte": month_ago}}
    )

    # Iter 388y — Real revenue from cto_payments.  Uses `payment_status`
    # ('paid'/'pending'/'expired') as the source of truth, NOT the
    # `status` field (which mirrors Stripe checkout-session state and
    # can be 'complete' even for cards that later declined).  Same
    # field as `admin_payments.list_payments` uses on line 75 — this
    # removes the drift that had two admin cards showing different
    # revenue numbers depending on which endpoint they read from.
    revenue_month = 0.0
    paid_txn_month = 0
    try:
        rev_pipe = [
            {"$match": {"created_at": {"$gte": month_ago},
                        "payment_status": "paid"}},
            {"$group": {"_id": None,
                        "sum": {"$sum": "$amount"},
                        "n":   {"$sum": 1}}},
        ]
        async for row in db.cto_payments.aggregate(rev_pipe):
            revenue_month  = round(float(row.get("sum") or 0), 2)
            paid_txn_month = int(row.get("n") or 0)
            break
    except Exception as e:
        logger.warning("token-pnl: revenue_month aggregate failed: %r", e)

    # Stripe fees (US standard published rate): 2.9% + $0.30 per
    # successful charge.  We compute an ESTIMATE — actual fee lives on
    # the Stripe Balance API and can vary by country / card type; this
    # is close enough for the cockpit card and never claims to be
    # settlement-accurate (`_note` below spells that out).
    stripe_fees_month = round(
        (revenue_month * 0.029) + (paid_txn_month * 0.30), 2,
    )
    net_revenue_month = round(revenue_month - stripe_fees_month, 2)
    net_profit_month  = round(net_revenue_month - ai_cost_month, 2)
    margin_pct = (
        round((net_profit_month / revenue_month) * 100, 1)
        if revenue_month > 0 else 0.0
    )

    # Iter 388y — surface real Stripe wiring state (was hardcoded False).
    stripe_key = (os.environ.get("STRIPE_API_KEY")
                  or os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    stripe_configured = bool(stripe_key)

    return {
        "revenue_month":       revenue_month,
        "stripe_fees":         stripe_fees_month,
        "net_revenue":         net_revenue_month,
        "ai_cost_month":       ai_cost_month,
        "ai_cost_today":       ai_cost_today,
        "net_profit":          net_profit_month,
        "margin_pct":          margin_pct,
        "paid_txn_month":      paid_txn_month,
        "tasks_done_month":    done_month,
        "tasks_done_today":    done_today,
        "chat_sessions_month": chat_month,
        "month_by_agent":      month_by_agent,
        "day_by_agent":        day_by_agent,
        "month_calls_by_agent": month_calls_by_agent,
        "day_calls_by_agent":   day_calls_by_agent,
        "stripe_configured":   stripe_configured,
        "_note": (
            "Revenue = sum(amount) from cto_payments where "
            "payment_status='paid' in the last 30d.  Stripe fees are "
            "an ESTIMATE at 2.9% + $0.30/txn (US standard) — not a "
            "settlement-accurate figure.  AI cost = sum(cost_usd) from "
            "customer_chat_cost (real per-call LLM ledger, same source "
            "/admin/agent-performance uses) in the last 30d/24h."
        ),
    }


@router.get("/agent-tokens")
async def agent_tokens(
    range: str = "7d",
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    await _require_admin(authorization)
    db = require_db()
    range_map = {
        "24h":   ("hourly",   86400,            3600),     # 24 hourly buckets
        "7d":    ("daily",    7 * 86400,        86400),    # 7 daily
        "30d":   ("daily",    30 * 86400,       86400),    # 30 daily
        "90d":   ("weekly",   90 * 86400,       7 * 86400),
        "365d":  ("monthly",  365 * 86400,      30 * 86400),
    }
    if range not in range_map:
        range = "7d"
    bucket_label, window_secs, bucket_secs = range_map[range]
    now = time.time()
    since = now - window_secs

    # Pull every done task in window with agent + tokens + created_at.
    cur = db.cto_tasks.find(
        {"created_at": {"$gte": since}, "status": "done"},
        {"agent_used": 1, "tokens_used": 1, "created_at": 1,
         "claude_corrected": 1, "_id": 0},
    )
    # Cost rates per 1k tokens (real OpenRouter/Anthropic prices Feb 2026)
    cost_per_1k = {"deepseek": 0.30, "maxx": 0.65, "claude": 0.65, "groq": 0.03}

    # Bucket → agent → tokens
    buckets: dict[float, dict[str, int]] = {}
    totals = {"deepseek": 0, "maxx": 0, "claude": 0, "groq": 0}
    task_counts = {"deepseek": 0, "maxx": 0, "claude": 0, "groq": 0}
    claude_corrections = 0

    async for d in cur:
        agent = (d.get("agent_used") or "deepseek").lower()
        if agent not in totals:
            agent = "deepseek"
        tk = int(d.get("tokens_used") or 0)
        bucket_start = (int(d.get("created_at", now) // bucket_secs)) * bucket_secs
        bkt = buckets.setdefault(bucket_start, {a: 0 for a in totals})
        bkt[agent] += tk
        totals[agent] += tk
        task_counts[agent] += 1
        if d.get("claude_corrected"):
            claude_corrections += 1

    # Format chronological series
    series = []
    for bs in sorted(buckets.keys()):
        row = {"ts": int(bs), "label": _bucket_label(bs, bucket_label)}
        for agent, val in buckets[bs].items():
            row[agent] = val
        series.append(row)

    # Cost summary
    costs = {a: round((t / 1000) * cost_per_1k.get(a, 0.30), 4)
             for a, t in totals.items()}
    total_cost = round(sum(costs.values()), 4)

    # Per-task averages — the key question Teji asks
    avg_per_task = {}
    for a in totals:
        n = task_counts[a]
        avg_per_task[a] = {
            "tokens_avg": round(totals[a] / n) if n else 0,
            "cost_avg_usd": round((totals[a] / n / 1000) * cost_per_1k.get(a, 0.30), 4)
                             if n else 0,
        }

    # Claude-vs-DeepSeek delta — directly answers the "how much extra"
    extra_for_claude = None
    if task_counts["deepseek"] > 0 and (task_counts["maxx"] + task_counts["claude"]) > 0:
        ds_avg_cost = avg_per_task["deepseek"]["cost_avg_usd"]
        maxx_avg_tokens = (
            totals["maxx"] + totals["claude"]
        ) / max(task_counts["maxx"] + task_counts["claude"], 1)
        claude_rate = cost_per_1k["maxx"]
        claude_avg_cost = round((maxx_avg_tokens / 1000) * claude_rate, 4)
        extra_for_claude = {
            "deepseek_avg_cost_per_task": ds_avg_cost,
            "claude_maxx_avg_cost_per_task": claude_avg_cost,
            "delta_usd_per_task": round(claude_avg_cost - ds_avg_cost, 4),
            "delta_multiplier": round(claude_avg_cost / ds_avg_cost, 2)
                                if ds_avg_cost else None,
        }

    return {
        "range": range,
        "bucket": bucket_label,
        "buckets_count": len(series),
        "series": series,
        "totals_tokens": totals,
        "task_counts": task_counts,
        "claude_corrections": claude_corrections,
        "cost_per_1k_usd": cost_per_1k,
        "costs_usd": costs,
        "total_cost_usd": total_cost,
        "avg_per_task": avg_per_task,
        "claude_vs_deepseek": extra_for_claude,
    }


@router.get("/digest")
async def get_digest(authorization: Optional[str] = Header(None)):
    """Returns the same 1-pager that the daily cron sends. Preview-friendly."""
    await _require_admin(authorization)
    from services.daily_digest import build_digest
    return await build_digest()


@router.get("/learning-health")
async def learning_health(authorization: Optional[str] = Header(None)):
    """Iter 331 · ORA learning-health (PRD #3-e) — data for the
    /admin/architecture tile. GREEN = project_brains touched <24h,
    RED = stale, EMPTY = no brains yet. Every collection read is
    fail-open so one bad collection never blanks the tile."""
    await _require_admin(authorization)
    db = require_db()
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    def _iso(v):
        try:
            return v.isoformat() if v else None
        except Exception:
            return None

    brain: dict = {"count": 0, "updated_at": None,
                   "age_hours": None, "project_id": None}
    try:
        brain["count"] = await db.project_brains.count_documents({})
        doc = await db.project_brains.find_one(
            {}, {"_id": 0, "project_id": 1, "updated_at": 1},
            sort=[("updated_at", -1)],
        )
        if doc and doc.get("updated_at"):
            u = doc["updated_at"]
            if u.tzinfo is None:
                u = u.replace(tzinfo=timezone.utc)
            brain.update({
                "project_id": doc.get("project_id"),
                "updated_at": _iso(u),
                "age_hours": round((now - u).total_seconds() / 3600, 1),
            })
    except Exception as e:
        logger.warning("learning-health brains read failed: %r", e)

    patterns: dict = {"count": 0, "last_seen": None}
    try:
        patterns["count"] = await db.ora_patterns.count_documents({})
        pdoc = await db.ora_patterns.find_one(
            {}, {"_id": 0, "last_seen": 1}, sort=[("last_seen", -1)])
        if pdoc and pdoc.get("last_seen"):
            patterns["last_seen"] = _iso(datetime.fromtimestamp(
                float(pdoc["last_seen"]), tz=timezone.utc))
    except Exception as e:
        logger.warning("learning-health patterns read failed: %r", e)

    council: dict = {"count": 0, "last_24h": 0}
    try:
        council["count"] = await db.ora_council_logs.count_documents({})
        council["last_24h"] = await db.ora_council_logs.count_documents(
            {"timestamp": {"$gte": day_ago}})
    except Exception as e:
        logger.warning("learning-health council read failed: %r", e)

    canary: dict = {
        "enabled": os.environ.get(
            "ORA_CANARY_ENABLED", "0").lower() in ("1", "true", "yes"),
        "last_run": None,
    }
    try:
        cdoc = await db.ora_canary_runs.find_one(
            {}, {"_id": 0}, sort=[("$natural", -1)])
        if cdoc:
            for k, v in list(cdoc.items()):
                if isinstance(v, datetime):
                    cdoc[k] = _iso(v)
            canary["last_run"] = cdoc
    except Exception as e:
        logger.warning("learning-health canary read failed: %r", e)

    if brain["age_hours"] is not None and brain["age_hours"] < 24:
        status = "green"
    elif brain["count"] == 0:
        status = "empty"
    else:
        status = "red"

    return {
        "status": status,
        "brain": brain,
        "patterns": patterns,
        "council_logs": council,
        "canary": canary,
        "eval_cron_enabled": os.environ.get(
            "ENABLE_EVAL_CRON", "").lower() in ("1", "true", "yes"),
        "learning_disabled_flag":
            os.environ.get("ORA_LEARNING_DISABLED") == "1",
        "generated_at": _iso(now),
    }


@router.get("/ora/stats")
async def ora_council_stats(authorization: Optional[str] = Header(None)):
    """Quick summary: total logs, by-mode counts, correction rate,
    pending-export queue, fine-tune readiness."""
    await _require_admin(authorization)
    from services.ora_council_logger import get_council_stats
    return await get_council_stats(require_db())


@router.get("/ora-stats")
async def ora_council_stats_v2(authorization: Optional[str] = Header(None)):
    """Alias for AuremAdminPanel (uses /admin/ora-stats path)."""
    await _require_admin(authorization)
    from services.ora_council_logger import get_council_stats
    return await get_council_stats(require_db())


@router.post("/ora/export")
async def ora_council_export(authorization: Optional[str] = Header(None)):
    """Manually trigger yesterday's JSONL export. Daily cron also runs it."""
    await _require_admin(authorization)
    from services.ora_council_logger import export_daily_jsonl
    return await export_daily_jsonl(require_db())


class _SkillBody(BaseModel):
    query: Optional[str] = None
    url: Optional[str] = None
    urls: Optional[list[str]] = None
    max_results: Optional[int] = None
    deep: Optional[bool] = None
    topic: Optional[str] = None
    formats: Optional[list[str]] = None
    limit: Optional[int] = None


@router.post("/skills/web-search")
async def skill_web_search(body: _SkillBody,
                           authorization: Optional[str] = Header(None)):
    return await _run_skill("web_search", body, authorization)


@router.post("/skills/fetch-url")
async def skill_fetch_url(body: _SkillBody,
                          authorization: Optional[str] = Header(None)):
    return await _run_skill("fetch_url", body, authorization)


@router.post("/skills/search-and-summarize")
async def skill_web_search_and_summarize(
    body: _SkillBody, authorization: Optional[str] = Header(None),
):
    return await _run_skill("web_search_and_summarize", body, authorization)


@router.post("/skills/firecrawl-scrape")
async def skill_firecrawl_scrape(body: _SkillBody,
                                 authorization: Optional[str] = Header(None)):
    return await _run_skill("firecrawl_scrape", body, authorization)


@router.post("/skills/firecrawl-crawl")
async def skill_firecrawl_crawl(body: _SkillBody,
                                authorization: Optional[str] = Header(None)):
    return await _run_skill("firecrawl_crawl_site", body, authorization)


@router.get("/skills/status")
async def skill_status(authorization: Optional[str] = Header(None)):
    """Reveal which web skills are wired (key present). No secrets returned."""
    await _require_admin(authorization)
    import os
    return {
        "ok": True,
        "skills": {
            "web_search":               bool(os.environ.get("TAVILY_API_KEY")),
            "fetch_url":                bool(os.environ.get("TAVILY_API_KEY")),
            "web_search_and_summarize": bool(os.environ.get("TAVILY_API_KEY")),
            "firecrawl_scrape":         bool(os.environ.get("FIRECRAWL_API_KEY")),
            "firecrawl_crawl_site":     bool(os.environ.get("FIRECRAWL_API_KEY")),
        },
    }


@router.get("/eval-quality")
async def eval_quality(authorization: Optional[str] = Header(None)):
    """Last 30 days of eval runs (from `ora_eval_runs`). Returns:
       latest      — most recent run summary (None if no runs yet)
       trend       — chronological score list [{ts, score, hard_fails}]
       totals      — { runs, hard_fail_runs, avg_score, last30d_runs }
    Read-only, admin only, no external calls."""
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        return {"latest": None, "trend": [], "totals": {"runs": 0}}
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    # 2026-08-27 — ROOT FIX: a periodic "quick" liveness ping of the eval
    # harness itself (0 test cases, exists purely to prove the pipeline
    # can still run) was being picked up as "latest" whenever it landed
    # more recently than the last REAL eval — which made the tile always
    # show "—" (score = null, since 100 * passed/total with total=0 is
    # undefined) even with 97 real historical runs sitting right there.
    # Excluding quick pings from this endpoint entirely — they carry no
    # score signal, they'd only dilute totals.avg_score too.
    docs = await db.ora_eval_runs.find(
        {"ts": {"$gte": cutoff}, "quick": {"$ne": True}}, {"_id": 0},
    ).sort("ts", 1).to_list(length=200)
    trend = [{
        "ts":         d.get("ts"),
        "score":      round(100 * (d.get("passed", 0) / max(d.get("total", 1), 1))),
        "hard_fails": d.get("hard_fails", 0),
        "ok":         bool(d.get("ok")),
    } for d in docs]
    latest = docs[-1] if docs else None
    avg_score = round(sum(t["score"] for t in trend) / len(trend)) if trend else None
    return {
        "latest": latest,
        "trend":  trend,
        "totals": {
            "runs":           len(docs),
            "hard_fail_runs": sum(1 for d in docs if d.get("hard_fails", 0) > 0),
            "avg_score":      avg_score,
            "last30d_runs":   len(docs),
        },
    }


@router.get("/mode-telemetry")
async def mode_telemetry(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Aggregates the last 100 mode classifications + the most recent 10
    raw entries. Lets the founder see which modes ORA picks most often,
    how often it asks for confirmation, and how confident it is on avg.
    """
    await _require_admin(authorization)
    db = require_db()
    docs = await db["mode_classifications"].find(
        {}, {"_id": 0}
    ).sort("ts", -1).limit(100).to_list(100)

    from collections import Counter
    mode_counts = Counter(d.get("mode", "?") for d in docs)
    needs_confirm_count = sum(1 for d in docs if d.get("needs_confirm"))
    f12_forced_count = sum(1 for d in docs if d.get("f12_forced"))
    avg_confidence = (
        sum(d.get("confidence", 0.0) for d in docs) / len(docs)
        if docs else 0.0
    )
    total = len(docs)
    return {
        "total":              total,
        "mode_counts":        dict(mode_counts),
        "needs_confirm_pct":  round(needs_confirm_count / max(total, 1) * 100, 1),
        "f12_forced_pct":     round(f12_forced_count    / max(total, 1) * 100, 1),
        "avg_confidence":     round(avg_confidence, 2),
        "recent":             docs[:10],
    }


@router.get("/product-analytics")
async def product_analytics(
    days: int = 30,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Real product analytics — DAU, MAU, mode usage, feature adoption,
    task success rate, token burn, top users.

    Iter 139 — replaces the previous "Analytics" page that only showed
    Mode-Council debate logs. This endpoint hits the same collections
    the rest of the platform writes to, so every number is sourced
    from real user activity (no estimates, no stubs).
    """
    await _require_admin(authorization)
    db = require_db()
    import time
    from datetime import datetime, timezone

    # Iter 139 — clamp window so a malicious client can't probe with
    # days=10**12 and DoS the aggregation pipeline.
    days = max(1, min(int(days or 30), 365))
    now = time.time()
    window_start = now - (days * 86400)
    day_ago = now - 86400
    week_ago = now - (7 * 86400)

    async def _first_or_zero(coll, pipe: list, field: str) -> int:
        async for r in coll.aggregate(pipe):
            return int(r.get(field, 0) or 0)
        return 0

    # DAU — unique users who sent a chat message today
    dau = await _first_or_zero(
        db.chat_sessions,
        [
            {"$match": {"updated_at": {"$gte": day_ago}}},
            {"$group": {"_id": "$user_id"}},
            {"$count": "dau"},
        ],
        "dau",
    )

    # WAU — unique users active this week
    wau = await _first_or_zero(
        db.chat_sessions,
        [
            {"$match": {"updated_at": {"$gte": week_ago}}},
            {"$group": {"_id": "$user_id"}},
            {"$count": "wau"},
        ],
        "wau",
    )

    # MAU — unique users active this month
    mau = await _first_or_zero(
        db.chat_sessions,
        [
            {"$match": {"updated_at": {"$gte": window_start}}},
            {"$group": {"_id": "$user_id"}},
            {"$count": "mau"},
        ],
        "mau",
    )

    # Total users
    total_users = await db.dev_users.estimated_document_count()

    # New users this week
    new_users_week = await db.dev_users.count_documents(
        {"created_at": {"$gte": week_ago}}
    )

    # Task stats
    tasks_total = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": window_start}}
    )
    tasks_done = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": window_start}, "status": "done"}
    )
    tasks_failed = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": window_start}, "status": "failed"}
    )
    # Iter 388n — Bug 6+7 fix.  Loop runs live in `loop_sessions` under
    # a `state` field (COMPLETED / FAILED / …), NOT `cto_tasks.status`.
    # Without this backfill Analytics showed "0/1 tasks · 0% success"
    # for a founder who'd just shipped a real Plan→Ship loop 30 min
    # earlier.  `loop_sessions.created_at` is stored as a datetime
    # (via `_now()` in loop_engine.py), so we compare against a
    # datetime bound here — not the `time.time()` float used for
    # cto_tasks above.
    from datetime import datetime as _dt, timezone as _tz
    window_start_dt = _dt.fromtimestamp(window_start, tz=_tz.utc)
    loop_total = await db.loop_sessions.count_documents(
        {"created_at": {"$gte": window_start_dt}}
    )
    loop_done = await db.loop_sessions.count_documents(
        {"created_at": {"$gte": window_start_dt}, "state": "completed"}
    )
    loop_failed = await db.loop_sessions.count_documents(
        {"created_at": {"$gte": window_start_dt},
         "state": {"$in": ["failed", "aborted", "expired"]}},
    )
    tasks_total  += loop_total
    tasks_done   += loop_done
    tasks_failed += loop_failed
    success_rate = (
        round((tasks_done / tasks_total * 100), 1) if tasks_total else 0
    )

    # Mode distribution (A/B/C/D/E/F) from ora_council_logs
    mode_dist: dict = {}
    async for r in db.ora_council_logs.aggregate(
        [
            {"$match": {"ts": {"$gte": window_start}}},
            {"$group": {"_id": "$mode", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
    ):
        mid = r.get("_id")
        if mid:
            mode_dist[mid] = int(r.get("count", 0))

    # Daily active users trend (last 14 days)
    dau_trend = []
    for i in range(13, -1, -1):
        day_start = now - ((i + 1) * 86400)
        day_end = now - (i * 86400)
        day_count = await _first_or_zero(
            db.chat_sessions,
            [
                {"$match": {"updated_at": {"$gte": day_start, "$lt": day_end}}},
                {"$group": {"_id": "$user_id"}},
                {"$count": "c"},
            ],
            "c",
        )
        label = datetime.fromtimestamp(day_end, tz=timezone.utc).strftime("%b %d")
        dau_trend.append({"date": label, "users": day_count})

    # Top features used (by chat mode)
    feature_labels = {
        "C": "Code Ship", "D": "Debug", "B": "Advice",
        "A": "Chat", "E": "Audit", "F": "Engage",
    }
    top_features = [
        {"mode": k, "label": feature_labels.get(k, k), "count": v}
        for k, v in sorted(mode_dist.items(), key=lambda x: -x[1])
    ]

    # Token burn (last N days)
    tokens_burned = await _first_or_zero(
        db.cto_tasks,
        [
            {"$match": {"created_at": {"$gte": window_start}, "status": "done"}},
            {"$group": {"_id": None, "total": {"$sum": "$tokens_used"}}},
        ],
        "total",
    )

    # Tier breakdown
    tier_breakdown: dict = {}
    async for r in db.dev_users.aggregate(
        [
            {"$group": {"_id": "$tier", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
    ):
        tier_breakdown[r.get("_id") or "unknown"] = int(r.get("count", 0))

    # Maxx mode usage
    maxx_tasks = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": window_start}, "maxx_mode": True}
    )

    return {
        "ok": True,
        "period_days": days,
        "users": {
            "total": total_users,
            "dau": dau,
            "wau": wau,
            "mau": mau,
            "new_this_week": new_users_week,
            "by_tier": tier_breakdown,
        },
        "tasks": {
            "total": tasks_total,
            "done": tasks_done,
            "failed": tasks_failed,
            "success_rate_pct": success_rate,
            "maxx_mode": maxx_tasks,
            "tokens_burned": tokens_burned,
        },
        "modes": {
            "distribution": mode_dist,
            "top_features": top_features,
        },
        "trend": {
            "dau_14d": dau_trend,
        },
    }


@router.get("/vanguard/stats")
async def vanguard_stats(
    days: int = 7,
    authorization: Optional[str] = Header(None),
):
    """Stats for the admin Vanguard dashboard:
      total blocked this window, top rule, by-rule / by-project /
      by-severity breakdowns, day-bucketed sparkline."""
    await _require_admin(authorization)
    db = get_db()
    from services.vanguard_audit import weekly_stats
    return await weekly_stats(db, since_days=max(1, min(days, 90)))


@router.get("/vanguard/recent")
async def vanguard_recent(
    limit: int = 25,
    authorization: Optional[str] = Header(None),
):
    """Most recent N blocked-commit rows for the table."""
    await _require_admin(authorization)


@router.get("/skills-usage")
async def skills_usage(
    days: int = 14,
    authorization: Optional[str] = Header(None),
):
    """Aggregate ora_skill_usage over the last N days.

    Returns per-skill: call count, success rate, p50/p95 elapsed_ms.
    Use to identify dead-weight skills (<2% of calls) for pruning.
    """
    await _require_admin(authorization)
    db = require_db()

    days = max(1, min(int(days or 14), 90))
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    pipeline = [
        {"$match": {"ts": {"$gte": cutoff}}},
        {"$group": {
            "_id":       "$tool",
            "count":     {"$sum": 1},
            "ok_count":  {"$sum": {"$cond": ["$ok", 1, 0]}},
            "elapsed":   {"$push": "$elapsed_ms"},
        }},
        {"$sort": {"count": -1}},
    ]
    rows = []
    total = 0
    async for r in db.ora_skill_usage.aggregate(pipeline):
        # p50/p95 in Python — collection is small (<100k entries even at scale)
        elapsed = sorted(e for e in (r.get("elapsed") or []) if isinstance(e, (int, float)))
        def _pct(arr, p):
            if not arr:
                return None
            i = max(0, min(len(arr) - 1, int(len(arr) * p) - 1))
            return arr[i]
        rows.append({
            "tool":     r["_id"],
            "count":    r["count"],
            "ok_rate":  round(r["ok_count"] / r["count"], 3) if r["count"] else 0,
            "p50_ms":   _pct(elapsed, 0.50),
            "p95_ms":   _pct(elapsed, 0.95),
        })
        total += r["count"]

    # Annotate share so the founder can see at-a-glance which skills < 2%
    for row in rows:
        row["share"] = round(row["count"] / total, 3) if total else 0
        row["dead_weight"] = row["share"] < 0.02   # the prune threshold

    return {
        "window_days": days,
        "total_calls": total,
        "skills":      rows,
        "hint":        "skills with share<0.02 are prune candidates (industry ceiling target: 18 skills)",
    }


@router.get("/overview-metrics")
async def admin_overview_metrics(
    authorization: Optional[str] = Header(None),
):
    """Extended metrics for the Admin Overview tab.

    Returns one flat object with every metric the new cards need:
      - active_users_today, tasks_today, tasks_done_today
      - avg_task_seconds (over last 100 done tasks)
      - mcp_keys_total / mcp_keys_active_30d
      - warm_starts_24h with success_rate_pct
      - postscan_findings_7d (critical + warning split)
      - most_active_project (name + task_count last 7 d)
      - mode_distribution_30d (swift/pro/maxx counts)
      - revenue_30d (sum of completed cto_payments)
    """
    await _require_admin(authorization)
    db = require_db()

    now = time.time()
    day_ago = now - 86_400
    week_ago = now - 7 * 86_400
    month_ago = now - 30 * 86_400

    # Active users today — distinct user_id touching cto_tasks in 24 h.
    active_users_today = 0
    try:
        active_users_today = len(
            await db.cto_tasks.distinct(
                "user_id", {"created_at": {"$gte": day_ago}}
            )
        )
    except Exception as e:
        logger.warning("overview-metrics: active_users_today: %r", e)

    tasks_today = 0
    tasks_done_today = 0
    try:
        tasks_today = await db.cto_tasks.count_documents(
            {"created_at": {"$gte": day_ago}}
        )
        tasks_done_today = await db.cto_tasks.count_documents(
            {"created_at": {"$gte": day_ago}, "status": "done"}
        )
    except Exception as e:
        logger.warning("overview-metrics: tasks_today: %r", e)

    # Average task time over the last 100 completed tasks. Computed as
    # finished_at - created_at when both are present; sub-second values
    # ignored so a misset timestamp doesn't drag the mean down.
    avg_task_seconds = 0
    try:
        pipeline = [
            {"$match": {"status": "done",
                        "created_at": {"$gte": month_ago},
                        "finished_at": {"$exists": True}}},
            {"$sort": {"finished_at": -1}},
            {"$limit": 100},
            {"$project": {
                "_id": 0,
                "secs": {"$subtract": ["$finished_at", "$created_at"]},
            }},
            {"$match": {"secs": {"$gt": 1}}},
            {"$group": {"_id": None, "avg": {"$avg": "$secs"}}},
        ]
        async for row in db.cto_tasks.aggregate(pipeline):
            avg_task_seconds = round(float(row.get("avg") or 0), 2)
            break
    except Exception as e:
        logger.warning("overview-metrics: avg_task_seconds: %r", e)

    # MCP usage — keys minted via the /mcp/keys flow. The api_keys
    # collection holds rows shaped {key, user_id, client_id, scope,
    # last_used_at, …}.
    mcp_keys_total = 0
    mcp_keys_active_30d = 0
    try:
        mcp_keys_total = await db.api_keys.count_documents({})
        mcp_keys_active_30d = await db.api_keys.count_documents(
            {"last_used_at": {"$gte": month_ago}}
        )
    except Exception as e:
        logger.warning("overview-metrics: mcp_keys: %r", e)

    # Warm-start success rate over 24 h.
    warm_total_24h = 0
    warm_done_24h = 0
    warm_success_rate_pct = 0
    try:
        warm_total_24h = await db.warm_start_jobs.count_documents(
            {"created_at": {"$gte": day_ago}}
        )
        warm_done_24h = await db.warm_start_jobs.count_documents(
            {"created_at": {"$gte": day_ago}, "status": "done"}
        )
        if warm_total_24h:
            warm_success_rate_pct = round(
                100 * warm_done_24h / warm_total_24h, 1
            )
    except Exception as e:
        logger.warning("overview-metrics: warm_start: %r", e)

    # Post-scan issues last 7 d — vanguard findings recorded after
    # each task. Counts critical (block) vs warning (notify) buckets.
    postscan_critical_7d = 0
    postscan_warning_7d = 0
    try:
        postscan_critical_7d = await db.post_task_scans.count_documents(
            {"created_at": {"$gte": week_ago}, "severity": "critical"}
        )
        postscan_warning_7d = await db.post_task_scans.count_documents(
            {"created_at": {"$gte": week_ago},
             "severity": {"$in": ["warning", "warn"]}}
        )
    except Exception as e:
        logger.warning("overview-metrics: postscan: %r", e)

    # Most active project over the last 7 d.
    most_active_project = {"name": None, "task_count": 0}
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": week_ago},
                        "project_id": {"$ne": None}}},
            {"$group": {"_id": "$project_id", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": 1},
        ]
        async for row in db.cto_tasks.aggregate(pipeline):
            pid = row.get("_id")
            n = int(row.get("n") or 0)
            proj = await db.cto_projects.find_one(
                {"project_id": pid}, {"_id": 0, "name": 1}
            )
            most_active_project = {
                "name": (proj or {}).get("name") or pid,
                "task_count": n,
            }
            break
    except Exception as e:
        logger.warning("overview-metrics: most_active: %r", e)

    # Mode distribution over 30 d (Swift / Pro / Maxx).
    mode_distribution_30d = {"swift": 0, "pro": 0, "maxx": 0}
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": month_ago},
                        "mode": {"$in": ["swift", "pro", "maxx"]}}},
            {"$group": {"_id": "$mode", "n": {"$sum": 1}}},
        ]
        async for row in db.cto_tasks.aggregate(pipeline):
            m = row.get("_id")
            if m in mode_distribution_30d:
                mode_distribution_30d[m] = int(row.get("n") or 0)
    except Exception as e:
        logger.warning("overview-metrics: mode_dist: %r", e)

    # Revenue over 30 d — sum of `amount` from paid payments.
    # Iter 388y · Admin Payments Accuracy fix (#35 slice) — filter on
    # `payment_status='paid'` (matches admin_payments.list_payments
    # semantics, single source of truth) instead of the old
    # `status IN [paid, complete, ...]` which was mixing Stripe
    # checkout-session state with payment-outcome state and could
    # count a completed-but-declined session as revenue.
    revenue_30d = 0.0
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": month_ago},
                        "payment_status": "paid"}},
            {"$group": {"_id": None, "sum": {"$sum": "$amount"}}},
        ]
        async for row in db.cto_payments.aggregate(pipeline):
            revenue_30d = round(float(row.get("sum") or 0), 2)
            break
    except Exception as e:
        logger.warning("overview-metrics: revenue_30d: %r", e)

    return {
        "active_users_today":     active_users_today,
        "tasks_today":            tasks_today,
        "tasks_done_today":       tasks_done_today,
        "avg_task_seconds":       avg_task_seconds,
        "mcp_keys_total":         mcp_keys_total,
        "mcp_keys_active_30d":    mcp_keys_active_30d,
        "warm_total_24h":         warm_total_24h,
        "warm_done_24h":          warm_done_24h,
        "warm_success_rate_pct":  warm_success_rate_pct,
        "postscan_critical_7d":   postscan_critical_7d,
        "postscan_warning_7d":    postscan_warning_7d,
        "most_active_project":    most_active_project,
        "mode_distribution_30d":  mode_distribution_30d,
        "revenue_30d":            revenue_30d,
        "generated_at":           now,
    }


@router.get("/mcp-usage")
async def admin_mcp_usage(
    authorization: Optional[str] = Header(None),
    limit: int = 50,
):
    """Recent MCP API keys with usage timestamps for the MCP Usage tab."""
    await _require_admin(authorization)
    db = require_db()
    rows: list[dict] = []
    try:
        cursor = db.api_keys.find(
            {},
            {"_id": 0, "user_id": 1, "client_id": 1, "scope": 1,
             "created_at": 1, "last_used_at": 1, "expires_at": 1, "key": 1},
            sort=[("last_used_at", -1), ("created_at", -1)],
            limit=max(1, min(int(limit or 50), 200)),
        )
        async for r in cursor:
            k = r.get("key") or ""
            r["key_tail"] = k[-6:] if k else ""
            r.pop("key", None)
            rows.append(r)
    except Exception as e:
        logger.warning("admin/mcp-usage: %r", e)
    return {"rows": rows, "count": len(rows)}


@router.get("/warm-start-stats")
async def admin_warm_start_stats(
    authorization: Optional[str] = Header(None),
):
    """Warm-start latency & success metrics for the dedicated tab.

    `avg_seconds` covers the last 100 done jobs over 30 d; the breakdown
    shows the per-status counts over 7 d so we can spot a stuck queue."""
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    week_ago = now - 7 * 86_400
    month_ago = now - 30 * 86_400

    breakdown: dict[str, int] = {}
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": week_ago}}},
            {"$group": {"_id": "$status", "n": {"$sum": 1}}},
        ]
        async for row in db.warm_start_jobs.aggregate(pipeline):
            breakdown[str(row.get("_id") or "unknown")] = int(row.get("n") or 0)
    except Exception as e:
        logger.warning("admin/warm-start-stats breakdown: %r", e)

    avg_seconds = 0.0
    try:
        pipeline = [
            {"$match": {"status": "done",
                        "created_at": {"$gte": month_ago},
                        "finished_at": {"$exists": True}}},
            {"$sort": {"finished_at": -1}},
            {"$limit": 100},
            {"$project": {"_id": 0,
                          "secs": {"$subtract": ["$finished_at", "$created_at"]}}},
            {"$match": {"secs": {"$gt": 0.1}}},
            {"$group": {"_id": None, "avg": {"$avg": "$secs"}}},
        ]
        async for row in db.warm_start_jobs.aggregate(pipeline):
            avg_seconds = round(float(row.get("avg") or 0), 2)
            break
    except Exception as e:
        logger.warning("admin/warm-start-stats avg: %r", e)

    return {
        "avg_seconds":  avg_seconds,
        "breakdown_7d": breakdown,
        "window_days":  7,
    }


@router.get("/graph-status")
async def admin_graph_status(
    authorization: Optional[str] = Header(None),
    limit: int = 60,
):
    """Which projects have a Knowledge Graph built (and how recently)."""
    await _require_admin(authorization)
    db = require_db()
    rows: list[dict] = []
    try:
        cursor = db.cto_projects.find(
            {},
            {"_id": 0, "project_id": 1, "name": 1, "user_id": 1,
             "graph_built_at": 1, "graph_node_count": 1,
             "github_owner": 1, "github_repo": 1},
            sort=[("graph_built_at", -1), ("created_at", -1)],
            limit=max(1, min(int(limit or 60), 200)),
        )
        async for r in cursor:
            r["has_graph"] = bool(r.get("graph_built_at"))
            rows.append(r)
    except Exception as e:
        logger.warning("admin/graph-status: %r", e)
    return {"rows": rows, "count": len(rows)}


@router.get("/agent-performance")
async def admin_agent_performance(
    authorization: Optional[str] = Header(None),
):
    """Smart-router agent stats — real per-model call volume + cost.

    2026-08-26 — ROOT FIX: this previously aggregated `cto_tasks` on a
    `model` field that DOESN'T EXIST on any document in that collection
    (cto_tasks only holds health_fix admin auto-remediation rows — no
    model attribution, ever). Result: always `per_model_30d: []`,
    contradicting the Cockpit's LLM Credits / Cost-by-Model widgets,
    which correctly read `customer_chat_cost` (the real per-call LLM
    usage ledger — same source `admin_bi.py::_fetch_inference_metrics`
    already uses for its by-model breakdown). Switched to the same
    real source. `done`/`avg_secs` (task success rate / latency) had
    no real backing data at this granularity either — dropped rather
    than fabricated; replaced with real cost + token fields."""
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    month_ago = now - 30 * 86_400

    per_model: list[dict] = []
    per_model_customers_only: list[dict] = []
    try:
        pipeline = [
            {"$match": {"ts": {"$gte": month_ago},
                        "model": {"$ne": None}}},
            {"$group": {
                "_id": "$model",
                "n": {"$sum": 1},
                "total_cost_usd": {"$sum": "$cost_usd"},
                "avg_input_tokens": {"$avg": "$input_tokens"},
                "avg_output_tokens": {"$avg": "$output_tokens"},
            }},
            {"$sort": {"n": -1}},
            {"$limit": 20},
        ]
        async for row in db.customer_chat_cost.aggregate(pipeline):
            per_model.append({
                "model":              row.get("_id"),
                "calls":              int(row.get("n") or 0),
                "total_cost_usd":     round(float(row.get("total_cost_usd") or 0), 4),
                "avg_input_tokens":   round(float(row.get("avg_input_tokens") or 0)),
                "avg_output_tokens":  round(float(row.get("avg_output_tokens") or 0)),
            })
        # 2026-08 hardening — SAME data, real-customers-only (excludes
        # the founder/admin/QA account + orphaned test/canary IDs). See
        # services/customer_cost_tracker.py::real_customer_match_stages.
        from services.customer_cost_tracker import real_customer_match_stages
        pipeline_customers = (
            pipeline[:1] + real_customer_match_stages() + pipeline[1:]
        )
        async for row in db.customer_chat_cost.aggregate(pipeline_customers):
            per_model_customers_only.append({
                "model":              row.get("_id"),
                "calls":              int(row.get("n") or 0),
                "total_cost_usd":     round(float(row.get("total_cost_usd") or 0), 4),
                "avg_input_tokens":   round(float(row.get("avg_input_tokens") or 0)),
                "avg_output_tokens":  round(float(row.get("avg_output_tokens") or 0)),
            })
    except Exception as e:
        logger.warning("admin/agent-performance: %r", e)

    return {
        "per_model_30d":               per_model,
        "per_model_30d_customers_only": per_model_customers_only,
        "source": "customer_chat_cost",
        "_note": (
            "per_model_30d = ALL traffic (includes founder/admin/QA "
            "test accounts). per_model_30d_customers_only = real "
            "paying/free customers only (Task 2 cost audit, 2026-08: "
            "95%+ of the 'all' number here was test_admin_001)."
        ),
    }


class _SeoRunPayload(BaseModel):
    project_id:      str
    plan:            str  = "swift"
    site_url:        str  = ""
    title:           str  = ""
    description:     str  = ""
    og_image:        str  = ""
    author:          str  = ""
    dry_run:         bool = True
    commit_message:  str  = "chore(seo): aurem auto-fix"


@router.post("/seo/run")
async def admin_seo_run(
    payload: _SeoRunPayload,
    authorization: Optional[str] = Header(None),
):
    admin = await _require_admin(authorization)
    from services.seo import run_seo_fixes, SeoOptions
    result = await run_seo_fixes(
        user_id=admin.get("user_id") or "",
        project_id=payload.project_id,
        options=SeoOptions(
            plan=payload.plan,
            site_url=payload.site_url,
            title=payload.title,
            description=payload.description,
            og_image=payload.og_image,
            author=payload.author,
            commit_message=payload.commit_message,
            dry_run=payload.dry_run,
        ),
    )
    return result

    return {"timings": timings, "count": len(timings)}


@router.get("/loop-metrics")
async def loop_metrics(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    now = datetime.now(timezone.utc)
    window_days = 7
    cur_start   = now - timedelta(days=window_days)
    prev_start  = now - timedelta(days=window_days * 2)

    async def _agg(gte, lt):
        pipeline = [
            {"$match": {"created_at": {"$gte": gte, "$lt": lt}}},
            {"$group": {"_id": "$state", "n": {"$sum": 1}}},
            {"$sort":  {"n": -1}},
        ]
        out: dict = {}
        async for row in db.loop_sessions.aggregate(pipeline):
            out[str(row.get("_id") or "unknown")] = int(row["n"])
        total = sum(out.values())
        failed = out.get("failed", 0)
        completed = out.get("completed", 0)
        aborted = out.get("aborted", 0)
        # Founder-facing metric: what fraction of RESOLVED sessions
        # ended in FAILED?  Excludes expired (housekeeping reaper)
        # and in-flight states so noise doesn't smear the ratio.
        resolved = failed + completed + aborted
        failed_ratio = (failed / resolved) if resolved else None
        return {
            "counts":         out,
            "total":          total,
            "resolved":       resolved,
            "failed":         failed,
            "completed":      completed,
            "failed_ratio":   failed_ratio,
        }

    current  = await _agg(cur_start, now)
    previous = await _agg(prev_start, cur_start)

    delta_ratio: Optional[float] = None
    if current["failed_ratio"] is not None and previous["failed_ratio"] is not None:
        delta_ratio = current["failed_ratio"] - previous["failed_ratio"]

    # Iter 309 · Batch-2 Item 9 — expose per-loop SSE ring-buffer
    # health so the founder can eyeball client-vs-server lag on the
    # same admin card. Read-only, in-memory only, capped by TTL
    # eviction so no new collection needed. `last_event_seq` is the
    # highest seq assigned to an event for that loop (proxy for
    # progress); `client_lag_hint` is how many buffered events sit
    # UNREAD (never consumed by any SSE client) — non-zero means a
    # user's browser is behind or disconnected. We don't try to
    # attribute lag per-client (that would need session tracking).
    try:
        from services.sse_replay_buffer import buffer_stats
        sse_stats = buffer_stats()
    except Exception:
        sse_stats = {}
    sse_summary = {
        "active_loops":  len(sse_stats),
        "total_buffered": sum(s["buffered"] for s in sse_stats.values()),
        "max_seq":       max((s["next_seq"] for s in sse_stats.values()), default=0),
    }

    # ── data_source identity ────────────────────────────────────────
    # Never leak connection strings; return only fields safe for
    # display in an admin UI so the founder can confirm the card
    # is actually reading prod (not preview / not local).
    from services.usage import is_founder_email
    mongo_url = os.environ.get("MONGO_URL", "")
    if "@" in mongo_url:
        # mongodb+srv://user:pass@host/…  → keep only the host stem
        host_hint = mongo_url.split("@", 1)[1].split("/", 1)[0]
    else:
        host_hint = mongo_url.split("//", 1)[-1].split("/", 1)[0]
    try:
        from routers.version import _COMMIT_SHA, _env_from_host
        fwd = request.headers.get("x-forwarded-host") or ""
        host = request.headers.get("host") or ""
        env_label = _env_from_host(fwd or host)
    except Exception:
        _COMMIT_SHA, env_label = "unknown", "unknown"
    data_source = {
        "db_name":     os.environ.get("DB_NAME", "unknown"),
        "mongo_host":  host_hint or "unknown",
        "commit_sha":  _COMMIT_SHA,
        "env":         env_label,
        "queried_at":  now.isoformat(),
    }

    # ── failed-session breakdown for the current window ─────────────
    # Pulls the actual 7 (or however many) failed sessions so the
    # founder can eyeball WHO owned them before treating the
    # failed_ratio as a live user-facing regression.  Classifies
    # each session's owner into: founder / admin / test / user.
    failed_cursor = db.loop_sessions.find(
        {"state": "failed", "created_at": {"$gte": cur_start, "$lt": now}},
        {
            "_id": 1, "user_id": 1, "created_at": 1, "updated_at": 1,
            "phase": 1, "current_phase": 1, "phase_history": 1,
            "error_summary": 1, "last_event": 1,
            "prompt_summary": 1,
        },
    ).sort("created_at", -1).limit(50)

    failed_sample: list = []
    async for doc in failed_cursor:
        uid = doc.get("user_id")
        user_doc = None
        if uid:
            # Iter 351 — the canonical dev_users key is the `user_id`
            # FIELD (e.g. "test_admin_001"), which is exactly what
            # loop_sessions stores. The old lookup went straight to
            # `_id` (ObjectId) — ObjectId("test_admin_001") raises →
            # bare string `_id` never matches → email empty → EVERY
            # failed session classified "orphan" (founder audit:
            # 11/11 orphans were real users mislabeled). Look up by
            # `user_id` first; keep the ObjectId path as fallback for
            # genuinely legacy rows.
            try:
                user_doc = await db.dev_users.find_one(
                    {"user_id": uid}, {"email": 1, "role": 1, "is_admin": 1})
            except Exception:
                user_doc = None
        if uid and not user_doc:
            try:
                # user_id might be stored as ObjectId or string
                from bson import ObjectId  # type: ignore
                q = {"_id": ObjectId(uid)} if not isinstance(uid, ObjectId) else {"_id": uid}
            except Exception:
                q = {"_id": uid}
            try:
                user_doc = await db.dev_users.find_one(q, {"email": 1, "role": 1, "is_admin": 1})
            except Exception:
                user_doc = None
        email = (user_doc or {}).get("email") or ""
        role  = (user_doc or {}).get("role") or ""
        is_admin_flag = bool((user_doc or {}).get("is_admin"))
        # Classification order matters — founder wins over admin
        # wins over test wins over user.
        if is_founder_email(email):
            classification = "founder"
        elif is_admin_flag or role == "admin":
            classification = "admin"
        elif email.endswith("@aurem.dev") or "test" in email.lower():
            classification = "test"
        elif not email:
            classification = "orphan"
        else:
            classification = "user"

        # Last phase attempted before failure — useful for the
        # founder to see if all 7 failed on the same phase (points
        # at a single root cause) or scattered.
        # Iter 351 — sessions persist the phase under `phase` (see
        # LoopEngine._doc); the old `current_phase`-only read rendered
        # "phase=?" on every row.
        phase = doc.get("phase") or doc.get("current_phase") or ""
        history = doc.get("phase_history") or []
        if not phase and history:
            phase = history[-1].get("phase", "") if isinstance(history[-1], dict) else ""

        # Iter 351 — error_summary isn't a session field; fall back to
        # the last SSE event message so the expand rows show WHY.
        _err = doc.get("error_summary") or ""
        if not _err:
            _le = doc.get("last_event") or {}
            if isinstance(_le, dict):
                _err = _le.get("message") or ""

        failed_sample.append({
            "session_id":     str(doc.get("_id")),
            "user_hint":      _email_hint(email),
            "classification": classification,
            "last_phase":     phase or "?",
            "error_short":    _err[:140],
            "created_at":     doc.get("created_at").isoformat()
                                if hasattr(doc.get("created_at"), "isoformat")
                                else str(doc.get("created_at") or ""),
        })

    # Owner-classification summary — makes the "who failed" answer
    # visible at a glance.
    owner_counts: dict = {}
    for row in failed_sample:
        c = row["classification"]
        owner_counts[c] = owner_counts.get(c, 0) + 1

    return {
        "ok":            True,
        "window_days":   window_days,
        "data_source":   data_source,
        "current": {
            "since_utc":  cur_start.isoformat(),
            "until_utc":  now.isoformat(),
            **current,
        },
        "previous": {
            "since_utc":  prev_start.isoformat(),
            "until_utc":  cur_start.isoformat(),
            **previous,
        },
        "delta_failed_ratio":  delta_ratio,
        "sse_buffer": sse_summary,
        "failed_sample":       failed_sample,
        "failed_owner_counts": owner_counts,
        "note": (
            "failed_ratio = failed / (failed + completed + aborted). "
            "Expired sessions (reaper-cleared) are excluded so "
            "housekeeping does not smear the signal. "
            "Cluster 1 priority rule: "
            "(a) if delta_failed_ratio > +0.05 OR "
            "(b) if failed_owner_counts.user >= 3 in the current "
            "window → treat as P0 live regression; "
            "otherwise (test/admin/founder-only failures) it is a "
            "fixture-shape / dogfood signal and the fast_timeouts "
            "fix ships as planned."
        ),
    }


@router.get("/loop-token-metrics")
async def loop_token_metrics(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    now = datetime.now(timezone.utc).timestamp()
    window_s = 7 * 24 * 3600
    cur_since = now - window_s
    prev_since = now - (2 * window_s)

    async def _agg(gte, lt):
        # `ts` is stored as a UNIX timestamp float by cost_tracker.log_call
        # (see services/ora_chat/cost_tracker.py line ~218 — `now = time.time()`).
        pipeline = [
            {"$match": {
                "ts":    {"$gte": gte, "$lt": lt},
                "route": {"$regex": "^loop\\."},
            }},
            {"$group": {
                "_id": "$route",
                "calls":          {"$sum": 1},
                "input_tokens":   {"$sum": "$input_tokens"},
                "output_tokens":  {"$sum": "$output_tokens"},
                "cost_usd":       {"$sum": "$cost_usd"},
                "loop_sessions":  {"$addToSet": "$session_id"},
            }},
            {"$sort":  {"cost_usd": -1}},
        ]
        by_phase: dict = {}
        total_calls, total_in, total_out, total_cost = 0, 0, 0, 0.0
        loop_ids: set = set()
        async for row in db.ora_chat_usage.aggregate(pipeline):
            route = str(row.get("_id") or "")
            phase = route.split(".", 1)[-1] if "." in route else route
            calls = int(row.get("calls") or 0)
            inp   = int(row.get("input_tokens") or 0)
            outp  = int(row.get("output_tokens") or 0)
            cost  = float(row.get("cost_usd") or 0.0)
            sess  = list(row.get("loop_sessions") or [])
            by_phase[phase] = {
                "calls":          calls,
                "input_tokens":   inp,
                "output_tokens":  outp,
                "cost_usd":       round(cost, 6),
                "loop_sessions":  len(sess),
            }
            total_calls += calls
            total_in    += inp
            total_out   += outp
            total_cost  += cost
            loop_ids.update(sess)

        avg_per_loop = None
        if loop_ids:
            avg_per_loop = {
                "loops":           len(loop_ids),
                "input_tokens":    total_in  // len(loop_ids),
                "output_tokens":   total_out // len(loop_ids),
                "cost_usd":        round(total_cost / len(loop_ids), 6),
            }
        return {
            "by_phase":       by_phase,
            "total_calls":    total_calls,
            "total_input":    total_in,
            "total_output":   total_out,
            "total_cost_usd": round(total_cost, 6),
            "distinct_loops": len(loop_ids),
            "avg_per_loop":   avg_per_loop,
        }

    current  = await _agg(cur_since,  now)
    previous = await _agg(prev_since, cur_since)

    try:
        from routers.version import _COMMIT_SHA, _env_from_host
        fwd = request.headers.get("x-forwarded-host") or ""
        host = request.headers.get("host") or ""
        env_label = _env_from_host(fwd or host)
    except Exception:
        _COMMIT_SHA, env_label = "unknown", "unknown"
    mongo_url = os.environ.get("MONGO_URL", "")
    if "@" in mongo_url:
        host_hint = mongo_url.split("@", 1)[1].split("/", 1)[0]
    else:
        host_hint = mongo_url.split("//", 1)[-1].split("/", 1)[0]

    return {
        "ok":           True,
        "window_days":  7,
        "data_source": {
            "db_name":    os.environ.get("DB_NAME", "unknown"),
            "mongo_host": host_hint or "unknown",
            "commit_sha": _COMMIT_SHA,
            "env":        env_label,
        },
        "current": {
            "since_utc": datetime.fromtimestamp(cur_since, tz=timezone.utc).isoformat(),
            "until_utc": datetime.fromtimestamp(now,       tz=timezone.utc).isoformat(),
            **current,
        },
        "previous": {
            "since_utc": datetime.fromtimestamp(prev_since, tz=timezone.utc).isoformat(),
            "until_utc": datetime.fromtimestamp(cur_since,  tz=timezone.utc).isoformat(),
            **previous,
        },
        "note": (
            "Aggregated from `ora_chat_usage` where route ^= 'loop.'. "
            "One row per loop-originated LLM call (Council A plan / "
            "Parliament execute / verify healer). Cost is computed at "
            "log time using services/ora_chat/cost_tracker's shipped "
            "price table (deepseek/perplexity/glm/claude-sonnet) — "
            "unknown models fall to the conservative default "
            "$1/$3 per 1M in/out. Baseline for Phase 1 cost delta."
        ),
    }


@router.get("/loop-inspect/{loop_id}")
async def loop_inspect(
    loop_id: str,
    tail: int = 20,
    authorization: Optional[str] = Header(None),
):
    user = await _require_admin(authorization)
    db = require_db()

    session = await db.loop_sessions.find_one({"loop_id": loop_id})
    if not session:
        raise HTTPException(404, "Loop not found")
    # Owner-scope: admin bypass only for founder tier; other admins
    # can inspect only their own loops. This mirrors /loop/{id}/status
    # scoping semantics for defence-in-depth.
    is_founder = (user or {}).get("tier") == "founder" or \
                 (user or {}).get("role") == "founder"
    if not is_founder and session.get("user_id") != (user or {}).get("user_id"):
        raise HTTPException(403, "Not your loop")

    session.pop("_id", None)
    # Redact potentially-sensitive ship_pending token so an inspection
    # UI can never leak a GitHub PAT even to an authorised viewer.
    ctx = session.get("context") or {}
    if isinstance(ctx.get("ship_pending"), dict):
        ctx["ship_pending"] = {k: v for k, v in ctx["ship_pending"].items()
                               if k != "token"}
        session["context"] = ctx

    tail_n = max(1, min(int(tail or 20), 200))
    events: list = []
    try:
        cursor = db.loop_events.find(
            {"loop_id": loop_id},
            {"_id": 0},
        ).sort([("ts", -1), ("seq", -1)]).limit(tail_n)
        async for row in cursor:
            events.append(row)
    except Exception as _e:
        events = [{"__error": f"loop_events read failed: {_e!r}"}]

    # Per-loop entry from Item 6 ring buffer. Import lazily and guard
    # so the endpoint still degrades gracefully if the module or its
    # helper are absent (e.g. rollback scenario).
    sse_entry: dict | None = None
    sse_events_raw: list = []
    try:
        from services import sse_replay_buffer as _sse_buf
        # buffer_stats() returns { loop_id: {next_seq, buffered, last_touched, ended_at}, ... }
        all_stats = _sse_buf.buffer_stats() if hasattr(_sse_buf, "buffer_stats") else {}
        sse_entry = (all_stats or {}).get(loop_id)
        # Iter 316 · Fix D — also return raw replay events for this
        # loop_id so the founder can see EXACTLY what the SSE client
        # would replay on connect (was the plan-ready event actually
        # in the buffer? or was the drain-by-driver race a fiction?).
        # Zero mutation; read-only.
        if hasattr(_sse_buf, "buffer_events"):
            sse_events_raw = _sse_buf.buffer_events(loop_id, max_events=200)
    except Exception as _e:
        sse_entry = {"__error": f"sse_replay_buffer inspect failed: {_e!r}"}

    return {
        "ok":            True,
        "loop_id":       loop_id,
        "session":       session,
        "events_tail":   events,   # newest-first
        "sse_buffer":    sse_entry,
        "sse_buffer_events": sse_events_raw,   # Iter 316 · Fix D
        "generated_at":  datetime.now(timezone.utc).isoformat(),
    }


@router.get("/speed-diagnostic")
async def speed_diagnostic(
    window_days: int = 30,
    sample: int = 20,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.loop_speed_diagnostic import compute_speed_report
    return await compute_speed_report(
        require_db(),
        window_days=max(1, min(int(window_days or 30), 180)),
        sample_target=max(1, min(int(sample or 20), 100)),
    )


@router.get("/scope-drift-audit")
async def scope_drift_audit(
    days: int = 30,
    limit: int = 50,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days or 30), 180)))
    cap = max(1, min(int(limit or 50), 500))

    # loop_events.ts is stored as ISO string in most call sites; we
    # match both string and datetime for defensive compatibility.
    query = {"kind": "scope_drift"}
    cursor = db.loop_events.find(query, {"_id": 0}).sort([("ts", -1)]).limit(cap)
    rows: list[dict] = []
    async for ev in cursor:
        ts = ev.get("ts")
        # Best-effort in-window filter (ISO strings sort lexically).
        if isinstance(ts, str):
            try:
                ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if ts_dt < cutoff:
                    continue
            except Exception:
                pass
        elif isinstance(ts, datetime):
            if ts < cutoff:
                continue
        rows.append(ev)

    # Aggregate patterns
    from collections import Counter
    extras_counter: Counter = Counter()
    per_loop: dict[str, dict] = {}
    total_extras = 0
    for r in rows:
        extras = r.get("extras") or []
        total_extras += len(extras)
        for e in extras:
            extras_counter[e] += 1
        lid = r.get("loop_id")
        if lid and lid not in per_loop:
            per_loop[lid] = {
                "loop_id":      lid,
                "ts":           r.get("ts"),
                "frozen_count": len(r.get("frozen") or []),
                "extras_count": len(extras),
                "extras":       extras[:20],
            }

    return {
        "ok":                    True,
        "generated_at":          datetime.now(timezone.utc).isoformat(),
        "window_days":           days,
        "limit":                 cap,
        "total_drift_events":    len(rows),
        "distinct_loops":        len(per_loop),
        "avg_extras_per_drift":  round(total_extras / len(rows), 2) if rows else 0,
        "most_frequent_extra_paths": [
            {"path": p, "seen_in_loops": n}
            for p, n in extras_counter.most_common(15)
        ],
        "samples": list(per_loop.values())[:20],
        "notes": [
            "Iter 311 · Fix C ships a structural invariant: candidates ⊆ "
            "planner_set. Once deployed, NEW scope_drift events with "
            "file_selector-sourced extras should stop appearing. "
            "Extras seen AFTER this fix's deploy timestamp indicate "
            "planner-side bloat (a different failure mode) rather than "
            "file_selector expansion.",
            "loop_events.kind='scope_drift' rows are ONLY written when "
            "extras were detected (loop_engine.py:1093). Absence of an "
            "audit row does NOT mean the loop was clean — it means no "
            "drift was flagged.",
        ],
    }
