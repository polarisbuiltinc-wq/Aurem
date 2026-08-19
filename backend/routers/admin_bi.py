"""admin_bi.py — Slice A · Business Intelligence Cockpit endpoints.

Live financial + inference-cost telemetry for the founder cockpit.
Extends /admin/financials by exposing:

  GET  /admin/bi/stripe-metrics    — live Stripe subscription pull
                                     (MRR / ARR / active / new-30d /
                                     canceled-30d). No DB caching yet;
                                     Stripe list_subscriptions is cheap
                                     (a few 100ms) and this endpoint is
                                     founder-only.
  GET  /admin/bi/inference-metrics — aggregates `ora_chat_usage` for
                                     today / month / 30d timeseries /
                                     by-model / by-route breakdowns.
                                     Uses the already-shipped budget
                                     tracker so the "mode" badge
                                     (normal/warning/economy/spike)
                                     matches what the chat router
                                     actually enforces.
  GET  /admin/bi/summary           — one-shot payload combining both
                                     of the above (single request the
                                     admin cockpit hits on load).

No hallucination rule enforced end-to-end:
  • If Stripe key missing → status="missing_key", numbers are all 0
    with an explicit `error` message. Front-end renders "No data yet"
    instead of $0.00 MRR silently.
  • If a customer has no subscriptions → active=0, everything downstream
    is 0. No projections, no catalog-price fallbacks — this file is
    exclusively for LIVE stripe data.
  • Every dollar amount is derived from a real Stripe object or a real
    `ora_chat_usage` row. Preview snapshots today should read $0 MRR +
    ~$0.12 inference cost from real historical rows.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from cto_services.auth import require_admin_dep
from cto_services.db import require_db
from routers._admin_common import _require_admin
from services.stripe_client import stripe_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/bi",
    tags=["Admin-BI"],
    dependencies=[Depends(require_admin_dep)],
)


# ─── Stripe helpers ────────────────────────────────────────────────
def _subscription_monthly_usd(sub) -> float:
    """Convert a Stripe subscription's line items into monthly USD.

    Only sums recurring items priced in USD. Ignores tax/discount lines
    (those come back as separate objects on the invoice, not here).
    """
    total = 0.0
    items = ((sub or {}).get("items") or {}).get("data") or []
    for it in items:
        price = it.get("price") or {}
        rec = price.get("recurring") or {}
        if not rec:
            continue
        currency = (price.get("currency") or "").lower()
        if currency != "usd":
            continue
        unit_amount = float(price.get("unit_amount") or 0) / 100.0  # cents → USD
        quantity = int(it.get("quantity") or 1)
        interval = rec.get("interval") or "month"
        interval_count = int(rec.get("interval_count") or 1)
        # Normalise to a monthly figure.
        if interval == "month":
            monthly = unit_amount * quantity / max(1, interval_count)
        elif interval == "year":
            monthly = (unit_amount * quantity) / (12 * max(1, interval_count))
        elif interval == "week":
            monthly = unit_amount * quantity * 4.345 / max(1, interval_count)
        elif interval == "day":
            monthly = unit_amount * quantity * 30 / max(1, interval_count)
        else:
            monthly = 0.0
        total += monthly
    return round(total, 2)


async def _fetch_stripe_metrics() -> dict:
    """Live Stripe subscription pull. Fails soft — returns a shape the
    UI can always render, with `status` telling the truth."""
    key = stripe_key()
    if not key:
        return {
            "status":              "missing_key",
            "error":               "Stripe key not configured. Set it via /admin/stripe-config.",
            "mode":                "unknown",
            "mrr_usd":             0.0,
            "arr_usd":             0.0,
            "active_subs":         0,
            "trialing_subs":       0,
            "past_due_subs":       0,
            "new_30d":             0,
            "canceled_30d":        0,
            "arpu_usd":            0.0,
            "generated_at":        datetime.now(timezone.utc).isoformat(),
        }

    import stripe as _stripe
    _stripe.api_key = key

    mode = "live" if key.startswith("sk_live_") else "test"

    # Cutoff timestamp for 30-day windows.
    cutoff_30d = int(time.time()) - (30 * 86_400)

    active_subs = 0
    trialing_subs = 0
    past_due_subs = 0
    new_30d = 0
    canceled_30d = 0
    mrr_total = 0.0

    try:
        # Auto-paginate all non-canceled subscriptions. Small biz today
        # so this is bounded (< 100 pages of 100). If we ever grow into
        # thousands of subs we swap in a cached day-old aggregate.
        it = await asyncio.to_thread(
            _stripe.Subscription.list, status="all", limit=100,
            expand=["data.items.data.price"],
        )
        # `.auto_paging_iter()` is a generator; wrap the sync iteration
        # so we don't block the event loop on Stripe latency.
        def _walk():
            rows = []
            for sub in it.auto_paging_iter():
                rows.append(dict(sub))
            return rows

        subs = await asyncio.to_thread(_walk)
        for sub in subs:
            status = sub.get("status") or ""
            created = int(sub.get("created") or 0)
            canceled_at = int(sub.get("canceled_at") or 0)

            if status == "active":
                active_subs += 1
                mrr_total += _subscription_monthly_usd(sub)
            elif status == "trialing":
                trialing_subs += 1
                # Trialing = not yet paying → don't add to MRR.
            elif status == "past_due":
                past_due_subs += 1
                # Still counts toward MRR (Stripe considers it active
                # billing-wise; the founder should see the number they
                # WILL collect if the customer's card recovers).
                mrr_total += _subscription_monthly_usd(sub)

            if created >= cutoff_30d:
                new_30d += 1
            if canceled_at and canceled_at >= cutoff_30d:
                canceled_30d += 1
    except Exception as e:
        logger.warning("bi.stripe-metrics: Stripe list_subscriptions failed: %r", e)
        return {
            "status":              "error",
            "error":               f"Stripe API error: {str(e)[:200]}",
            "mode":                mode,
            "mrr_usd":             0.0,
            "arr_usd":             0.0,
            "active_subs":         0,
            "trialing_subs":       0,
            "past_due_subs":       0,
            "new_30d":             0,
            "canceled_30d":        0,
            "arpu_usd":            0.0,
            "generated_at":        datetime.now(timezone.utc).isoformat(),
        }

    paying = active_subs + past_due_subs
    arpu = round(mrr_total / paying, 2) if paying > 0 else 0.0

    return {
        "status":              "ok",
        "error":               "",
        "mode":                mode,
        "mrr_usd":             round(mrr_total, 2),
        "arr_usd":             round(mrr_total * 12, 2),
        "active_subs":         active_subs,
        "trialing_subs":       trialing_subs,
        "past_due_subs":       past_due_subs,
        "new_30d":             new_30d,
        "canceled_30d":        canceled_30d,
        "arpu_usd":            arpu,
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "_note": (
            "MRR = sum of recurring USD unit_amount over subs where "
            "status ∈ {active, past_due}, normalised to a monthly "
            "figure. ARR = MRR × 12. Trialing subs are counted but "
            "excluded from MRR (they're not paying yet). Fetched live "
            "from Stripe on each request — no caching."
        ),
    }


# ─── Inference metrics from ora_chat_usage + customer_chat_cost ────
async def _fetch_inference_metrics() -> dict:
    """30-day inference cost snapshot + budget mode.

    2026-08-19 fix: this used to read `ora_chat_usage` ONLY, which is
    admin-ORA-tool-only (see services/ora_chat/cost_tracker.py) — real
    customer `/chat/send` and `/chat/stream` traffic was 0% covered
    (confirmed via preview audit: 0 of 2,739 real turns had a cost
    row). `customer_chat_cost` (services/customer_cost_tracker.py) now
    covers that path with a char-count token ESTIMATE (not exact
    provider-reported usage — flagged per-row as `estimation_method`).
    `today_usd`/`month_usd`/`by_model`/`by_route`/`daily_series_30d`
    below are now the COMBINED real total; `admin_tool_*_usd` keeps the
    admin-ORA-only figure the personal $30/day guard is scoped to
    (see `budget` below — deliberately UNCHANGED, still admin-tool-only,
    so that guard's real email alerts stay correctly scoped)."""
    db = require_db()
    from services.ora_chat import cost_tracker as _ct

    now = time.time()
    cutoff_30d = now - (30 * 86_400)

    # Admin-ORA-tool-only figures — unchanged, backs the personal
    # $30/day budget guard (services/ora_chat/cost_tracker.py).
    admin_today_usd = await _ct.current_day_spend_usd(now)
    admin_month_usd = await _ct.current_month_spend_usd(now)
    budget = await _ct.budget_status()

    # Real customer chat cost — new collection, see module docstring.
    cust_today_usd = 0.0
    cust_month_usd = 0.0
    try:
        cust_today_usd = await _sum_cost(db.customer_chat_cost, _ct._current_day_key(now), "ts_day")
        cust_month_usd = await _sum_cost(db.customer_chat_cost, _ct._current_month_key(now), "ts_month")
    except Exception as e:
        logger.warning("bi.inference-metrics: customer cost sum failed: %r", e)

    today_usd = admin_today_usd + cust_today_usd
    month_usd = admin_month_usd + cust_month_usd

    # 30-day daily timeseries — merged across both collections.
    daily_map: dict = {}
    for coll in (db.ora_chat_usage, db.customer_chat_cost):
        try:
            pipe = [
                {"$match": {"ts": {"$gte": cutoff_30d}}},
                {"$group": {
                    "_id":    "$ts_day",
                    "cost":   {"$sum": "$cost_usd"},
                    "calls":  {"$sum": 1},
                    "tokens": {"$sum": {"$add": ["$input_tokens", "$output_tokens"]}},
                }},
            ]
            async for row in coll.aggregate(pipe):
                d = daily_map.setdefault(row.get("_id"), {"day": row.get("_id"), "cost": 0.0, "calls": 0, "tokens": 0})
                d["cost"]   += float(row.get("cost") or 0)
                d["calls"]  += int(row.get("calls") or 0)
                d["tokens"] += int(row.get("tokens") or 0)
        except Exception as e:
            logger.warning("bi.inference-metrics: daily aggregate failed: %r", e)
    daily = sorted(
        [{**d, "cost": round(d["cost"], 6)} for d in daily_map.values()],
        key=lambda d: d["day"] or "",
    )

    # By-model (last 30 days) — merged.
    model_map: dict = {}
    for coll in (db.ora_chat_usage, db.customer_chat_cost):
        try:
            pipe = [
                {"$match": {"ts": {"$gte": cutoff_30d}}},
                {"$group": {
                    "_id":    "$model",
                    "cost":   {"$sum": "$cost_usd"},
                    "calls":  {"$sum": 1},
                    "tokens": {"$sum": {"$add": ["$input_tokens", "$output_tokens"]}},
                }},
            ]
            async for row in coll.aggregate(pipe):
                key = row.get("_id") or "unknown"
                m = model_map.setdefault(key, {"model": key, "cost": 0.0, "calls": 0, "tokens": 0})
                m["cost"]   += float(row.get("cost") or 0)
                m["calls"]  += int(row.get("calls") or 0)
                m["tokens"] += int(row.get("tokens") or 0)
        except Exception as e:
            logger.warning("bi.inference-metrics: by-model aggregate failed: %r", e)
    by_model = sorted(
        [{**m, "cost": round(m["cost"], 6)} for m in model_map.values()],
        key=lambda m: -m["cost"],
    )[:15]

    # By-route (last 30 days) — merged.
    route_map: dict = {}
    for coll in (db.ora_chat_usage, db.customer_chat_cost):
        try:
            pipe = [
                {"$match": {"ts": {"$gte": cutoff_30d}}},
                {"$group": {"_id": "$route", "cost": {"$sum": "$cost_usd"}, "calls": {"$sum": 1}}},
            ]
            async for row in coll.aggregate(pipe):
                key = row.get("_id") or "unknown"
                r = route_map.setdefault(key, {"route": key, "cost": 0.0, "calls": 0})
                r["cost"]  += float(row.get("cost") or 0)
                r["calls"] += int(row.get("calls") or 0)
        except Exception as e:
            logger.warning("bi.inference-metrics: by-route aggregate failed: %r", e)
    by_route = sorted(
        [{**r, "cost": round(r["cost"], 6)} for r in route_map.values()],
        key=lambda r: -r["cost"],
    )[:15]

    return {
        "today_usd":              round(today_usd, 6),
        "month_usd":              round(month_usd, 6),
        "admin_tool_today_usd":   round(admin_today_usd, 6),
        "admin_tool_month_usd":   round(admin_month_usd, 6),
        "customer_chat_today_usd": round(cust_today_usd, 6),
        "customer_chat_month_usd": round(cust_month_usd, 6),
        "budget":           budget,
        "daily_series_30d": daily,
        "by_model":         by_model,
        "by_route":         by_route,
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "_note": (
            "today_usd/month_usd = REAL COMBINED cost: admin ORA-tool "
            "usage (`ora_chat_usage`, exact) + customer chat "
            "(`customer_chat_cost`, char-count ESTIMATE — see "
            "services/customer_cost_tracker.py, no exact provider "
            "token usage is threaded through yet). `budget` below is "
            "scoped to admin-tool-only spend (unaffected by customer "
            "volume) and is what the $30/day personal guard enforces. "
            "Budget modes: normal < 70% of daily soft cap; warning "
            "≥ 70%; economy ≥ 100% (forces GLM-5.2 route); "
            "spike_hard_stop ≥ daily spike cap (blocks new chats)."
        ),
    }

async def _sum_cost(collection, key_value: str, key_field: str) -> float:
    pipe = [
        {"$match": {key_field: key_value}},
        {"$group": {"_id": None, "total": {"$sum": "$cost_usd"}}},
    ]
    total = 0.0
    async for row in collection.aggregate(pipe):
        total = float(row.get("total") or 0.0)
    return total



# ─── Endpoints ─────────────────────────────────────────────────────
@router.get("/stripe-metrics")
async def stripe_metrics(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    return await _fetch_stripe_metrics()


@router.get("/inference-metrics")
async def inference_metrics(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    return await _fetch_inference_metrics()


@router.get("/summary")
async def summary(authorization: Optional[str] = Header(None)):
    """One-shot payload the cockpit hits on load. Runs both fetches
    concurrently so the UI paints without a waterfall."""
    await _require_admin(authorization)
    stripe_task = asyncio.create_task(_fetch_stripe_metrics())
    infer_task  = asyncio.create_task(_fetch_inference_metrics())
    stripe_data, infer_data = await asyncio.gather(
        stripe_task, infer_task, return_exceptions=False,
    )

    # Net-margin projection: MRR minus month-to-date inference cost
    # PLUS a pro-rated projection for the remainder of the month.
    mrr = float(stripe_data.get("mrr_usd") or 0)
    month_infer = float(infer_data.get("month_usd") or 0)
    now = datetime.now(timezone.utc)
    days_elapsed = max(1, now.day)
    days_in_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    days_total = days_in_month.day
    projected_month_infer = round(month_infer / days_elapsed * days_total, 2)
    net_margin_usd = round(mrr - projected_month_infer, 2)
    net_margin_pct = round((net_margin_usd / mrr * 100), 1) if mrr > 0 else 0.0

    return {
        "stripe":              stripe_data,
        "inference":           infer_data,
        "projected_month_infer_usd": projected_month_infer,
        "net_margin_usd":      net_margin_usd,
        "net_margin_pct":      net_margin_pct,
        "generated_at":        datetime.now(timezone.utc).isoformat(),
    }
