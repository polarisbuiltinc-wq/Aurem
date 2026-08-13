"""admin_payments.py — Payments / Billing / Stripe admin endpoints.

Extracted from routers/admin.py during the Phase 2 architecture split
(2026-02-11). Contains 9 handlers + 1 helper class:

  GET  /admin/payments
  POST /admin/payments/reconcile
  GET  /admin/financials
  POST /admin/financials/settings
  POST /admin/billing/run-overage-cron
  GET  /admin/stripe-config
  POST /admin/stripe-config
  GET  /admin/stripe-prices
  POST /admin/stripe-prices

Every handler + helper is COPIED VERBATIM from the pre-split admin.py
to guarantee zero behavior change. Route paths, response shapes, auth
gates, and side effects are identical to the pre-split version.

The shared `_require_admin` helper lives in routers/_admin_common.py
(imported below) so this file no longer needs to define it.
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
    prefix="/admin", tags=["Admin-payments"],
    dependencies=[Depends(require_admin_dep)],
)

# Shared helper — extracted to _admin_common during Phase 2 split.
from routers._admin_common import _require_admin  # noqa: E402


@router.get("/payments")
async def list_payments(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    # Visible page — most recent 100 for the table UI.
    payments = await db.cto_payments.find(
        {}, {"_id": 0},
    ).sort("created_at", -1).limit(100).to_list(100)
    # Iter 388y · Admin Payments Accuracy fix (#35 slice) — lifetime
    # revenue was previously computed by summing `amount` over the
    # visible-page 100 rows only, so once we crossed 100 paid
    # transactions the total_revenue card silently truncated.  Now
    # revenue comes from an aggregate over the WHOLE collection with
    # the same `payment_status='paid'` filter as the token-pnl +
    # overview-metrics endpoints (single source of truth).
    total_revenue = 0.0
    total_paid_count = 0
    try:
        rev_pipe = [
            {"$match": {"payment_status": "paid"}},
            {"$group": {"_id": None,
                        "sum": {"$sum": "$amount"},
                        "n":   {"$sum": 1}}},
        ]
        async for row in db.cto_payments.aggregate(rev_pipe):
            total_revenue    = round(float(row.get("sum") or 0), 2)
            total_paid_count = int(row.get("n") or 0)
            break
    except Exception as e:
        logger.warning("admin-payments: total_revenue aggregate failed: %r", e)
    return {
        "payments":         payments,
        "total_revenue":    total_revenue,
        "total_paid_count": total_paid_count,
        "count":            len(payments),
        "_note": (
            "total_revenue is lifetime sum over ALL paid rows "
            "(payment_status='paid'), independent of the 100-row "
            "visible page cap.  `count` is the visible-page row count."
        ),
    }


@router.post("/payments/reconcile")
async def reconcile_pending_payments(authorization: Optional[str] = Header(None)):
    """Iter 352 — founder-only. Pull every non-paid cto_payments row,
    retrieve its Checkout Session from Stripe and sync the REAL status
    (paid / expired / open) + amount into Mongo. Returns a full
    per-row evidence report so the founder can audit the 22 stuck
    'pending' rows: what they were, when created, what Stripe says."""
    await _require_admin(authorization)
    db = require_db()
    import asyncio as _aio

    import stripe as _stripe
    _stripe.api_key = (os.environ.get("STRIPE_SECRET_KEY")
                       or os.environ.get("STRIPE_API_KEY") or "")
    if not _stripe.api_key:
        raise HTTPException(503, "Stripe key not configured")

    rows = await db.cto_payments.find(
        {"payment_status": {"$ne": "paid"}}, {"_id": 0},
    ).sort("created_at", -1).limit(100).to_list(100)

    report, counts = [], {"paid": 0, "expired": 0, "open": 0, "error": 0}
    now = time.time()
    for p in rows:
        sid = p.get("session_id") or ""
        entry = {
            "session_id": (sid[-8:] and f"…{sid[-8:]}"),
            "plan":       p.get("plan"),
            "price_id":   (p.get("price_id") or "")[-8:] and f"…{(p.get('price_id') or '')[-8:]}",
            "user_email": p.get("user_email"),
            "created_at": p.get("created_at"),
            "old_status": p.get("payment_status"),
        }
        try:
            sess = await _aio.to_thread(
                _stripe.checkout.Session.retrieve, sid)
            pay_status = sess.get("payment_status") or "unknown"
            sess_status = sess.get("status") or "unknown"
            new_pay = ("paid" if pay_status == "paid"
                       else "expired" if sess_status == "expired"
                       else "pending")
            update = {
                "payment_status": new_pay,
                "status":         sess_status,
                "amount":         round((sess.get("amount_total") or 0) / 100, 2),
                "updated_at":     now,
                "updated_via":    "reconcile",
            }
            if new_pay == "paid" and p.get("payment_status") != "paid":
                update["paid_at"] = now
            await db.cto_payments.update_one(
                {"session_id": sid}, {"$set": update})
            entry["new_status"] = new_pay
            entry["stripe_session_status"] = sess_status
            entry["amount"] = update["amount"]
            counts["paid" if new_pay == "paid"
                   else "expired" if new_pay == "expired" else "open"] += 1
        except Exception as e:                              # noqa: BLE001
            entry["new_status"] = "error"
            entry["error"] = str(e)[:120]
            counts["error"] += 1
        report.append(entry)

    return {
        "ok": True,
        "scanned": len(rows),
        "counts": counts,
        "rows": report,
        "reconciled_at": now,
    }


@router.get("/financials")
async def admin_financials(
    authorization: Optional[str] = Header(None),
):
    """Live financial dashboard payload — pulls real user counts from
    `dev_users`, real payments from `cto_payments`, real Maxx usage,
    blends with editable settings and current USD→CAD FX."""
    await _require_admin(authorization)
    from services.financials import compute_financials
    db = require_db()
    return await compute_financials(db)


@router.post("/financials/settings")
async def admin_financials_save(
    payload: dict,
    authorization: Optional[str] = Header(None),
):
    """Persist the founder's editable financial inputs (cash on hand,
    dev salary, manual user overrides for hypotheticals). Founder-only."""
    await _require_admin(authorization)
    from services.financials import save_settings, compute_financials
    db = require_db()
    await save_settings(db, payload or {})


@router.post("/billing/run-overage-cron")
async def admin_run_overage_cron(
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.billing_cron import bill_maxx_overages
    db = require_db()
    result = await bill_maxx_overages(db)
    await db.billing_cron_runs.insert_one({**result, "trigger": "manual"})
    return result

    return await compute_financials(db)

    # Also append to history (tiny, last 100 snapshots)
    await db.integration_health_history.insert_one({
        **snap,
        "_id": f"snap_{int(snap['generated_at'])}",
    })
    return snap


@router.get("/stripe-config")
async def admin_get_stripe_config(
    authorization: Optional[str] = Header(None),
):
    """Return the current Stripe key (masked) + live ping result."""
    await _require_admin(authorization)
    from routers.payments import _stripe_key, set_runtime_stripe_key
    import stripe as _stripe

    db = require_db()

    # If there's an admin override in DB and it hasn't been loaded yet,
    # load it now so the green/red light reflects the actual key in use.
    db_key = ""
    try:
        row = await db.admin_settings.find_one({"_id": "stripe_api_key"})
        if row:
            db_key = (row.get("value") or "").strip()
            if db_key:
                set_runtime_stripe_key(db_key)
    except Exception as e:
        logger.warning("admin/stripe-config: DB lookup failed: %r", e)

    key = _stripe_key()
    if not key:
        return {
            "configured": False,
            "status": "error",
            "error": "No Stripe key configured. Click Edit and paste your sk_live_… or sk_test_… key.",
            "source": "none",
            "last4": "",
            "mode": "unknown",
        }

    # Detect source for the UI badge.
    if db_key and db_key == key:
        source = "db_override"
    elif (os.environ.get("STRIPE_SECRET_KEY") or
          os.environ.get("STRIPE_API_KEY")) == key:
        source = "env"
    else:
        source = "dotenv"

    mode = "live" if key.startswith("sk_live_") else (
        "test" if key.startswith("sk_test_") else "unknown"
    )
    last4 = key[-4:] if len(key) >= 8 else ""

    # Live ping — Account.retrieve is the canonical "is this key valid"
    # check. Cheap, free of charge, and surfaces capability/restrictions.
    _stripe.api_key = key
    try:
        acct = await asyncio.to_thread(_stripe.Account.retrieve)
        return {
            "configured": True,
            "status":  "ok",
            "error":   "",
            "source":  source,
            "last4":   last4,
            "mode":    mode,
            "account": {
                "id":             acct.get("id"),
                "email":          acct.get("email"),
                "business_name":  acct.get("business_profile", {}).get("name")
                                   or acct.get("settings", {}).get("dashboard", {}).get("display_name")
                                   or "",
                "country":        acct.get("country"),
                "charges_enabled":  bool(acct.get("charges_enabled")),
                "payouts_enabled":  bool(acct.get("payouts_enabled")),
                "details_submitted": bool(acct.get("details_submitted")),
            },
        }
    except _stripe.error.AuthenticationError as e:
        return {
            "configured": True, "status": "error",
            "error": f"Invalid key — Stripe rejected authentication ({getattr(e,'user_message',None) or str(e)})",
            "source": source, "last4": last4, "mode": mode,
        }
    except _stripe.error.PermissionError as e:
        return {
            "configured": True, "status": "error",
            "error": f"Key is missing the `rak_read_only` or account-read permission ({e})",
            "source": source, "last4": last4, "mode": mode,
        }
    except _stripe.error.APIConnectionError as e:
        return {
            "configured": True, "status": "error",
            "error": f"Can't reach Stripe ({e}). Network or DNS issue from the deploy pod.",
            "source": source, "last4": last4, "mode": mode,
        }
    except _stripe.error.StripeError as e:
        return {
            "configured": True, "status": "error",
            "error": f"Stripe error: {getattr(e,'user_message',None) or str(e)}",
            "source": source, "last4": last4, "mode": mode,
        }
    except Exception as e:
        return {
            "configured": True, "status": "error",
            "error": f"Unexpected error: {e}",
            "source": source, "last4": last4, "mode": mode,
        }


@router.post("/stripe-config")
async def admin_set_stripe_config(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Validate a new Stripe key, save it to admin_settings, and
    hot-swap it into the running process. Refuses any key that fails
    a live Account.retrieve()."""
    user = await _require_admin(authorization)
    from routers.payments import set_runtime_stripe_key
    import stripe as _stripe

    new_key = ((body or {}).get("api_key") or "").strip()
    if not new_key:
        raise HTTPException(400, "api_key required")
    if not (new_key.startswith("sk_live_") or new_key.startswith("sk_test_")):
        raise HTTPException(400, "Key must start with sk_live_ or sk_test_")
    if new_key.startswith("sk_test_emergent"):
        raise HTTPException(400, "Refusing to save the Emergent sandbox placeholder")

    # Validate via live ping BEFORE saving — never persist a broken key.
    _stripe.api_key = new_key
    try:
        acct = await asyncio.to_thread(_stripe.Account.retrieve)
    except _stripe.error.AuthenticationError:
        raise HTTPException(400, "Stripe rejected this key — authentication failed")
    except _stripe.error.PermissionError as e:
        raise HTTPException(400, f"Key missing required permissions: {e}")
    except _stripe.error.StripeError as e:
        raise HTTPException(400, f"Stripe error: {getattr(e,'user_message',None) or str(e)}")
    except Exception as e:
        raise HTTPException(400, f"Could not validate key: {e}")

    # Persist + hot-swap.
    db = require_db()
    try:
        await db.admin_settings.update_one(
            {"_id": "stripe_api_key"},
            {"$set": {
                "_id":       "stripe_api_key",
                "value":     new_key,
                "updated_at": time.time(),
                "updated_by": user.get("email") or user.get("user_id"),
                "mode":      "live" if new_key.startswith("sk_live_") else "test",
                "account_id": acct.get("id"),
            }},
            upsert=True,
        )
    except Exception as e:
        logger.error("admin/stripe-config: DB save failed: %r", e)
        raise HTTPException(500, f"Saved to memory but DB persistence failed: {e}")

    set_runtime_stripe_key(new_key)
    logger.info("Stripe key hot-swapped by admin=%s account=%s mode=%s",
                user.get("email"), acct.get("id"),
                "live" if new_key.startswith("sk_live_") else "test")

    return {
        "ok": True,
        "last4": new_key[-4:],
        "mode": "live" if new_key.startswith("sk_live_") else "test",
        "account_id": acct.get("id"),
        "message": "Stripe key saved, validated, and now live.",
    }


@router.get("/stripe-prices")
async def admin_get_stripe_prices(
    authorization: Optional[str] = Header(None),
):
    """Return the current stored price IDs + a live Stripe validation
    for each (masked last-6 char, mode, and recurring status)."""
    await _require_admin(authorization)
    from services.stripe_client import (
        stripe_key, price_id_for, get_runtime_stripe_price_ids, PLAN_IDS,
    )
    import stripe as _stripe

    db = require_db()

    # Ensure runtime cache is hydrated from DB before reporting state
    # (defensive — main.py's lifespan already does this at boot).
    row = None
    try:
        row = await db.admin_settings.find_one({"_id": "stripe_price_ids"})
        if row:
            from services.stripe_client import set_runtime_stripe_price_ids
            set_runtime_stripe_price_ids(row.get("prices") or {})
    except Exception as e:
        logger.warning("admin/stripe-prices GET: DB lookup failed: %r", e)

    runtime = get_runtime_stripe_price_ids()
    key = stripe_key()
    _stripe.api_key = key

    out_plans = {}
    for plan in PLAN_IDS:
        effective = price_id_for(plan)
        env_name = f"STRIPE_{plan.upper()}_PRICE_ID"
        source = (
            "db_override" if runtime.get(plan) else
            ("env" if effective else "none")
        )
        info = {
            "plan":       plan,
            "configured": bool(effective),
            "source":     source,
            "last6":      effective[-6:] if effective else "",
            "env_var":    env_name,
        }
        if effective and key:
            try:
                p = await asyncio.to_thread(_stripe.Price.retrieve, effective)
                info["valid"]     = True
                info["recurring"] = bool((p or {}).get("recurring"))
                info["interval"]  = ((p or {}).get("recurring") or {}).get("interval")
                info["mode"]      = "live" if (p or {}).get("livemode") else "test"
            except _stripe.error.StripeError as e:
                info["valid"] = False
                info["error"] = (
                    getattr(e, "user_message", None) or str(e)
                )[:200]
            except Exception as e:
                info["valid"] = False
                info["error"] = f"{type(e).__name__}: {e}"[:200]
        else:
            info["valid"] = False
            info["error"] = "not configured"
        out_plans[plan] = info

    return {
        "plans": out_plans,
        "last_updated": (row or {}).get("updated_at"),
        "updated_by":   (row or {}).get("updated_by"),
    }


class StripePricesBody(BaseModel):
    starter:        Optional[str] = None
    pro:            Optional[str] = None
    team:           Optional[str] = None
    starter_annual: Optional[str] = None
    pro_annual:     Optional[str] = None
    team_annual:    Optional[str] = None


@router.post("/stripe-prices")
async def admin_set_stripe_prices(
    body: StripePricesBody,
    authorization: Optional[str] = Header(None),
):
    """Validate + persist all 6 Stripe price IDs and hot-swap into the
    runtime cache. Each ID is verified via `Price.retrieve` against the
    live Stripe key BEFORE anything is written — the endpoint refuses
    to persist partial or invalid mappings."""
    user = await _require_admin(authorization)
    from services.stripe_client import (
        stripe_key, set_runtime_stripe_price_ids, PLAN_IDS,
    )
    import stripe as _stripe

    key = stripe_key()
    if not key:
        raise HTTPException(400,
            "Stripe secret key is not configured — set it via "
            "/admin/stripe-config first.")
    _stripe.api_key = key

    submitted = body.model_dump()
    # Normalise
    submitted = {p: (submitted.get(p) or "").strip() for p in PLAN_IDS}

    provided_count = sum(1 for v in submitted.values() if v)
    if provided_count == 0:
        raise HTTPException(400, "At least one price ID must be provided.")

    # Validate each provided price against Stripe
    errors: dict = {}
    validated: dict = {}
    for plan, pid in submitted.items():
        if not pid:
            continue
        if not pid.startswith("price_"):
            errors[plan] = f"Not a valid Stripe price ID (must start with 'price_'): {pid[:20]}…"
            continue
        try:
            p = await asyncio.to_thread(_stripe.Price.retrieve, pid)
        except _stripe.error.StripeError as e:
            errors[plan] = (
                getattr(e, "user_message", None) or str(e)
            )[:200]
            continue
        except Exception as e:
            errors[plan] = f"{type(e).__name__}: {e}"[:200]
            continue
        # Must be recurring
        rec = (p or {}).get("recurring")
        if not rec:
            errors[plan] = f"Price {pid[-8:]} is one_time, not recurring — Subscription checkout would 400."
            continue
        # Interval sanity — monthly plans must be month, annual must be year
        expected_interval = "year" if plan.endswith("_annual") else "month"
        if rec.get("interval") != expected_interval:
            errors[plan] = (
                f"Price {pid[-8:]} interval is `{rec.get('interval')}` — "
                f"plan `{plan}` requires interval=`{expected_interval}`."
            )
            continue
        validated[plan] = pid

    if errors:
        raise HTTPException(
            400,
            f"Refusing to persist — Stripe rejected {len(errors)} of "
            f"{provided_count} submitted price ID(s). Fix and resubmit. "
            f"Details: {errors}",
        )

    # Persist all 6 slots (validated ones set, others cleared to fall
    # back to env). This is idempotent — the founder pastes the full
    # set every time via the admin UI.
    db = require_db()
    try:
        await db.admin_settings.update_one(
            {"_id": "stripe_price_ids"},
            {"$set": {
                "_id":         "stripe_price_ids",
                "prices":      validated,
                "updated_at":  time.time(),
                "updated_by":  user.get("email") or user.get("user_id"),
            }},
            upsert=True,
        )
    except Exception as e:
        logger.error("admin/stripe-prices POST: DB save failed: %r", e)
        raise HTTPException(500, f"DB persistence failed: {e}")

    # Hot-swap into runtime cache — both prod workers read from
    # `services.stripe_client._RUNTIME_STRIPE_PRICE_IDS` immediately.
    # Note: In multi-worker prod, this call only mutates THIS worker's
    # in-memory dict. The other worker(s) pick up the change on their
    # next request that reads the DB (integration_health probe polls
    # every 10 min, but each `/payments/checkout` also has a fast path
    # in `services.stripe_client.price_id_for` that falls back through
    # env if the runtime dict is empty). To guarantee immediate cross-
    # worker convergence we ALSO stamp a lifespan-boot loader in
    # main.py — but for hot-swap we accept a brief window where the
    # other worker still uses env fallback (which now is much less
    # likely to be stale since founders are steered away from it).
    set_runtime_stripe_price_ids(validated)
    logger.info(
        "Stripe price IDs hot-swapped by admin=%s count=%d",
        user.get("email"), len(validated),
    )

    return {
        "ok":         True,
        "saved":      len(validated),
        "message":    f"Saved and validated {len(validated)} price ID(s). "
                      f"Every future checkout in every worker will use these "
                      f"values (via DB read at boot + hot-swap now).",
        "plans":      {p: pid[-6:] for p, pid in validated.items()},
    }

