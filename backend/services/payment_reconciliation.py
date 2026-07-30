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

    # (2) Stripe succeeded PIs that never landed locally as "paid".
    try:
        pis = stripe.PaymentIntent.list(
            created={"gte": since_epoch}, limit=100,
        )
        for pi in pis.auto_paging_iter():
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
            # Cap the scan so a slow Stripe response can't hang the tick.
            if checked_pi >= 500:
                break
    except Exception as e:
        logger.warning("[G7] Stripe PI sweep failed: %r", e)

    # (3) Stripe active subs whose local row is canceled.
    try:
        subs = stripe.Subscription.list(status="active", limit=100)
        for s in subs.auto_paging_iter():
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
            if checked_sub >= 500:
                break
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
