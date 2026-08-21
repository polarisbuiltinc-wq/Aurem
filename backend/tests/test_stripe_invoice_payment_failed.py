"""test_stripe_invoice_payment_failed.py — 2026-08-22

Founder billing-gap ask: Stripe already sends `invoice.payment_failed`
to our webhook (confirmed live against the real Stripe account's
webhook config), but nothing reacted to it. Verifies the new handling
in routers/payments.py:

  1. `invoice.payment_failed` flags the user (`payment_failed=True`,
     `payment_failure_count` incremented) so the frontend can show the
     "update your card" banner, fires ONE founder alert (via the
     existing G10 `services.founder_alerts.send_founder_alert`), and
     fires ONE customer-facing recovery email
     (`services.payment_recovery_email.send_payment_recovery_email`,
     with a freshly-generated Stripe portal link).
  2. `invoice.paid` on a previously-flagged subscription clears the
     flag (recovery path — a retry succeeded).
  3. GET /payments/my-plan surfaces `payment_failed` so the frontend
     banner has something to poll.
  4. `send_payment_recovery_email` itself dedupes per Stripe invoice
     id — calling it twice for the SAME invoice only sends once.
  5. 2026-08-22 follow-up — a real recovery (sub was flagged
     payment_failed=True) fires a "you're all set" confirmation email
     to the customer via `send_payment_recovered_email`; a normal
     first-try renewal (never flagged) does NOT.

Bypasses real Stripe signature verification (monkeypatches
`stripe.Webhook.construct_event`) — this is a pure webhook-payload
shape test, not a live-Stripe integration test. Wires the DB handle
directly (bypassing the app's full startup lifespan, which spins up
crons/index-setup that don't belong in a unit test) and drives the
ASGI app in-process via httpx so seeding + assertions share one event
loop with the request.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

import routers.payments as payments_mod
from main import app
from cto_services.db import set_db, get_db


def _fake_event(etype: str, obj: dict) -> dict:
    return {"type": etype, "data": {"object": obj}}


def _test_user_id() -> str:
    return f"test-user-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _wire_db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    set_db(client[os.environ["DB_NAME"]])
    yield
    client.close()


async def _seed_user(user_id: str, sub_id: str, tier: str = "pro", payment_failed: bool = False):
    db = get_db()
    await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id, "email": f"{user_id}@example.com",
            "tier": tier, "stripe_sub_id": sub_id,
            "payment_failed": payment_failed,
        }},
        upsert=True,
    )


async def _cleanup_user(user_id: str):
    db = get_db()
    await db.dev_users.delete_one({"user_id": user_id})


@pytest.mark.asyncio
async def test_invoice_payment_failed_flags_user(monkeypatch):
    user_id = _test_user_id()
    sub_id = f"sub_test_{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(
        payments_mod.stripe.Webhook, "construct_event",
        staticmethod(lambda payload, sig, secret: _fake_event("invoice.payment_failed", {
            "id": "in_test_1", "subscription": sub_id, "customer": "cus_test",
            "attempt_count": 1, "next_payment_attempt": time.time() + 86400,
            "amount_due": 1900,
        })),
    )
    # Never actually hit Resend during a test run — just assert it was called.
    alert_calls = []

    async def _fake_alert(db, **kwargs):
        alert_calls.append(kwargs)
        return {"sent": False, "reason": "test_stub"}
    monkeypatch.setattr("services.founder_alerts.send_founder_alert", _fake_alert)

    # Never hit real Stripe (portal session) or real Resend (customer
    # email) during a test run — just capture the calls.
    class _FakePortalSession:
        url = "https://billing.stripe.com/p/session/test_fake"
    monkeypatch.setattr(
        payments_mod.stripe.billing_portal.Session, "create",
        staticmethod(lambda **kw: _FakePortalSession()),
    )
    recovery_calls = []

    async def _fake_recovery_email(db, user, **kwargs):
        recovery_calls.append({"user": user, **kwargs})
        return {"ok": True}
    monkeypatch.setattr(
        "services.payment_recovery_email.send_payment_recovery_email",
        _fake_recovery_email,
    )

    await _seed_user(user_id, sub_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/aurem-dev/payments/webhook", json={})
    assert r.status_code == 200, r.text

    row = await get_db().dev_users.find_one({"user_id": user_id})
    assert row.get("payment_failed") is True
    assert row.get("payment_failure_count") == 1
    assert len(alert_calls) == 1, "founder alert must fire exactly once"
    assert alert_calls[0]["guard"] == "stripe_dunning"
    assert len(recovery_calls) == 1, "customer recovery email must fire exactly once"
    assert recovery_calls[0]["invoice_id"] == "in_test_1"
    assert recovery_calls[0]["portal_url"] == "https://billing.stripe.com/p/session/test_fake"
    assert recovery_calls[0]["amount_due"] == 19.0
    assert recovery_calls[0]["next_attempt_at"] is not None, (
        "grace-period date must be threaded through from Stripe's next_payment_attempt"
    )
    await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_invoice_paid_clears_flag(monkeypatch):
    user_id = _test_user_id()
    sub_id = f"sub_test_{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(
        payments_mod.stripe.Webhook, "construct_event",
        staticmethod(lambda payload, sig, secret: _fake_event("invoice.paid", {
            "id": "in_test_2", "subscription": sub_id, "customer": "cus_test",
        })),
    )
    await _seed_user(user_id, sub_id, payment_failed=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/aurem-dev/payments/webhook", json={})
    assert r.status_code == 200, r.text

    row = await get_db().dev_users.find_one({"user_id": user_id})
    assert row.get("payment_failed") is False
    await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_invoice_paid_sends_recovery_confirmation_when_previously_failed(monkeypatch):
    """2026-08-22 — founder follow-up: close the loop with a 'you're
    all set' email, but ONLY when the sub was actually flagged
    payment_failed=True beforehand (a real recovery)."""
    user_id = _test_user_id()
    sub_id = f"sub_test_{uuid.uuid4().hex[:10]}"
    invoice_id = f"in_recovered_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(
        payments_mod.stripe.Webhook, "construct_event",
        staticmethod(lambda payload, sig, secret: _fake_event("invoice.paid", {
            "id": invoice_id, "subscription": sub_id, "customer": "cus_test",
        })),
    )
    recovered_calls = []

    async def _fake_recovered_email(db, user, **kwargs):
        recovered_calls.append({"user": user, **kwargs})
        return {"ok": True}
    monkeypatch.setattr(
        "services.payment_recovery_email.send_payment_recovered_email",
        _fake_recovered_email,
    )

    await _seed_user(user_id, sub_id, payment_failed=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/aurem-dev/payments/webhook", json={})
    assert r.status_code == 200, r.text

    assert len(recovered_calls) == 1, "recovery confirmation email must fire exactly once"
    assert recovered_calls[0]["invoice_id"] == invoice_id
    row = await get_db().dev_users.find_one({"user_id": user_id})
    assert row.get("payment_failed") is False
    await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_invoice_paid_no_confirmation_email_for_normal_renewal(monkeypatch):
    """A normal renewal that succeeds on the FIRST try (never flagged
    payment_failed) must never trigger the recovery-confirmation email
    — nothing was ever wrong for this customer."""
    user_id = _test_user_id()
    sub_id = f"sub_test_{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(
        payments_mod.stripe.Webhook, "construct_event",
        staticmethod(lambda payload, sig, secret: _fake_event("invoice.paid", {
            "id": f"in_normal_{uuid.uuid4().hex[:8]}", "subscription": sub_id,
            "customer": "cus_test",
        })),
    )
    recovered_calls = []

    async def _fake_recovered_email(db, user, **kwargs):
        recovered_calls.append({"user": user, **kwargs})
        return {"ok": True}
    monkeypatch.setattr(
        "services.payment_recovery_email.send_payment_recovered_email",
        _fake_recovered_email,
    )

    await _seed_user(user_id, sub_id, payment_failed=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/aurem-dev/payments/webhook", json={})
    assert r.status_code == 200, r.text

    assert len(recovered_calls) == 0, "must not send the recovery email for a normal first-try renewal"
    await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_invoice_payment_failed_missing_subscription_does_not_crash(monkeypatch):
    monkeypatch.setattr(
        payments_mod.stripe.Webhook, "construct_event",
        staticmethod(lambda payload, sig, secret: _fake_event("invoice.payment_failed", {
            "id": "in_test_3", "subscription": None, "customer": "cus_test",
        })),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/aurem-dev/payments/webhook", json={})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_my_plan_surfaces_payment_failed(monkeypatch):
    user_id = _test_user_id()
    sub_id = f"sub_test_{uuid.uuid4().hex[:10]}"

    async def _fake_current_dev(authorization=None):
        return {"user_id": user_id, "email": f"{user_id}@example.com"}
    monkeypatch.setattr(payments_mod, "current_dev", _fake_current_dev)

    await _seed_user(user_id, sub_id, payment_failed=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/aurem-dev/payments/my-plan", headers={"Authorization": "Bearer fake"})
    assert r.status_code == 200, r.text
    assert r.json().get("payment_failed") is True
    await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_payment_recovery_email_dedups_per_invoice(monkeypatch):
    """Direct unit test of services/payment_recovery_email.py, not via
    the webhook — calling send_payment_recovery_email TWICE for the
    SAME invoice_id must only actually send (hit Resend) once."""
    from services.payment_recovery_email import send_payment_recovery_email

    send_calls = []

    async def _fake_resend_send(to_email, *, subject, text, html):
        send_calls.append(to_email)
        return True, None
    monkeypatch.setattr(
        "services.payment_recovery_email._resend_send", _fake_resend_send,
    )

    user = {"user_id": "u_dedup_test", "email": "dedup-test@example.com"}
    invoice_id = f"in_dedup_{uuid.uuid4().hex[:8]}"
    db = get_db()

    r1 = await send_payment_recovery_email(
        db, user, invoice_id=invoice_id, plan="pro", amount_due=19.0,
        portal_url="https://billing.stripe.com/p/session/test",
    )
    r2 = await send_payment_recovery_email(
        db, user, invoice_id=invoice_id, plan="pro", amount_due=19.0,
        portal_url="https://billing.stripe.com/p/session/test",
    )
    assert r1.get("ok") is True
    assert r2 == {"ok": True, "skipped": "already_sent"}
    assert len(send_calls) == 1, "must only actually send once for the same invoice_id"

    await db.payment_recovery_emails.delete_many({"invoice_id": invoice_id})


@pytest.mark.asyncio
async def test_payment_recovered_email_dedups_per_invoice(monkeypatch):
    """Direct unit test of send_payment_recovered_email's own dedup —
    same invoice_id, called twice, must only actually send once."""
    from services.payment_recovery_email import send_payment_recovered_email

    send_calls = []

    async def _fake_resend_send(to_email, *, subject, text, html):
        send_calls.append((to_email, subject))
        return True, None
    monkeypatch.setattr(
        "services.payment_recovery_email._resend_send", _fake_resend_send,
    )

    user = {"user_id": "u_recovered_dedup_test", "email": "recovered-dedup-test@example.com"}
    invoice_id = f"in_recovered_dedup_{uuid.uuid4().hex[:8]}"
    db = get_db()

    r1 = await send_payment_recovered_email(db, user, invoice_id=invoice_id, plan="pro")
    r2 = await send_payment_recovered_email(db, user, invoice_id=invoice_id, plan="pro")
    assert r1.get("ok") is True
    assert r2 == {"ok": True, "skipped": "already_sent"}
    assert len(send_calls) == 1, "must only actually send once for the same invoice_id"
    assert "all set" in send_calls[0][1].lower()

    await db.payment_recovered_emails.delete_many({"invoice_id": invoice_id})


def test_grace_period_line_with_future_next_attempt():
    """2026-08-22 — founder ask: an exact, verifiable deadline, not a
    vague 'couple of weeks'. Uses the real next_payment_attempt date
    Stripe gives us for this specific invoice."""
    import datetime as dt
    from services.payment_recovery_email import _grace_period_line

    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=5)
    line = _grace_period_line(future)
    assert "5 days" in line
    assert "Stripe will automatically try again" in line


def test_grace_period_line_with_no_next_attempt():
    """When Stripe reports no further scheduled retry (final attempt
    already exhausted), the copy must say so plainly instead of
    quoting a stale/future date."""
    from services.payment_recovery_email import _grace_period_line

    line = _grace_period_line(None)
    assert "last scheduled automatic retry" in line


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
