"""stripe_webhook_compat.py — Iter 388-ae (2026-02-14).

Legacy-path alias for the Stripe webhook endpoint.

Root-cause context (the "Payments $0 mystery"):
- Our actual Stripe webhook endpoint lives at
  `/api/aurem-dev/payments/webhook` (see `routers/payments.py`).
- The Stripe dashboard for `auremcto.com` was configured at some
  point with `/api/stripe/webhook` (no `aurem-dev` prefix).
- Result: every real Stripe payment webhook returned `404 Not Found`
  in production logs (see 2026-02-14 prod tail — repeated ~7 times
  in a single minute).
- Consequence: `cto_payments.payment_status` never transitioned from
  `"pending"` to `"paid"`, so admin/analytics reported $0 revenue
  despite 68 sessions in the ledger.

This router exposes a tiny alias at `/stripe/webhook` (mounted under
the app's `/api` prefix in `main.py`, so the final URL is
`/api/stripe/webhook`) that simply delegates to the real webhook
handler in `routers/payments.py::stripe_webhook`. Same signature
verification, same DB writes, same idempotency — no divergence.

Alternative would have been to update the Stripe dashboard endpoint
URL to `/api/aurem-dev/payments/webhook`, but:
  1. That requires manual founder action + rotating the signing
     secret if the dashboard "Reveal" step is used.
  2. Any legacy integration that references the old URL breaks.

This compat shim is idempotent, side-effect free, and closes the
issue at the code layer.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from routers.payments import stripe_webhook

router = APIRouter()


@router.post("/stripe/webhook")
async def stripe_webhook_compat(request: Request) -> dict:
    """Delegate to the canonical Stripe webhook handler.

    We import + call the real handler rather than duplicating logic —
    signature verification, event routing, cto_payments transitions,
    dev_users upgrades, and alert wiring stay in one place.
    """
    return await stripe_webhook(request)
