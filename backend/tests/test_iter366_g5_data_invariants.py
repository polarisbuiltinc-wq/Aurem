"""
test_iter366_g5_data_invariants.py — G5 · Data invariant tests

Real invariants that must hold in prod-shaped data:
  1. Every dev_users row has EXACTLY ONE "current" tier — no dup rows,
     no null tier.
  2. Every incident row that has both detected_at + resolved_at has
     resolved_at >= detected_at (no time-travel MTTR).
  3. Every loop_sessions row has state ∈ known enum (no orphans /
     free-text drift).
  4. date-helper: `datetime.now(timezone.utc)` round-trips through
     ISO string + fromisoformat with timezone preserved.
  5. dev_users.tokens_granted (bonus tokens) is never negative.
  6. Every terminal loop_execution_log row has non-negative duration_s.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


# ── Invariant 1 · one tier per user ─────────────────────────────────

@pytest.mark.asyncio
async def test_dev_users_exactly_one_tier_per_row():
    db = _db()
    n_null_tier = await db.dev_users.count_documents({
        "$or": [{"tier": {"$exists": False}}, {"tier": None}],
    })
    assert n_null_tier == 0, (
        f"{n_null_tier} dev_users rows have null/missing tier — "
        "the /admin/tokens grant path assumes tier is always set"
    )
    # No user should have two rows (uniq_email index should prevent this).
    pipeline = [
        {"$group": {"_id": "$email", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
    ]
    dups = [d async for d in db.dev_users.aggregate(pipeline)]
    assert not dups, f"dev_users has duplicate email rows: {dups[:5]}"


# ── Invariant 2 · incident MTTR never negative ──────────────────────

@pytest.mark.asyncio
async def test_incidents_resolved_after_detected():
    db = _db()
    if "incidents" not in await db.list_collection_names():
        pytest.skip("incidents collection not yet present")
    bad = []
    async for d in db.incidents.find({
        "detected_at": {"$exists": True},
        "resolved_at": {"$exists": True, "$ne": None},
    }, {"_id": 0, "incident_id": 1, "detected_at": 1, "resolved_at": 1}):
        det = d.get("detected_at")
        res = d.get("resolved_at")
        # Both may be datetime OR float epoch — coerce.
        if hasattr(det, "timestamp"): det = det.timestamp()
        if hasattr(res, "timestamp"): res = res.timestamp()
        if det is not None and res is not None and res < det:
            bad.append(d)
    assert not bad, f"MTTR time-travel in incidents: {bad[:3]}"


# ── Invariant 3 · loop state enum discipline ────────────────────────

@pytest.mark.asyncio
async def test_loop_sessions_state_is_known_enum():
    db = _db()
    if "loop_sessions" not in await db.list_collection_names():
        pytest.skip("loop_sessions collection not yet present")
    from services.loop_engine import LoopState
    known = {s.value for s in LoopState}
    bad = []
    async for d in db.loop_sessions.find({}, {"_id": 0, "loop_id": 1, "state": 1}):
        st = d.get("state")
        if st is not None and st not in known:
            bad.append(d)
    assert not bad, (
        f"loop_sessions has rows with unknown state values: {bad[:5]} "
        f"(known: {sorted(known)})"
    )


# ── Invariant 4 · date-helper round-trip ────────────────────────────

def test_utc_datetime_roundtrip_preserves_tz():
    now = datetime.now(timezone.utc)
    iso = now.isoformat()
    parsed = datetime.fromisoformat(iso)
    assert parsed.tzinfo is not None, (
        "ISO round-trip lost tzinfo — a naive datetime here would "
        "poison the incident MTTR + funnel window queries"
    )
    delta = abs((parsed - now).total_seconds())
    assert delta < 0.001


def test_datetime_utcnow_not_used_in_hot_paths():
    """`datetime.utcnow()` is a naive-datetime landmine. Enforce a hard
    ban on the top-3 hot paths where tz drift caused Iter 212m-131
    MTTR miscounts."""
    import pathlib
    hot = [
        "/app/backend/services/loop_engine.py",
        "/app/backend/services/incident_log.py",
        "/app/backend/services/process_recovery.py",
    ]
    hits = []
    for p in hot:
        src = pathlib.Path(p).read_text(encoding="utf-8", errors="ignore")
        if "datetime.utcnow(" in src:
            hits.append(p)
    assert not hits, (
        f"datetime.utcnow() found in hot-path files: {hits} — use "
        "datetime.now(timezone.utc)"
    )


# ── Invariant 5 · non-negative bonus tokens ─────────────────────────

@pytest.mark.asyncio
async def test_no_negative_tokens_granted():
    db = _db()
    bad = await db.dev_users.count_documents({"tokens_granted": {"$lt": 0}})
    assert bad == 0, f"{bad} dev_users have negative tokens_granted"


# ── Invariant 6 · duration_s non-negative ───────────────────────────

@pytest.mark.asyncio
async def test_loop_execution_log_duration_non_negative():
    db = _db()
    if "loop_execution_log" not in await db.list_collection_names():
        pytest.skip("loop_execution_log not yet present")
    bad = await db.loop_execution_log.count_documents(
        {"duration_s": {"$lt": 0}}
    )
    assert bad == 0, (
        f"{bad} loop_execution_log rows have negative duration_s "
        "— either clock drift or a bad time.time() reference"
    )
