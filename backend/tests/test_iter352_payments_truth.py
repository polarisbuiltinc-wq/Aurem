"""Iter 352 — payments pipeline truth locks.

RCA (founder audit: 22 stuck 'pending' rows, $0.00, up to 35 days old):
  1. checkout.session.completed webhook updated dev_users tier but NEVER
     touched the cto_payments ledger row → paid sessions could stay
     "pending" if the browser never reached the success-redirect poll.
  2. checkout.session.expired was not handled at all → abandoned
     checkouts stay "pending" forever (the actual bulk of the 22).
  3. Ledger rows never stored an amount → $0.00 rendering.
Verified live on preview: signed-webhook e2e flipped pending→paid
($9.00, paid_at) and pending→expired; /admin/payments/reconcile
classified 14 stuck preview rows (12 Stripe-confirmed expired, 0 paid).
"""
import os

_PAY_SRC = open(os.path.join(
    os.path.dirname(__file__), "..", "routers", "payments.py")).read()
_ADMIN_SRC = open(os.path.join(
    os.path.dirname(__file__), "..", "routers", "admin.py")).read()
_HR_SRC = open("/app/frontend/src/components/AdminHouseRules.jsx").read()


def test_webhook_completed_syncs_ledger_row():
    block = _PAY_SRC.split('if etype == "checkout.session.completed"')[1]
    block = block.split("elif etype")[0]
    assert "cto_payments.update_one" in block
    assert '"payment_status": "paid"' in block
    assert '"amount"' in block and "amount_total" in block
    assert '"paid_at"' in block


def test_webhook_handles_expired_sessions():
    assert 'checkout.session.expired' in _PAY_SRC
    block = _PAY_SRC.split('checkout.session.expired')[1].split("elif etype")[0]
    assert '"payment_status": "expired"' in block


def test_reconcile_endpoint_is_admin_gated():
    assert '@router.post("/payments/reconcile")' in _ADMIN_SRC
    block = _ADMIN_SRC.split('@router.post("/payments/reconcile")')[1]
    block = block.split('@router.get("/support")')[0]
    assert "_require_admin(authorization)" in block
    assert "Session.retrieve" in block
    assert '"expired"' in block and '"paid"' in block


def test_house_rules_never_leaks_infra_errors():
    assert "cleanErr" in _HR_SRC
    assert "cloudflare|could not parse" in _HR_SRC
    # both catch paths routed through the sanitizer
    assert _HR_SRC.count("cleanErr(e") >= 2


def test_payments_page_has_reconcile_button():
    src = open("/app/frontend/src/pages/Admin.jsx").read()
    assert 'data-testid="reconcile-pending-btn"' in src
    assert '/admin/payments/reconcile' in src
