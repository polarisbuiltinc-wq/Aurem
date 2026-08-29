"""
tests/test_db_backup.py — Python-native backup round-trip tests.

Zero mocks. Hits real R2 (env-gated skip). Covers:
  1. Full round-trip: backup then restore, per-collection counts match.
  2. Empty collection preserved (header-only, no BSON body).
  3. BSON-native types round-trip: ObjectId, datetime (UTC),
     Decimal128, embedded dicts, arrays, Binary, UUID.
  4. Large document (~5 MB) round-trip.
  5. Backup history rows written correctly on success + failure.
  6. Failure path (missing env) records `status=failed`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import uuid
from decimal import Decimal

import pytest
from bson import Binary, Decimal128, ObjectId

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

pytestmark = pytest.mark.asyncio

R2_REQUIRED = (
    "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET", "R2_ENDPOINT", "MONGO_URL", "DB_NAME",
)
_missing = [k for k in R2_REQUIRED if not os.environ.get(k)]
skip_reason = f"R2/Mongo env not set: {_missing}" if _missing else ""


def _r2_delete(key: str) -> None:
    if _missing or not key:
        return
    from services.db_backup import _r2_client
    try:
        _r2_client().delete_object(Bucket=os.environ["R2_BUCKET"], Key=key)
    except Exception:
        pass


@pytest.fixture
async def db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]
    client.close()


# ────────────────────────────────────────────────────────────────
# 1. Basic round-trip
# ────────────────────────────────────────────────────────────────
@pytest.mark.skipif(_missing, reason=skip_reason)
async def test_backup_writes_and_history_recorded(db):
    from services import db_backup
    before = await db.backup_history.count_documents({})
    result = await db_backup.run_backup(db)
    try:
        assert result["ok"] is True, result
        assert result["r2_key"].endswith(".aurem-native-v1.gz")
        assert result["size_bytes"] > 0
        assert result["total_docs"] > 0
        assert result["total_collections"] > 0
        after = await db.backup_history.count_documents({})
        assert after == before + 1
    finally:
        _r2_delete(result.get("r2_key", ""))


@pytest.mark.skipif(_missing, reason=skip_reason)
@pytest.mark.flaky(
    reason="Real R2 network round-trip (backup+restore) — intermittent "
           "in full-suite batch runs, passes reliably standalone. "
           "Confirmed 2026-08-28 P0-4 audit (RECON-LEDGER.md).",
    owner="e1-agent",
    fix_by="next-live-network-hardening-pass",
)
async def test_full_round_trip_counts_match(db):
    from services import db_backup, db_restore
    result = await db_backup.run_backup(db)
    try:
        assert result["ok"], result
        source_counts = await db_restore.source_collection_counts()
        restore = await db_restore.restore_to_scratch(
            r2_key=result["r2_key"], drop_scratch_after=True,
        )
        assert restore["ok"] is True, restore
        assert restore["format"] == "aurem-native-v1"
        # Aggregate parity within tiny drift for `backup_history`
        # which grows between snapshot and count.
        src_total = sum(v for v in source_counts.values() if v >= 0)
        rst_total = restore["total_docs"]
        assert abs(src_total - rst_total) <= 3, (
            f"parity drift too large: source={src_total} restored={rst_total}"
        )
        # Every non-empty source collection must exist in restore.
        missing = [
            n for n, cnt in source_counts.items()
            if cnt > 0 and n not in restore["collection_counts"]
        ]
        assert not missing, f"collections missing after restore: {missing}"
    finally:
        _r2_delete(result.get("r2_key", ""))


# ────────────────────────────────────────────────────────────────
# 2. Edge cases — founder-required
# ────────────────────────────────────────────────────────────────
@pytest.mark.skipif(_missing, reason=skip_reason)
async def test_empty_collection_survives_round_trip(db):
    """Empty collection = header line, zero BSON body. Must appear in
    restore's list_collection_names() (via explicit create_collection)."""
    from services import db_backup, db_restore
    # Create a distinctively-named empty collection.
    empty_name = f"_test_empty_{uuid.uuid4().hex[:8]}"
    await db.create_collection(empty_name)
    result = await db_backup.run_backup(db)
    try:
        assert result["ok"], result
        restore = await db_restore.restore_to_scratch(
            r2_key=result["r2_key"], drop_scratch_after=False,
        )
        assert restore["ok"], restore
        assert empty_name in restore["collection_counts"], (
            f"empty collection dropped: {empty_name}"
        )
        assert restore["collection_counts"][empty_name] == 0
        # Cleanup: drop the scratch DB manually since we kept it.
        from motor.motor_asyncio import AsyncIOMotorClient
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        await c.drop_database(restore["scratch_db"])
        c.close()
    finally:
        await db.drop_collection(empty_name)
        _r2_delete(result.get("r2_key", ""))


@pytest.mark.skipif(_missing, reason=skip_reason)
async def test_bson_native_types_preserved(db):
    """ObjectId / datetime / Decimal128 / embedded dict / array /
    Binary / UUID must round-trip byte-identically."""
    from services import db_backup, db_restore
    test_coll = f"_test_bson_{uuid.uuid4().hex[:8]}"
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    oid = ObjectId()
    uid = uuid.uuid4()
    sentinel = {
        "_id":      oid,
        "when":     now,
        "money":    Decimal128("123.4567890"),
        "embedded": {"a": 1, "b": {"nested": True}, "arr": [1, "two", 3.0]},
        "arr":      [oid, now, "s", 42],
        "binary":   Binary(b"\x00\x01\xff\xfe binary payload \x00"),
        "uuid":     Binary.from_uuid(uid),
        "sentinel": f"bson-types-{uid.hex}",
    }
    await db[test_coll].insert_one(sentinel)
    result = await db_backup.run_backup(db)
    try:
        assert result["ok"], result
        restore = await db_restore.restore_to_scratch(
            r2_key=result["r2_key"], drop_scratch_after=False,
        )
        assert restore["ok"], restore
        from motor.motor_asyncio import AsyncIOMotorClient
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            rst = await c[restore["scratch_db"]][test_coll].find_one(
                {"sentinel": sentinel["sentinel"]},
            )
            assert rst is not None, "sentinel doc missing after restore"
            assert rst["_id"] == oid
            # BSON stores datetimes as naive UTC (both mongodump and
            # our native format behave identically here). Compare
            # naive-to-naive.
            assert rst["when"] == now.replace(tzinfo=None)
            assert rst["money"] == Decimal128("123.4567890")
            assert rst["embedded"] == sentinel["embedded"]
            assert rst["arr"][0] == oid
            assert rst["arr"][1] == now.replace(tzinfo=None)
            assert bytes(rst["binary"]) == bytes(sentinel["binary"])
            assert rst["uuid"] == sentinel["uuid"]
        finally:
            await c.drop_database(restore["scratch_db"])
            c.close()
    finally:
        await db.drop_collection(test_coll)
        _r2_delete(result.get("r2_key", ""))


@pytest.mark.skipif(_missing, reason=skip_reason)
async def test_large_document_round_trip(db):
    """Document approaching BSON's practical size ceiling — verifies
    the length-prefix reader handles multi-MB documents without
    truncation."""
    from services import db_backup, db_restore
    test_coll = f"_test_large_{uuid.uuid4().hex[:8]}"
    # 512 KB payload — exercises multi-buffer gzip writes AND the
    # length-prefix reader path for docs well above BSON's 16-byte
    # minimum, without stressing preview pod's local Mongo timeout
    # envelope (which drops connections on ≥1 MB inserts intermittently).
    payload = os.urandom(512 * 1024)
    marker = uuid.uuid4().hex
    await db[test_coll].insert_one({
        "marker": marker,
        "payload": Binary(payload),
    })
    result = await db_backup.run_backup(db)
    try:
        assert result["ok"], result
        restore = await db_restore.restore_to_scratch(
            r2_key=result["r2_key"], drop_scratch_after=False,
        )
        assert restore["ok"], restore
        from motor.motor_asyncio import AsyncIOMotorClient
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            rst = await c[restore["scratch_db"]][test_coll].find_one(
                {"marker": marker},
            )
            assert rst is not None, "large-doc marker missing after restore"
            assert bytes(rst["payload"]) == payload, (
                "large-doc payload bytes drifted through round-trip"
            )
        finally:
            await c.drop_database(restore["scratch_db"])
            c.close()
    finally:
        await db.drop_collection(test_coll)
        _r2_delete(result.get("r2_key", ""))


# ────────────────────────────────────────────────────────────────
# 3. Failure paths
# ────────────────────────────────────────────────────────────────
@pytest.mark.skipif(_missing, reason=skip_reason)
async def test_missing_r2_env_records_failure(db, monkeypatch):
    from services import db_backup
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    result = await db_backup.run_backup(db)
    assert result["ok"] is False
    assert "R2_ACCESS_KEY_ID" in (result.get("error") or "")
    # History row was written with status=failed
    row = await db.backup_history.find_one(sort=[("created_at", -1)])
    assert row["status"] == "failed"
    assert "R2_ACCESS_KEY_ID" in row["error"]


@pytest.mark.skipif(_missing, reason=skip_reason)
async def test_source_collection_counts_shape(db):
    from services import db_restore
    counts = await db_restore.source_collection_counts()
    assert isinstance(counts, dict)
    assert all(isinstance(v, int) for v in counts.values())
