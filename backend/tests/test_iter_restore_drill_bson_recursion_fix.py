"""Iter · Priority 1 · restore_drill_cron BSON RecursionError fix.

Root cause (CONFIRMED via live traceback + local `bson.encode`
reproduction, 2026-08-26): when the drill fell back through more than
one `backup_history` candidate, `run_restore_drill` set
`row["fallback_attempts"] = attempts` where `row` was itself one of
the dicts already inside `attempts` — a circular reference
(`row -> fallback_attempts -> attempts -> [..., row]`). PyMongo's
BSON encoder then recurses forever walking that cycle, surfacing as
`RecursionError: maximum recursion depth exceeded while encoding an
object to BSON` on `insert_one`. This was a self-referential-document
bug, NOT a recursive retry/call-stack bug — the candidate loop in
`run_restore_drill` was already a bounded `for` loop
(`MAX_CANDIDATES_TO_TRY`), not a recursive function.

Contract after the fix:
  1. `run_restore_drill` never produces a `row` that is BSON-encodable
     only by accident — `bson.encode(row)` must succeed whenever there
     is more than one attempt (the exact condition that used to crash).
  2. `row["fallback_attempts"]` still carries every attempt's data
     for visibility (same shape/values as before), just as independent
     copies instead of aliases.
  3. Single-candidate runs (the common case) are unaffected:
     `fallback_attempts == []`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import bson
import pytest

from services.restore_drill_cron import run_restore_drill


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def to_list(self, length=None):
        return AsyncMock(return_value=self._rows)()


def _fake_db(backup_rows):
    db = MagicMock()
    db.backup_history.find = MagicMock(return_value=_FakeCursor(backup_rows))
    db.restore_drill_history.insert_one = AsyncMock(return_value=None)
    return db


@pytest.mark.asyncio
async def test_fallback_row_is_bson_encodable_no_recursion_error(monkeypatch):
    """Reproduces the exact original crash condition (>1 candidate
    tried) and proves the resulting row round-trips through BSON."""
    from services import restore_drill_cron as m

    backup_rows = [
        {"r2_key": "newest.gz", "created_at": "2026-08-26T00:00:00Z"},
        {"r2_key": "older.gz", "created_at": "2026-08-25T00:00:00Z"},
    ]
    db = _fake_db(backup_rows)

    monkeypatch.setattr(
        m.db_restore, "source_collection_counts",
        AsyncMock(return_value={"users": 10, "tasks": 5}),
    )

    async def fake_restore(r2_key, drop_scratch_after=True):
        if r2_key == "newest.gz":
            return {"ok": False, "error": "R2 object missing", "total_docs": 0,
                     "total_collections": 0, "duration_ms": 5}
        return {"ok": True, "error": None, "total_docs": 15,
                "total_collections": 2, "duration_ms": 42}

    monkeypatch.setattr(m.db_restore, "restore_to_scratch", fake_restore)
    monkeypatch.setattr(m, "send_founder_alert", AsyncMock(return_value=None))

    row = await run_restore_drill(db)

    assert row["ok"] is True
    assert row["r2_key"] == "older.gz"
    assert len(row["fallback_attempts"]) == 2

    # This is the exact operation that previously raised RecursionError.
    encoded = bson.encode(row)
    assert isinstance(encoded, bytes)
    assert bson.decode(encoded)["r2_key"] == "older.gz"

    # No attempt in the snapshot should be the same object as `row`
    # (proves the cycle is broken, not just tolerated by luck).
    assert all(a is not row for a in row["fallback_attempts"])


@pytest.mark.asyncio
async def test_all_candidates_fail_row_still_bson_encodable(monkeypatch):
    """All-failed path also used to alias `row` into its own
    `fallback_attempts` — must be fixed too."""
    from services import restore_drill_cron as m

    backup_rows = [
        {"r2_key": "a.gz", "created_at": "2026-08-26T00:00:00Z"},
        {"r2_key": "b.gz", "created_at": "2026-08-25T00:00:00Z"},
        {"r2_key": "c.gz", "created_at": "2026-08-24T00:00:00Z"},
    ]
    db = _fake_db(backup_rows)

    monkeypatch.setattr(
        m.db_restore, "source_collection_counts",
        AsyncMock(return_value={"users": 10}),
    )

    async def fake_restore(r2_key, drop_scratch_after=True):
        return {"ok": False, "error": f"R2 download failed for {r2_key}",
                "total_docs": 0, "total_collections": 0, "duration_ms": 3}

    monkeypatch.setattr(m.db_restore, "restore_to_scratch", fake_restore)
    monkeypatch.setattr(m, "send_founder_alert", AsyncMock(return_value=None))

    row = await run_restore_drill(db)

    assert row["ok"] is False
    assert len(row["fallback_attempts"]) == 3
    encoded = bson.encode(row)
    assert bson.decode(encoded)["ok"] is False
    assert all(a is not row for a in row["fallback_attempts"])


@pytest.mark.asyncio
async def test_single_candidate_no_fallback_attempts(monkeypatch):
    """The common case (one successful candidate, no fallback) is
    unaffected by the fix."""
    from services import restore_drill_cron as m

    db = _fake_db([{"r2_key": "only.gz", "created_at": "2026-08-26T00:00:00Z"}])

    monkeypatch.setattr(
        m.db_restore, "source_collection_counts",
        AsyncMock(return_value={"users": 10}),
    )

    async def fake_restore(r2_key, drop_scratch_after=True):
        return {"ok": True, "error": None, "total_docs": 10,
                "total_collections": 1, "duration_ms": 12}

    monkeypatch.setattr(m.db_restore, "restore_to_scratch", fake_restore)

    row = await run_restore_drill(db)

    assert row["ok"] is True
    assert row["fallback_attempts"] == []
    bson.encode(row)  # must not raise
