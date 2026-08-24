"""
services/cost_revenue_alert_cron.py — 2026-08-27, Live Cost Alert.

Margin-risk alert: fires when AI inference cost exceeds customer
revenue, at two levels the founder asked for:
  - AGGREGATE: total customer_chat_cost.cost_usd vs total cto_payments
    (payment_status='paid') amount over a trailing 30d window — the
    same two numbers `/admin/token-pnl` already computes for the
    cockpit card, just watched proactively instead of only "when
    someone looks".
  - PER-CUSTOMER: for any user who actually paid in the window,
    whether THEIR usage cost this window exceeds what THEY paid this
    window. Free/trial users are excluded by design — running cost
    with $0 revenue is expected there, not a margin anomaly.

Reuses the existing G10 alert pattern (services/founder_alerts.py):
same 6h dedup-per-source_key shape, same fail-silent posture, same
main.py `_supervise()` long-lived-task wrapper as slo_alert_cron.

Preview safety (founder decision, 2026-08-27): real email send is
gated behind ENABLE_COST_REVENUE_ALERT_EMAIL (default OFF). Every
tick still computes + records the finding to
db.cost_revenue_alert_log (surfaced on the admin Overview "Live Cost
Alert" card via GET /admin/insights/cost-alert) even with email off —
log-only means no inbox spam, not no visibility.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

PERIOD_DAYS = 30
MIN_AGGREGATE_COST_USD = 1.0     # floor — don't page on a few cents
MIN_PER_CUSTOMER_COST_USD = 0.50
DEDUP_WINDOW_HOURS = 6


def _interval_seconds() -> int:
    raw = (os.environ.get("COST_ALERT_INTERVAL_SEC") or "1800").strip()
    try:
        return max(300, int(raw))
    except ValueError:
        return 1800


def _is_enabled() -> bool:
    v = (os.environ.get("ENABLE_COST_ALERT_CRON") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def _email_enabled() -> bool:
    v = (os.environ.get("ENABLE_COST_REVENUE_ALERT_EMAIL") or "0").strip().lower()
    return v in ("1", "true", "on", "yes")


async def compute_cost_revenue_status(db, period_days: int = PERIOD_DAYS) -> dict:
    """Real numbers, no cache — same collections /admin/token-pnl reads.
    Called by both the cron tick and the admin card's on-demand GET."""
    now = time.time()
    since = now - period_days * 86400

    cost_by_user: dict = {}
    async for d in db.customer_chat_cost.aggregate([
        {"$match": {"ts": {"$gte": since}}},
        {"$group": {"_id": "$user_id", "cost": {"$sum": "$cost_usd"}}},
    ]):
        uid = d.get("_id") or "unknown"
        cost_by_user[uid] = round(float(d.get("cost") or 0), 4)
    ai_cost_total = round(sum(cost_by_user.values()), 2)

    revenue_by_user: dict = {}
    async for d in db.cto_payments.aggregate([
        {"$match": {"created_at": {"$gte": since}, "payment_status": "paid"}},
        {"$group": {"_id": "$user_id", "amount": {"$sum": "$amount"}}},
    ]):
        uid = d.get("_id") or "unknown"
        revenue_by_user[uid] = round(float(d.get("amount") or 0), 2)
    revenue_total = round(sum(revenue_by_user.values()), 2)

    aggregate_breach = (
        ai_cost_total > revenue_total
        and ai_cost_total >= MIN_AGGREGATE_COST_USD
    )

    offenders = []
    for uid, revenue in revenue_by_user.items():
        cost = cost_by_user.get(uid, 0.0)
        if cost > revenue and cost >= MIN_PER_CUSTOMER_COST_USD:
            offenders.append({
                "user_id": uid, "cost_usd": cost, "revenue_usd": revenue,
                "overage_usd": round(cost - revenue, 2),
            })
    offenders.sort(key=lambda o: o["overage_usd"], reverse=True)

    for o in offenders[:20]:
        o["email"] = ""
        try:
            u = await db.dev_users.find_one({"user_id": o["user_id"]}, {"email": 1})
            if u:
                o["email"] = u.get("email") or ""
        except Exception:
            pass

    return {
        "period_days": period_days,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "ai_cost_total": ai_cost_total,
        "revenue_total": revenue_total,
        "aggregate_breach": aggregate_breach,
        "paying_customers": len(revenue_by_user),
        "offenders": offenders[:20],
        "offenders_count": len(offenders),
        "email_enabled": _email_enabled(),
    }


async def _recent_dedup_hit(db, source_key: str) -> bool:
    since = datetime.now(timezone.utc) - timedelta(hours=DEDUP_WINDOW_HOURS)
    try:
        row = await db.cost_revenue_alert_log.find_one({
            "source_key": source_key, "created_at": {"$gte": since},
        })
        return row is not None
    except Exception:
        return False


async def _record_finding(db, *, source_key: str, title: str, detail: str) -> dict:
    """Always writes the audit row (visible via the admin card); only
    fires the real Resend email when ENABLE_COST_REVENUE_ALERT_EMAIL=1."""
    if await _recent_dedup_hit(db, source_key):
        return {"recorded": False, "reason": "dedup"}

    emailed = False
    if _email_enabled():
        from services.founder_alerts import send_founder_alert
        resp = await send_founder_alert(
            db, source_key=source_key, title=title, detail=detail,
            level="critical", guard="cost_revenue_alert",
        )
        emailed = bool(resp.get("sent"))
    else:
        logger.warning(
            "💸 cost_revenue_alert (log-only, ENABLE_COST_REVENUE_ALERT_EMAIL=0) "
            "— %s: %s", title, detail,
        )

    try:
        await db.cost_revenue_alert_log.insert_one({
            "source_key": source_key, "title": title, "detail": detail,
            "level": "critical", "guard": "cost_revenue_alert",
            "created_at": datetime.now(timezone.utc), "emailed": emailed,
        })
    except Exception:
        pass
    return {"recorded": True, "emailed": emailed}


async def _check_and_alert_once(db) -> None:
    status = await compute_cost_revenue_status(db)

    if status["aggregate_breach"]:
        await _record_finding(
            db,
            source_key="cost_alert_aggregate",
            title="AI cost exceeds customer revenue (aggregate, 30d)",
            detail=(
                f"AI cost ${status['ai_cost_total']} vs revenue "
                f"${status['revenue_total']} over the last 30 days "
                f"({status['paying_customers']} paying customers)."
            ),
        )

    for o in status["offenders"]:
        await _record_finding(
            db,
            source_key=f"cost_alert_user_{o['user_id']}",
            title=f"Customer AI cost exceeds their revenue — {o.get('email') or o['user_id']}",
            detail=(
                f"user_id={o['user_id']} cost=${o['cost_usd']} "
                f"revenue=${o['revenue_usd']} overage=${o['overage_usd']} "
                f"(last 30 days)."
            ),
        )


async def schedule_cost_revenue_alert_cron() -> None:
    """Background scheduler — kicked off from main.py startup, same
    shape as schedule_slo_alert_cron / schedule_integration_health_cron."""
    if not _is_enabled():
        logger.info("cost_revenue_alert cron disabled via env")
        return

    interval = _interval_seconds()
    logger.info(
        "💸 cost_revenue_alert cron ON · every %ds · email=%s",
        interval, _email_enabled(),
    )
    await asyncio.sleep(200)  # boot stagger, after slo_alert_cron's 180s

    _index_ensured = False
    while True:
        try:
            from cto_services.db import get_db
            db = get_db()
            if db is not None:
                if not _index_ensured:
                    # code-review follow-up (2026-08-27 testing pass) —
                    # best-effort, non-unique: keeps the dedup lookup +
                    # admin card's recent_findings sort off a full scan
                    # as this collection grows. Created once per process.
                    try:
                        await db.cost_revenue_alert_log.create_index(
                            [("source_key", 1), ("created_at", -1)],
                            name="source_key_created_at", background=True,
                        )
                        _index_ensured = True
                    except Exception:
                        pass
                await _check_and_alert_once(db)
        except Exception as e:                              # noqa: BLE001
            logger.warning("cost_revenue_alert cron tick failed: %r", e)
        await asyncio.sleep(interval)
