"""
tests/test_iter_hardening2_real_customer_filter.py — Production
Hardening Fix 1 (2026-08, Task 2 cost-audit follow-up).

Task 2's cost audit found 95%+ of `customer_chat_cost` was
`test_admin_001` (the founder's own admin/QA account), not real
customers. `services/customer_cost_tracker.py::real_customer_match_stages`
fixes this by joining to `dev_users` and excluding founder/admin
accounts + orphaned test/canary IDs that were never signed up —
with NO hardcoded literal-string list to rot.

Uses the real configured Mongo (same pattern as
tests/test_fabrication_learning_loop.py) with uniquely-tagged rows,
cleaned up after each test.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

pytestmark = pytest.mark.asyncio


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


@pytest.fixture
async def db():
    d = _db()
    yield d
    await d.dev_users.delete_many({"_test_run": True})
    await d.customer_chat_cost.delete_many({"_test_run": True})


async def _mk_user(db, user_id, tier, is_admin=False):
    await db.dev_users.insert_one({
        "user_id": user_id, "email": f"{user_id}@example.com",
        "tier": tier, "is_admin": is_admin, "created_at": time.time(),
        "_test_run": True,
    })


async def _mk_cost_row(db, user_id, model, cost_usd):
    await db.customer_chat_cost.insert_one({
        "user_id": user_id, "session_id": "s", "project_id": None,
        "route": "chat_send", "model": model,
        "input_tokens": 10, "output_tokens": 10,
        "cost_usd": cost_usd, "ts": time.time(),
        "_test_run": True,
    })


# ─────────────────────────────────────────────────────────────
# T-F1a — mixed set (test + real) -> customers-only excludes test
# ─────────────────────────────────────────────────────────────
async def test_f1a_real_customer_filter_excludes_test_and_orphaned(db):
    from services.customer_cost_tracker import real_customer_match_stages

    tag = uuid.uuid4().hex[:8]
    real_uid = f"real-{tag}"
    founder_uid = f"founder-{tag}"
    admin_uid = f"admin-{tag}"
    orphan_uid = f"canary-{tag}"  # never signed up -> no dev_users row

    await _mk_user(db, real_uid, tier="pro")
    await _mk_user(db, founder_uid, tier="founder")
    await _mk_user(db, admin_uid, tier="pro", is_admin=True)

    await _mk_cost_row(db, real_uid, "z-ai/glm-5.2", 1.00)
    await _mk_cost_row(db, founder_uid, "z-ai/glm-5.2", 2.00)
    await _mk_cost_row(db, admin_uid, "z-ai/glm-5.2", 4.00)
    await _mk_cost_row(db, orphan_uid, "z-ai/glm-5.2", 8.00)

    match_tag = {"$match": {"_test_run": True}}
    total_pipe = [match_tag, {"$group": {"_id": None, "sum": {"$sum": "$cost_usd"}}}]
    customers_pipe = (
        [match_tag] + real_customer_match_stages()
        + [{"$group": {"_id": None, "sum": {"$sum": "$cost_usd"}}}]
    )

    total = 0.0
    async for r in db.customer_chat_cost.aggregate(total_pipe):
        total = r["sum"]
    customers_only = 0.0
    async for r in db.customer_chat_cost.aggregate(customers_pipe):
        customers_only = r["sum"]

    print(f"TOTAL (all traffic): ${total}")
    print(f"REAL CUSTOMERS ONLY: ${customers_only}")

    assert total == 15.00, "sanity: all 4 rows summed"
    assert customers_only == 1.00, (
        "only the real 'pro' non-admin user's $1.00 should count — "
        "founder/admin/orphaned-canary rows must be excluded"
    )


# ─────────────────────────────────────────────────────────────
# T-F1b — a brand-new, never-before-seen test-user pattern is
# caught automatically (no hardcoded string list to update)
# ─────────────────────────────────────────────────────────────
async def test_f1b_new_unregistered_test_id_is_caught_with_no_list_update(db):
    """A completely novel harness ID nobody hardcoded anywhere still
    gets excluded, because it was never signed up (no dev_users row) —
    proving the mechanism doesn't rot as new test IDs get invented."""
    from services.customer_cost_tracker import real_customer_match_stages

    tag = uuid.uuid4().hex[:8]
    brand_new_harness_id = f"totally-new-harness-id-nobody-listed-{tag}"
    await _mk_cost_row(db, brand_new_harness_id, "deepseek/deepseek-chat", 3.00)

    match_tag = {"$match": {"_test_run": True, "user_id": brand_new_harness_id}}
    customers_pipe = (
        [match_tag] + real_customer_match_stages()
        + [{"$group": {"_id": None, "sum": {"$sum": "$cost_usd"}}}]
    )
    customers_only = 0.0
    async for r in db.customer_chat_cost.aggregate(customers_pipe):
        customers_only = r["sum"]

    assert customers_only == 0.0, (
        "an ID with no dev_users row must be excluded automatically, "
        "with zero code changes needed to add it to a list"
    )


async def test_real_customer_user_ids_helper(db):
    from services.customer_cost_tracker import real_customer_user_ids

    tag = uuid.uuid4().hex[:8]
    real_uid, founder_uid, orphan_uid = f"real2-{tag}", f"founder2-{tag}", f"orphan2-{tag}"
    await _mk_user(db, real_uid, tier="starter")
    await _mk_user(db, founder_uid, tier="founder")

    result = await real_customer_user_ids(db, [real_uid, founder_uid, orphan_uid])
    assert result == {real_uid}
