"""
tests/test_r6_llm_usd_cap.py — R6 (2026-08-28), per-plan USD cap for
the ORA v2 (DashScope/Qwen) LLM client.

Named tests:
  t_usd_rate_table_used          — a known token count x known rate
                                    = the logged USD, exactly.
  t_usd_cap_blocks_over           — over the per-plan cap -> human
                                    message, zero tokens spent after.
  t_usd_free_user_precall_block   — Free tier, MOCK off -> blocked
                                    BEFORE the provider is ever called.
  t_usd_backfill_idempotent       — running the backfill twice writes
                                    the same totals (no double count).
  t_usd_secondary_caps_kept       — services.usage's existing
                                    token/task caps are untouched by
                                    this change and still fire.
"""
import os
import time
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from services import llm_rate_table, llm_usd_cap

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[DB_NAME]
    from cto_services.db import set_db
    set_db(database)  # services/usage.py reads the global via require_db()
    yield database
    client.close()


@pytest_asyncio.fixture
async def test_user(db):
    """A throwaway Free-tier user + a clean ledger slice for it."""
    uid = f"r6-test-{uuid.uuid4().hex[:10]}"
    await db.dev_users.insert_one({"user_id": uid, "tier": "free", "email": f"{uid}@test.local"})
    yield uid
    await db.dev_users.delete_one({"user_id": uid})
    await db[llm_usd_cap.LEDGER_COLLECTION].delete_many({"user_id": uid})
    await db.guardrail_events.delete_many({"user_id": uid})


def test_usd_rate_table_used():
    rates = {"qwen3.8-27b": {"input_per_m": 0.425, "output_per_m": 2.55}}
    cost = llm_rate_table.cost_usd(rates, "qwen3.8-27b", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(0.425 + 2.55, abs=1e-9)
    # A tiny, real-shaped turn — exact arithmetic, not just "some positive number".
    cost2 = llm_rate_table.cost_usd(rates, "qwen3.8-27b", input_tokens=2_000, output_tokens=500)
    assert cost2 == pytest.approx((2000 / 1_000_000) * 0.425 + (500 / 1_000_000) * 2.55, abs=1e-9)


@pytest.mark.asyncio
async def test_usd_cap_blocks_over(db, test_user):
    # Set a tiny, deterministic per-plan cap for "free" so the test is
    # exact regardless of the live default value.
    await llm_usd_cap.set_usd_caps(db, per_plan={"free": 0.01})
    # Already-spent this month == the cap itself -> the NEXT call
    # (any positive estimate) must be blocked before it happens.
    await llm_usd_cap.record_usd_spend(
        db, user_id=test_user, model="qwen3.8-27b", input_tokens=10_000, output_tokens=1_000,
        cost_usd=0.01)
    with pytest.raises(llm_usd_cap.LLMUsdCapExceeded) as exc:
        await llm_usd_cap.assert_within_usd_cap(db, user_id=test_user, est_cost_usd=0.001)
    assert exc.value.message == "Monthly limit reached — upgrade to continue."
    assert exc.value.cap_kind == "per_plan"
    # Zero tokens spent AFTER the block — ledger unchanged (only the
    # one row we inserted ourselves above).
    rows = await db[llm_usd_cap.LEDGER_COLLECTION].count_documents({"user_id": test_user})
    assert rows == 1
    # Reset the global default back so other tests aren't affected.
    await llm_usd_cap.set_usd_caps(db, per_plan=dict(llm_usd_cap.DEFAULT_PER_PLAN_CAPS_USD))


@pytest.mark.asyncio
async def test_usd_free_user_precall_block(db, test_user, monkeypatch):
    """End-to-end through the real llm_client.stream_chat() choke
    point: MOCK off, a Free user already over cap -> the provider
    (OpenAI client) must never be constructed/called."""
    from services.ora_chat_v2 import llm_client

    monkeypatch.setattr(llm_client, "is_mock", lambda: False)

    async def _fake_resolve(_db, role):
        return {"base_url": "https://example.invalid", "api_key": "sk-test",
                "model": "qwen3.8-27b", "label": "test", "source": "env"}
    monkeypatch.setattr(llm_client, "_resolve", _fake_resolve)
    await llm_usd_cap.set_usd_caps(db, per_plan={"free": 0.001})
    await llm_usd_cap.record_usd_spend(
        db, user_id=test_user, model="qwen3.8-27b", input_tokens=5_000, output_tokens=500,
        cost_usd=0.001)

    called = {"n": 0}
    class _BoomClient:
        def __init__(self, *a, **kw):
            called["n"] += 1
        class chat:
            class completions:
                @staticmethod
                async def create(*a, **kw):
                    raise AssertionError("provider must never be called past the USD cap")
    monkeypatch.setattr("openai.AsyncOpenAI", _BoomClient)

    events = []
    async for evt in llm_client.stream_chat(
            messages=[{"role": "user", "content": "hi"}], db=db, user_id=test_user):
        events.append(evt)

    assert called["n"] == 0, "provider client must never be constructed past the cap"
    assert any(e["type"] == "error" and e.get("error") == "monthly_limit_reached" for e in events)
    err = next(e for e in events if e["type"] == "error")
    assert err["detail"] == "Monthly limit reached — upgrade to continue."
    await llm_usd_cap.set_usd_caps(db, per_plan=dict(llm_usd_cap.DEFAULT_PER_PLAN_CAPS_USD))


@pytest.mark.asyncio
async def test_usd_backfill_idempotent(db, test_user):
    now = time.time()
    row = await db.ora_chat_usage.insert_one({
        "ts": now, "admin_id": test_user, "session_id": "s1",
        "tokens_in": 4_000, "tokens_out": 1_000, "model": "qwen3.8-27b",
        "config_label": "test",
    })
    try:
        r1 = await llm_usd_cap.backfill_current_month_from_usage_log(db, dry_run=False)
        assert r1["ok"] is True
        spend_after_1 = await llm_usd_cap.month_spend_usd(db, user_id=test_user)
        assert spend_after_1 > 0

        r2 = await llm_usd_cap.backfill_current_month_from_usage_log(db, dry_run=False)
        assert r2["ok"] is True
        spend_after_2 = await llm_usd_cap.month_spend_usd(db, user_id=test_user)

        assert spend_after_2 == pytest.approx(spend_after_1, abs=1e-9)
        ledger_rows = await db[llm_usd_cap.LEDGER_COLLECTION].count_documents(
            {"backfilled_from": str(row.inserted_id)})
        assert ledger_rows == 1  # upsert, not duplicate insert
    finally:
        await db.ora_chat_usage.delete_one({"_id": row.inserted_id})
        await db[llm_usd_cap.LEDGER_COLLECTION].delete_many({"backfilled_from": str(row.inserted_id)})


@pytest.mark.asyncio
async def test_usd_secondary_caps_kept(db, test_user):
    """R6 layers a NEW dollar cap on TOP of the existing token/task
    caps in services/usage.py for the OTHER (non-Qwen) chat path —
    it must not have touched or disabled them."""
    from services import usage
    task = await db.cto_tasks.insert_one({
        "user_id": test_user, "status": "done", "tokens_used": 999_999_999,
    })
    try:
        with pytest.raises(Exception) as exc:
            await usage.assert_has_budget(test_user)
        assert getattr(exc.value, "status_code", None) == 402
    finally:
        await db.cto_tasks.delete_one({"_id": task.inserted_id})
