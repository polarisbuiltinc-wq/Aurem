"""
services/payment_reconciliation.py — G7 · Hourly Stripe vs DB reconcile

Runs from the existing 60-min housekeeping tick (main.py). Compares
Stripe API subscription + payment_intent state against local
`payments` / `subscriptions` collections. Flags:

  - Local `pending` payments older than 24h with no Stripe match
  - Stripe `succeeded` PaymentIntents that never landed as `paid`
    rows locally (money-in-but-not-credited)
  - Stripe `active` subscriptions whose local row is `canceled`
    (billing user without service)

Any finding → `payment_reconciliation_log` row + G10 critical alert.

Env:
  STRIPE_API_KEY   (required — code is silent no-op if unset)
  RECONCILE_LOOKBACK_HOURS  default 168 (7 days)
"""
from __future__ import annotations

import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List

logger = logging.getLogger("aurem.payment_reconciliation")


def _lookback() -> timedelta:
    try:
        return timedelta(hours=int(os.environ.get(
            "RECONCILE_LOOKBACK_HOURS", "168")))
    except (ValueError, TypeError):
        return timedelta(hours=168)


async def run_reconciliation(db) -> dict:
    """Return a summary dict; also persists a `payment_reconciliation_log`
    row and fires G10 alert if findings > 0."""
    if db is None:
        return {"ok": False, "reason": "no_db"}
    api_key = os.environ.get("STRIPE_API_KEY", "")
    if not api_key:
        return {"ok": False, "reason": "stripe_key_missing"}

    try:
        import stripe                                # type: ignore
    except ImportError:
        return {"ok": False, "reason": "stripe_sdk_missing"}

    stripe.api_key = api_key
    since = datetime.now(timezone.utc) - _lookback()
    since_epoch = int(since.timestamp())

    findings: List[dict] = []
    checked_pi = 0
    checked_sub = 0

    # (1) Local pending payments > 24h.
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        async for p in db.payments.find(
            {"status": "pending", "created_at": {"$lt": cutoff}},
            {"_id": 0, "payment_id": 1, "user_id": 1, "amount": 1,
             "stripe_id": 1, "created_at": 1},
        ):
            findings.append({
                "kind":       "local_pending_stuck",
                "payment_id": p.get("payment_id"),
                "user_id":    p.get("user_id"),
                "amount":     p.get("amount"),
                "age_hours":  round(
                    (datetime.now(timezone.utc) - p.get(
                        "created_at", datetime.now(timezone.utc))
                    ).total_seconds() / 3600, 1),
            })
    except Exception as e:
        logger.warning("[G7] local pending sweep failed: %r", e)

    # 2026-08-20 · prod-hang fix — `stripe.PaymentIntent.list(...)` /
    # `stripe.Subscription.list(...)` and their `.auto_paging_iter()`
    # are SYNCHRONOUS network calls (each page = a blocking HTTP
    # round-trip to Stripe). Called bare inside this `async def` they
    # freeze the entire event loop for the duration — same class of
    # bug already fixed for G18/G21/CI-drift. This is the real root
    # cause of the recurring "/admin/status/all timed out after 15s"
    # + "Business Pulse timeout of 12000ms" hangs: this cron fires
    # hourly and stalls every other in-flight request while it runs.
    # Fetch off-loop via asyncio.to_thread, then do the async Mongo
    # comparison against plain in-memory lists (no Stripe calls left
    # inside the loop).

    def _fetch_pis() -> list:
        pis = stripe.PaymentIntent.list(created={"gte": since_epoch}, limit=100)
        out = []
        for pi in pis.auto_paging_iter():
            out.append(pi)
            # Cap the scan so a slow/huge Stripe response can't hang the tick.
            if len(out) >= 500:
                break
        return out

    def _fetch_active_subs() -> list:
        subs = stripe.Subscription.list(status="active", limit=100)
        out = []
        for s in subs.auto_paging_iter():
            out.append(s)
            if len(out) >= 500:
                break
        return out

    # (2) Stripe succeeded PIs that never landed locally as "paid".
    try:
        pis = await asyncio.to_thread(_fetch_pis)
        for pi in pis:
            checked_pi += 1
            if pi.status != "succeeded":
                continue
            local = await db.payments.find_one(
                {"stripe_id": pi.id},
                {"_id": 0, "status": 1},
            )
            if not local:
                findings.append({
                    "kind":       "stripe_paid_missing_locally",
                    "pi_id":      pi.id,
                    "amount":     pi.amount,
                    "customer":   pi.customer,
                })
            elif local.get("status") != "paid":
                findings.append({
                    "kind":       "local_status_mismatch",
                    "pi_id":      pi.id,
                    "local_status": local.get("status"),
                    "stripe_status": "succeeded",
                })
    except Exception as e:
        logger.warning("[G7] Stripe PI sweep failed: %r", e)

    # (3) Stripe active subs whose local row is canceled.
    try:
        subs = await asyncio.to_thread(_fetch_active_subs)
        for s in subs:
            checked_sub += 1
            local = await db.subscriptions.find_one(
                {"stripe_subscription_id": s.id},
                {"_id": 0, "status": 1, "user_id": 1},
            )
            if local and local.get("status") == "canceled":
                findings.append({
                    "kind":       "stripe_active_local_canceled",
                    "sub_id":     s.id,
                    "user_id":    local.get("user_id"),
                })
    except Exception as e:
        logger.warning("[G7] Stripe sub sweep failed: %r", e)

    summary = {
        "ok":               True,
        "checked_at":       datetime.now(timezone.utc),
        "checked_pi":       checked_pi,
        "checked_sub":      checked_sub,
        "findings":         findings,
        "finding_count":    len(findings),
    }

    # Persist audit row (best-effort).
    try:
        await db.payment_reconciliation_log.insert_one({
            **summary, "created_at": summary["checked_at"],
        })
    except Exception:
        pass

    if findings:
        try:
            from services.founder_alerts import send_founder_alert
            title = f"Payment reconciliation: {len(findings)} discrepancy(ies)"
            detail_lines = [
                f"- {f['kind']}: {f.get('pi_id') or f.get('sub_id') or f.get('payment_id')}"
                for f in findings[:20]
            ]
            await send_founder_alert(
                db,
                source_key="payment_reconciliation",
                title=title,
                detail="\n".join(detail_lines),
                level="critical", guard="G7",
            )
        except Exception:
            pass
    return summary


async def get_recon_summary(db) -> dict:
    """QA panel snapshot — last run + open finding count."""
    if db is None:
        return {"available": False}
    try:
        last = await db.payment_reconciliation_log.find_one(
            {}, sort=[("created_at", -1)],
        )
        if not last:
            return {"available": True, "last_run": None}
        return {
            "available":    True,
            "last_run":     last.get("created_at").isoformat()
                              if last.get("created_at") else None,
            "checked_pi":   last.get("checked_pi"),
            "checked_sub":  last.get("checked_sub"),
            "findings":     last.get("finding_count"),
        }
    except Exception as e:
        return {"available": False, "error": str(e)[:200]}


_RECON_INTERVAL_S = 3600  # hourly, matches the module docstring's promise


async def schedule_payment_reconciliation(db_getter) -> None:
    """2026-08-19 · guards-audit fix — `run_reconciliation()` was fully
    built (and STRIPE_API_KEY has been set the whole time) but was
    NEVER actually called anywhere, so G7 sat gray forever. Same
    scheduler shape as `schedule_integration_health_cron` — kicked off
    from main.py startup, sleeps `_RECON_INTERVAL_S` between runs.
    `db_getter` is a zero-arg callable (e.g. `lambda: app.state.db`)
    so this always reads the live db handle, not one captured at
    startup before Mongo connects."""
    import asyncio
    await asyncio.sleep(120)  # let the app finish booting first
    while True:
        try:
            db = db_getter()
            if db is not None:
                summary = await run_reconciliation(db)
                if summary.get("ok"):
                    logger.info(
                        "[G7] reconciliation ok — %d PI, %d sub checked, "
                        "%d finding(s)",
                        summary.get("checked_pi", 0),
                        summary.get("checked_sub", 0),
                        summary.get("finding_count", 0),
                    )
                else:
                    logger.info("[G7] reconciliation skipped: %s",
                                summary.get("reason"))
        except asyncio.CancelledError:
            raise
        except Exception as e:                                # noqa: BLE001
            logger.warning("[G7] reconciliation tick failed: %r", e)
        await asyncio.sleep(_RECON_INTERVAL_S)
