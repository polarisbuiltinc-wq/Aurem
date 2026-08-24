"""
Live TTL-field-type verification for the 2026-08-27 fix.

Verifies each writer site that feeds a MongoDB TTL index now writes a
real BSON `datetime` (not float / not ISO string), so the TTL monitor
will actually delete expired rows.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")


@pytest.fixture
def db():
    client = AsyncIOMotorClient(MONGO_URL)
    return client[DB_NAME]


@pytest.mark.asyncio
async def test_acquire_loop_lock_writes_bson_datetime(db):
    from services.loop_safety import acquire_loop_lock, release_loop_lock

    project_id = f"TEST_ttl_{uuid.uuid4().hex[:8]}"
    user_id = f"TEST_user_{uuid.uuid4().hex[:8]}"
    loop_id = f"TEST_loop_{uuid.uuid4().hex[:8]}"

    ok, existing = await acquire_loop_lock(db, project_id, user_id, loop_id)
    assert ok is True and existing is None

    try:
        raw = await db.loop_locks.find_one({"project_id": project_id})
        assert raw is not None
        assert isinstance(raw.get("acquired_at"), datetime), (
            f"acquired_at must be BSON datetime, got {type(raw.get('acquired_at'))}"
        )

        # Second concurrent acquire (different loop_id) must be refused
        loop_id2 = f"TEST_loop_{uuid.uuid4().hex[:8]}"
        ok2, existing2 = await acquire_loop_lock(db, project_id, user_id, loop_id2)
        assert ok2 is False, "second concurrent lock should be refused"
        assert existing2 is not None
    finally:
        await release_loop_lock(db, project_id, user_id, loop_id)

    # After release, a new acquire must succeed
    ok3, _ = await acquire_loop_lock(db, project_id, user_id, loop_id)
    assert ok3 is True
    await release_loop_lock(db, project_id, user_id, loop_id)


@pytest.mark.asyncio
async def test_record_loop_failure_writes_bson_datetime_and_circuit_opens(db):
    from services.loop_safety import record_loop_failure, is_loop_circuit_open

    project_id = f"TEST_ttlfail_{uuid.uuid4().hex[:8]}"
    user_id = f"TEST_u_{uuid.uuid4().hex[:8]}"

    # 2 failures — circuit should stay closed
    for i in range(2):
        await record_loop_failure(db, project_id, user_id, f"phase_{i}", f"reason_{i}")

    open_flag, count, retry = await is_loop_circuit_open(db, project_id, user_id)
    assert open_flag is False and count == 2, f"expected closed circuit with count=2, got open={open_flag} count={count}"

    # 3rd failure — should open circuit
    await record_loop_failure(db, project_id, user_id, "phase_3", "reason_3")

    # Verify BSON type on all rows
    rows = await db.loop_failures.find({"project_id": project_id}).to_list(length=10)
    assert len(rows) >= 3
    for r in rows:
        assert isinstance(r.get("occurred_at"), datetime), (
            f"occurred_at must be BSON datetime, got {type(r.get('occurred_at'))}"
        )

    open_flag, count, retry = await is_loop_circuit_open(db, project_id, user_id)
    assert open_flag is True, f"circuit must be open after 3 failures, got open={open_flag}"
    assert isinstance(retry, int) and retry >= 0

    await db.loop_failures.delete_many({"project_id": project_id})


@pytest.mark.asyncio
async def test_loop_independent_verifier_writes_bson_datetime(db):
    from services import loop_independent_verifier as liv

    loop_id = f"TEST_verify_{uuid.uuid4().hex[:8]}"

    # No spec in DB -> hits skipped_no_spec early return, still writes a row.
    result = await liv.verify(db, loop_id=loop_id, files=[])
    assert isinstance(result, dict)
    assert result.get("verdict") == "skipped_no_spec"

    rows = await db.loop_verification_log.find({"loop_id": loop_id}).to_list(length=10)
    assert len(rows) >= 1, "verify() must write at least one row"
    for r in rows:
        ca = r.get("created_at")
        assert isinstance(ca, datetime), (
            f"loop_verification_log.created_at must be BSON datetime, got {type(ca)}"
        )

    await db.loop_verification_log.delete_many({"loop_id": loop_id})


@pytest.mark.asyncio
async def test_loop_audit_log_writes_bson_datetime(db):
    from services import loop_audit_log

    loop_id = f"TEST_audit_{uuid.uuid4().hex[:8]}"

    await loop_audit_log.log(
        db,
        loop_id=loop_id,
        phase="plan",
        kind="test",
        verdict="ok",
        detail={"k": "v"},
    )

    rows = await db.loop_run_log.find({"loop_id": loop_id}).to_list(length=10)
    assert len(rows) >= 1
    for r in rows:
        assert isinstance(r.get("created_at"), datetime), (
            f"loop_run_log.created_at must be BSON datetime, got {type(r.get('created_at'))}"
        )

    await db.loop_run_log.delete_many({"loop_id": loop_id})


@pytest.mark.asyncio
async def test_naive_aware_datetime_readback_no_crash(db):
    """Regression guard for the naive/aware comparison bug main agent
    already hit in _age_seconds(). Motor returns naive datetimes by
    default. Comparing to datetime.now(timezone.utc) would raise
    TypeError. Verify the readback paths do not crash."""
    from services.loop_safety import (
        acquire_loop_lock, release_loop_lock, is_loop_circuit_open,
        record_loop_failure,
    )

    project_id = f"TEST_naive_{uuid.uuid4().hex[:8]}"
    user_id = f"TEST_u_{uuid.uuid4().hex[:8]}"
    loop_id = f"TEST_l_{uuid.uuid4().hex[:8]}"

    await acquire_loop_lock(db, project_id, user_id, loop_id)
    # Second acquire triggers ghost-sweep + _age_seconds() readback
    ok2, _ = await acquire_loop_lock(db, project_id, user_id, f"other_{uuid.uuid4().hex[:6]}")
    assert ok2 is False
    await release_loop_lock(db, project_id, user_id, loop_id)

    # Force a naive datetime into loop_failures (simulating legacy read)
    # and verify is_loop_circuit_open() does not crash.
    naive_now = datetime.utcnow()  # naive, no tzinfo
    await db.loop_failures.insert_many([
        {"project_id": project_id, "user_id": user_id, "phase": "p",
         "reason": "legacy", "occurred_at": naive_now}
        for _ in range(3)
    ])
    # This is the exact bug class — must not raise TypeError.
    open_flag, count, retry = await is_loop_circuit_open(db, project_id, user_id)
    assert isinstance(open_flag, bool)
    assert isinstance(count, int)
    assert count >= 3
    # Since we forced 3 recent naive fails, circuit should open
    assert open_flag is True

    await db.loop_failures.delete_many({"project_id": project_id})


@pytest.mark.asyncio
async def test_ttl_indexes_exist_on_target_collections(db):
    """Confirm every TTL index the fix targets is still installed. If
    any is missing, the type-fix accomplishes nothing."""
    expected = {
        "loop_locks": "acquired_at",
        "loop_failures": "occurred_at",
        "loop_verification_log": "created_at",
        "loop_run_log": "created_at",
        "loop_events": "created_at",
        "warm_start_jobs": "started_at",
        "oauth_codes": "expires_at",
        "api_keys": "expires_at",
    }
    missing = []
    for coll, field in expected.items():
        idx_info = await db[coll].index_information()
        ttl_present = any(
            "expireAfterSeconds" in v and any(k == field for k, _ in v.get("key", []))
            for v in idx_info.values()
        )
        if not ttl_present:
            missing.append(f"{coll}.{field}")
    assert not missing, f"TTL indexes missing: {missing}"
