"""
tests/test_migration_framework.py
=================================
End-to-end tests for the AUREM migration framework.

Every test hits the REAL local MongoDB (per AUREM's zero-mocks rule).
Each test uses a uniquely-named DB it creates and drops so it never
touches production or dev collections.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from migrations import framework as fw   # noqa: E402
from migrations.base import Migration    # noqa: E402  # re-exported for future test extension


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def scratch_db():
    mongo_url = os.environ["MONGO_URL"]
    client = AsyncIOMotorClient(mongo_url)
    db_name = f"aurem_migtest_{uuid.uuid4().hex[:12]}"
    db = client[db_name]
    try:
        yield db
    finally:
        await client.drop_database(db_name)
        client.close()


@pytest.fixture
def tmp_migrations(tmp_path):
    d = tmp_path / "migs"
    d.mkdir()
    (d / "__init__.py").write_text("")
    (d / "base.py").write_text(
        (_BACKEND_ROOT / "migrations" / "base.py").read_text()
    )
    return d


def _write_mig(directory: Path, version: str, name: str, *,
                up_body: str = "pass", down_body: str = "pass",
                irreversible: bool = False, dev_only: bool = False) -> Path:
    filename = f"{version}_{name}.py"
    content = f'''from migrations.base import Migration

class {name.title().replace("_", "")}Mig(Migration):
    version = "{version}"
    name = "{name}"
    description = "test migration {version}"
    dev_only = {dev_only}
    irreversible = {irreversible}

    async def up(self, db):
        {up_body}

    async def down(self, db):
        {down_body}
'''
    p = directory / filename
    p.write_text(content)
    return p


# ── Tests ────────────────────────────────────────────────────────────

async def test_discover_real_migrations_finds_001_and_002():
    migs = fw.discover_migrations()
    versions = [m.version for m in migs]
    assert "001" in versions
    assert "002" in versions
    m1 = next(m for m in migs if m.version == "001")
    m2 = next(m for m in migs if m.version == "002")
    assert m1.name == "aurem_upgrade_indexes"
    assert m2.name == "encrypt_pats"
    assert m2.irreversible is True


async def test_discover_rejects_duplicate_version(tmp_migrations):
    _write_mig(tmp_migrations, "001", "first")
    (tmp_migrations / "001_second.py").write_text('''from migrations.base import Migration
class SecondMig(Migration):
    version = "001"
    name = "second"
    async def up(self, db): pass
    async def down(self, db): pass
''')
    with pytest.raises(RuntimeError, match="duplicate migration version"):
        fw.discover_migrations(tmp_migrations)


async def test_discover_rejects_version_filename_mismatch(tmp_migrations):
    (tmp_migrations / "003_liar.py").write_text('''from migrations.base import Migration
class LiarMig(Migration):
    version = "007"
    name = "liar"
    async def up(self, db): pass
    async def down(self, db): pass
''')
    with pytest.raises(RuntimeError, match="declares version"):
        fw.discover_migrations(tmp_migrations)


async def test_status_starts_empty(scratch_db):
    report = await fw.status(scratch_db)
    assert report.applied == []
    assert len(report.pending) >= 2  # real 001 + 002 discovered
    assert report.drift == []
    assert report.orphans == []


async def test_apply_pending_runs_in_order_and_records_history(scratch_db):
    # Apply only up to 001 (002 requires AUREM_MASTER_KEY which may not be set)
    results = await fw.apply_pending(scratch_db, target="001", env="test")
    assert len(results) == 1
    assert results[0].version == "001"
    assert results[0].ok is True
    hist = await scratch_db["migration_history"].find_one({"version": "001"})
    assert hist is not None
    assert hist["status"] == "applied"
    assert hist["env"] == "test"
    assert hist["checksum"]
    idx = await scratch_db["project_brains"].index_information()
    assert any(
        spec.get("unique") and any(k[0] == "project_id" for k in spec["key"])
        for spec in idx.values()
    )


async def test_apply_is_idempotent(scratch_db):
    r1 = await fw.apply_pending(scratch_db, target="001", env="test")
    r2 = await fw.apply_pending(scratch_db, target="001", env="test")
    assert len(r1) == 1 and r1[0].ok
    assert r2 == []


async def test_rollback_last_reverses_up(scratch_db):
    await fw.apply_pending(scratch_db, target="001", env="test")
    results = await fw.rollback_last(scratch_db, env="test")
    assert len(results) == 1
    assert results[0].version == "001"
    assert results[0].ok is True
    row = await scratch_db["migration_history"].find_one({"version": "001"})
    assert row["status"] == "rolled_back"
    assert row["rolled_back_at"] is not None


async def test_rollback_refuses_irreversible_without_force(
    scratch_db, tmp_migrations, monkeypatch,
):
    _write_mig(tmp_migrations, "001", "irrev_step", irreversible=True,
                up_body="await db.foo.insert_one({'a':1})",
                down_body="await db.foo.delete_many({})")
    migs = fw.discover_migrations(tmp_migrations)
    monkeypatch.setattr(fw, "discover_migrations",
                        lambda directory=None: migs)
    await fw.apply_pending(scratch_db, env="test")
    # force=False → refused
    r = await fw.rollback_last(scratch_db, force=False)
    assert r[0].ok is False
    assert "irreversible" in (r[0].error or "").lower()
    # force=True → proceeds
    r2 = await fw.rollback_last(scratch_db, force=True, env="test")
    assert r2[0].ok is True
    # data cleaned up by down()
    assert (await scratch_db["foo"].count_documents({})) == 0


async def test_mark_applied_records_without_running(scratch_db):
    await fw.mark_applied(scratch_db, "001", env="test")
    row = await scratch_db["migration_history"].find_one({"version": "001"})
    assert row["status"] == "applied"
    assert row.get("imported") is True
    # Apply now: nothing pending since 001 is marked applied
    results = await fw.apply_pending(scratch_db, target="001", env="test")
    assert results == []


async def test_verify_detects_checksum_drift(scratch_db, tmp_migrations, monkeypatch):
    p = _write_mig(tmp_migrations, "001", "csum_test")
    real_discover = fw.discover_migrations
    migs_orig = real_discover(tmp_migrations)
    monkeypatch.setattr(fw, "discover_migrations",
                        lambda directory=None: migs_orig)
    await fw.apply_pending(scratch_db, env="test")
    # Modify file, then re-discover using the REAL (unpatched) function
    p.write_text(p.read_text() + "\n# changed after apply\n")
    migs_after = real_discover(tmp_migrations)
    assert migs_after[0].checksum != migs_orig[0].checksum
    monkeypatch.setattr(fw, "discover_migrations",
                        lambda directory=None: migs_after)
    drift = await fw.verify_checksums(scratch_db)
    assert len(drift) == 1
    assert drift[0].version == "001"


async def test_dev_only_skipped_in_prod(scratch_db, tmp_migrations, monkeypatch):
    _write_mig(tmp_migrations, "001", "prod_ok")
    _write_mig(tmp_migrations, "002", "dev_seed", dev_only=True,
                up_body="await db.seed.insert_one({'x':1})",
                down_body="pass")
    migs = fw.discover_migrations(tmp_migrations)
    monkeypatch.setattr(fw, "discover_migrations",
                        lambda directory=None: migs)
    results = await fw.apply_pending(scratch_db, env="prod")
    applied_versions = [r.version for r in results]
    assert "001" in applied_versions
    assert "002" not in applied_versions
    assert (await scratch_db["seed"].count_documents({})) == 0


async def test_dev_only_runs_in_dev(scratch_db, tmp_migrations, monkeypatch):
    _write_mig(tmp_migrations, "001", "dev_seed_only", dev_only=True,
                up_body="await db.seed.insert_one({'x':1})",
                down_body="pass")
    migs = fw.discover_migrations(tmp_migrations)
    monkeypatch.setattr(fw, "discover_migrations",
                        lambda directory=None: migs)
    await fw.apply_pending(scratch_db, env="dev")
    assert (await scratch_db["seed"].count_documents({})) == 1


async def test_dry_run_does_not_persist(scratch_db, tmp_migrations, monkeypatch):
    _write_mig(tmp_migrations, "001", "dry_test",
                up_body="await db.dry.insert_one({'x':1})",
                down_body="pass")
    migs = fw.discover_migrations(tmp_migrations)
    monkeypatch.setattr(fw, "discover_migrations",
                        lambda directory=None: migs)
    results = await fw.apply_pending(scratch_db, dry_run=True, env="test")
    assert results[0].ok
    assert (await scratch_db["dry"].count_documents({})) == 0
    assert (await scratch_db["migration_history"].count_documents({})) == 0


async def test_scaffold_new_migration_creates_next_version(tmp_migrations):
    _write_mig(tmp_migrations, "001", "existing")
    _write_mig(tmp_migrations, "002", "also_existing")
    new_path = fw.scaffold_new_migration("brand_new_feature", tmp_migrations)
    assert new_path.name == "003_brand_new_feature.py"
    assert new_path.exists()
    body = new_path.read_text()
    assert 'version = "003"' in body
    assert 'name = "brand_new_feature"' in body


async def test_orphan_history_row_detected(scratch_db):
    await scratch_db["migration_history"].insert_one({
        "version": "999",
        "name": "ghost",
        "applied_at": datetime.now(timezone.utc),
        "duration_ms": 0,
        "checksum": "deadbeef",
        "env": "test",
        "status": "applied",
    })
    report = await fw.status(scratch_db)
    assert len(report.orphans) == 1
    assert report.orphans[0].version == "999"


async def test_apply_stops_on_first_failure(scratch_db, tmp_migrations, monkeypatch):
    _write_mig(tmp_migrations, "001", "ok_step",
                up_body="await db.ok.insert_one({'x':1})",
                down_body="pass")
    _write_mig(tmp_migrations, "002", "boom",
                up_body='raise RuntimeError("boom")',
                down_body="pass")
    _write_mig(tmp_migrations, "003", "never_runs")
    migs = fw.discover_migrations(tmp_migrations)
    monkeypatch.setattr(fw, "discover_migrations",
                        lambda directory=None: migs)
    results = await fw.apply_pending(scratch_db, env="test")
    assert [r.version for r in results] == ["001", "002"]
    assert results[0].ok is True
    assert results[1].ok is False
    assert "boom" in (results[1].error or "")
    vers = await scratch_db["migration_history"].distinct("version")
    assert vers == ["001"]


async def test_target_limits_up_scope(scratch_db, tmp_migrations, monkeypatch):
    _write_mig(tmp_migrations, "001", "one")
    _write_mig(tmp_migrations, "002", "two")
    _write_mig(tmp_migrations, "003", "three")
    migs = fw.discover_migrations(tmp_migrations)
    monkeypatch.setattr(fw, "discover_migrations",
                        lambda directory=None: migs)
    results = await fw.apply_pending(scratch_db, target="002", env="test")
    assert [r.version for r in results] == ["001", "002"]
    remaining = await scratch_db["migration_history"].distinct("version")
    assert sorted(remaining) == ["001", "002"]


async def test_target_rollback_reverts_multiple_in_reverse_order(
    scratch_db, tmp_migrations, monkeypatch,
):
    _write_mig(tmp_migrations, "001", "keep")
    _write_mig(tmp_migrations, "002", "roll_two",
                up_body="await db.two.insert_one({'x':1})",
                down_body="await db.two.delete_many({})")
    _write_mig(tmp_migrations, "003", "roll_three",
                up_body="await db.three.insert_one({'x':1})",
                down_body="await db.three.delete_many({})")
    migs = fw.discover_migrations(tmp_migrations)
    monkeypatch.setattr(fw, "discover_migrations",
                        lambda directory=None: migs)
    await fw.apply_pending(scratch_db, env="test")
    # Roll back to state after 001 → 003 and 002 both reverted, in reverse
    results = await fw.rollback_last(scratch_db, target="001", env="test")
    assert [r.version for r in results] == ["003", "002"]
    assert all(r.ok for r in results)
    assert (await scratch_db["two"].count_documents({})) == 0
    assert (await scratch_db["three"].count_documents({})) == 0
    # 001 still recorded applied
    row = await scratch_db["migration_history"].find_one({"version": "001"})
    assert row["status"] == "applied"
