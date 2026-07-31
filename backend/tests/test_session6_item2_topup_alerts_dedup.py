"""
Session 6 · Item 2 — topup_alerts cross-day dedup regression contract.

Real-user QA discovered `db.topup_alerts` had 18 active `critical`
rows for Tavily — one per day since 2026-06-24 — because the old
`alert_key` template was `{integration}::{severity}::{day}`. A
long-standing incident piled a new row every 24 h. Founder saw
that as multiple duplicate CRITICAL banners in /admin.

The fix in `services/topup_alerts.upsert_alerts_from_snapshot`
now dedups on `(integration_id, severity, status="active")` day-
agnostic: an already-open incident gets its `last_seen` refreshed
in place rather than spawning a new daily row. A NEW incident
(after resolution) still opens a fresh row with today's day_key.

Zero mocks. Real Mongo via `pytest-asyncio` + a real motor client
seeded to the standard aurem_dev database.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest


@pytest.fixture
async def db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    _db = client[os.environ["DB_NAME"]]
    yield _db
    client.close()


async def _seed_snap(iid: str, severity_hint: str = "critical") -> dict:
    """Build a snapshot payload the module expects, matching the shape
    of `services.integration_health.run_all_probes()`."""
    return {
        "generated_at": time.time(),
        "results": [{
            "id":       iid,
            "name":     iid.title(),
            "status":   "broken" if severity_hint == "critical" else "warn",
            "summary":  "Simulated failure for dedup test",
            "detail":   "Simulated detail",
            "fix_hint": "Simulated fix hint",
        }],
    }


async def _cleanup(db, iid: str) -> None:
    await db.topup_alerts.delete_many({"integration_id": iid})


@pytest.mark.asyncio
async def test_second_day_call_refreshes_not_dupes(db):
    """Two `upsert_alerts_from_snapshot` calls with different
    generated_at days for the SAME (integration, severity) MUST
    result in exactly ONE active row — the second call refreshes
    `last_seen` + `seen_count`, doesn't create a new row."""
    from services.topup_alerts import upsert_alerts_from_snapshot
    iid = f"_test_dedup_day_{int(time.time())}"
    await _cleanup(db, iid)
    try:
        # Day 1
        snap1 = await _seed_snap(iid)
        new1 = await upsert_alerts_from_snapshot(db, snap1)
        assert len(new1) == 1, f"day 1 should open 1 new alert, got {new1!r}"

        # Day 2 — pretend it's tomorrow
        snap2 = await _seed_snap(iid)
        snap2["generated_at"] = snap1["generated_at"] + 86400
        new2 = await upsert_alerts_from_snapshot(db, snap2)
        assert new2 == [], (
            f"day 2 should NOT open a new alert when one is already "
            f"active, got {new2!r}"
        )

        rows = await db.topup_alerts.find(
            {"integration_id": iid, "status": "active"}
        ).to_list(None)
        assert len(rows) == 1, (
            f"expected exactly 1 active row after 2 daily calls, "
            f"got {len(rows)}: {[r.get('alert_key') for r in rows]}"
        )
        assert rows[0]["seen_count"] == 2, rows[0]
        assert rows[0]["last_seen"]  >  rows[0]["first_seen"], rows[0]
    finally:
        await _cleanup(db, iid)


@pytest.mark.asyncio
async def test_thirty_daily_calls_still_one_active_row(db):
    """Simulate the exact scenario that spawned the 18 Tavily rows:
    30 daily probe cycles hit the same integration with the same
    critical severity.  Post-fix must yield exactly ONE active row."""
    from services.topup_alerts import upsert_alerts_from_snapshot
    iid = f"_test_dedup_30days_{int(time.time())}"
    await _cleanup(db, iid)
    try:
        base_t = time.time()
        for day_offset in range(30):
            snap = await _seed_snap(iid)
            snap["generated_at"] = base_t + (day_offset * 86400)
            await upsert_alerts_from_snapshot(db, snap)
        active = await db.topup_alerts.count_documents(
            {"integration_id": iid, "status": "active"})
        assert active == 1, (
            f"30 daily calls should yield 1 active row, got {active}"
        )
        # And the seen_count captures the pile-up in a single row.
        row = await db.topup_alerts.find_one(
            {"integration_id": iid, "status": "active"})
        assert row["seen_count"] == 30, row
    finally:
        await _cleanup(db, iid)


@pytest.mark.asyncio
async def test_resolution_then_reopen_creates_new_row(db):
    """Business-logic invariant: if an incident RESOLVES (probe went
    healthy) and then RE-BREAKS later, the second breakage MUST
    open a NEW row — that's a distinct incident, not the same
    ongoing one.  The dedup only holds while `status=active`."""
    from services.topup_alerts import upsert_alerts_from_snapshot
    iid = f"_test_dedup_reopen_{int(time.time())}"
    await _cleanup(db, iid)
    try:
        # Day 1: opens critical
        snap1 = await _seed_snap(iid)
        await upsert_alerts_from_snapshot(db, snap1)
        # Day 2: probe now healthy → resolves
        healthy = {
            "generated_at": snap1["generated_at"] + 86400,
            "results": [{
                "id": iid, "name": iid.title(), "status": "ok",
                "summary": "healthy", "detail": "", "fix_hint": "",
            }],
        }
        await upsert_alerts_from_snapshot(db, healthy)
        active = await db.topup_alerts.count_documents(
            {"integration_id": iid, "status": "active"})
        assert active == 0, "probe healthy should resolve all active rows"

        # Day 3: probe broken again → SHOULD open a new active row
        snap3 = await _seed_snap(iid)
        snap3["generated_at"] = snap1["generated_at"] + (2 * 86400)
        new3 = await upsert_alerts_from_snapshot(db, snap3)
        assert len(new3) == 1, "re-break after resolution must open new row"
        rows = await db.topup_alerts.find(
            {"integration_id": iid}
        ).sort("first_seen", 1).to_list(None)
        assert len(rows) == 2, "should have 2 rows: 1 resolved + 1 active"
        statuses = sorted([r["status"] for r in rows])
        assert statuses == ["active", "resolved"], statuses
    finally:
        await _cleanup(db, iid)


@pytest.mark.asyncio
async def test_no_regression_tavily_currently_shows_one_active(db):
    """Post-cleanup snapshot: after the one-time cleanup migration
    (session6_item2_dedup_cleanup), `topup_alerts` must have AT MOST
    ONE active row per (integration_id, severity)."""
    pipeline = [
        {"$match": {"status": "active"}},
        {"$group": {
            "_id": {"integration_id": "$integration_id",
                    "severity":       "$severity"},
            "n":   {"$sum": 1},
        }},
        {"$match": {"n": {"$gt": 1}}},
    ]
    dupes = await db.topup_alerts.aggregate(pipeline).to_list(None)
    assert dupes == [], (
        f"REGRESSION — active-alert duplicates still present: {dupes}"
    )


@pytest.mark.asyncio
async def test_source_alert_key_still_used_for_new_row_only(db):
    """The `alert_key` field on the NEW row must still include today's
    `day_key` so the sparse-unique index on `alert_key` continues to
    prevent same-day dupes if the probe fires twice within the same
    minute (belt-and-suspenders defence)."""
    from services.topup_alerts import upsert_alerts_from_snapshot
    iid = f"_test_dedup_key_shape_{int(time.time())}"
    await _cleanup(db, iid)
    try:
        snap = await _seed_snap(iid)
        await upsert_alerts_from_snapshot(db, snap)
        row = await db.topup_alerts.find_one(
            {"integration_id": iid, "status": "active"})
        assert row["alert_key"].startswith(f"{iid}::critical::"), row
        # Format guard: three "::" segments.
        parts = row["alert_key"].split("::")
        assert len(parts) == 3, parts
        # And day_key portion is a valid date-ish string.
        assert len(parts[2]) == 10 and parts[2][4] == "-", parts
    finally:
        await _cleanup(db, iid)
