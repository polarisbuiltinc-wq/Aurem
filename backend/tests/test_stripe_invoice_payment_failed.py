"""test_stripe_invoice_payment_failed.py — 2026-08-22

Founder billing-gap ask: Stripe already sends `invoice.payment_failed`
to our webhook (confirmed live against the real Stripe account's
webhook config), but nothing reacted to it. Verifies the new handling
in routers/payments.py:

  1. `invoice.payment_failed` flags the user (`payment_failed=True`,
     `payment_failure_count` incremented) so the frontend can show the
     "update your card" banner, and fires ONE founder alert (via the
     existing G10 `services.founder_alerts.send_founder_alert`).
  2. `invoice.paid` on a previously-flagged subscription clears the
     flag (recovery path — a retry succeeded).
  3. GET /payments/my-plan surfaces `payment_failed` so the frontend
     banner has something to poll.

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

    await _seed_user(user_id, sub_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/aurem-dev/payments/webhook", json={})
    assert r.status_code == 200, r.text

    row = await get_db().dev_users.find_one({"user_id": user_id})
    assert row.get("payment_failed") is True
    assert row.get("payment_failure_count") == 1
    assert len(alert_calls) == 1, "founder alert must fire exactly once"
    assert alert_calls[0]["guard"] == "stripe_dunning"
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
