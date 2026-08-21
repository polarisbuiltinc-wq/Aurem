"""
test_iter364_loop_beta_rollout.py — Iter 364 Phase-1 + Phase-2 + Phase-3

Behavioural tests for the tiered Loop-Mode rollout:

  Phase 1 — total wall-clock budget + per-user concurrency cap
  Phase 2 — tiered feature-flag gate replaces founder-only lock
  Phase 3 — kill-switch, execution log, Maxx daily cap, stuck-loop
            auto-trip

Each test asserts an OUTCOME, not the mocking plumbing.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.loop_engine import (
    LoopState,
    LOOP_TOTAL_BUDGET_S,
    PHASE_TIMEOUTS_S,
)
from services import loop_beta as lb


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


# ── Phase 1 · Constants + invariants ─────────────────────────────────

def test_loop_total_budget_exists_and_exceeds_pipeline_sum():
    """LOOP_TOTAL_BUDGET_S must be a positive int AND >= the sum of a
    single-pass EXECUTE+VERIFY+SCAN+SHIP phase run. Otherwise a normal
    happy-path loop could hit the total-budget guard before any phase
    even times out."""
    assert isinstance(LOOP_TOTAL_BUDGET_S, int)
    assert LOOP_TOTAL_BUDGET_S >= 1200, (
        f"LOOP_TOTAL_BUDGET_S={LOOP_TOTAL_BUDGET_S}s is too tight — "
        "single-pass EXECUTE+VERIFY+SCAN+SHIP can burn up to "
        f"{PHASE_TIMEOUTS_S['execute']+PHASE_TIMEOUTS_S['verify']+PHASE_TIMEOUTS_S['scan']+PHASE_TIMEOUTS_S['ship']}s"
    )


def test_loop_beta_active_states_covers_every_pre_terminal():
    """The concurrency counter's _ACTIVE_STATES must include every non-
    terminal LoopState. If a new state is added and we forget to add it
    here, a user could sneak in a 2nd loop while the 1st is in that
    state."""
    non_terminal = {
        "planning", "awaiting_confirmation",
        "executing", "verifying", "scanning", "shipping",
        "self_healing", "paused_for_user",
    }
    missing = non_terminal - set(lb._ACTIVE_STATES)
    assert not missing, (
        f"_ACTIVE_STATES missing non-terminal states: {missing}"
    )


# ── Phase 1 · Concurrency counter ────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_second_start_would_be_rejected():
    """When a user has 1 active loop, count_active_loops returns >=1
    so the router will 409. Direct behavioural test — no HTTP layer."""
    db = _db()
    uid = f"u_iter364_{secrets.token_hex(3)}"
    loop_id = f"loop_iter364_conc_{secrets.token_hex(3)}"
    try:
        await db.loop_sessions.insert_one({
            "loop_id":   loop_id,
            "user_id":   uid,
            "state":     LoopState.EXECUTING.value,
            "phase":     "execute",
            "updated_at": datetime.now(timezone.utc),
        })
        n = await lb.count_active_loops(db, uid)
        assert n >= 1, f"expected at least 1 active loop, got {n}"
        assert n >= lb.LOOP_MAX_CONCURRENT_PER_USER, (
            "concurrency cap should be reached — router will 409"
        )
    finally:
        await db.loop_sessions.delete_many({"loop_id": loop_id})


@pytest.mark.asyncio
async def test_terminal_states_do_not_count_toward_concurrency():
    """A completed / failed / aborted loop must NOT block a user's
    next start."""
    db = _db()
    uid = f"u_iter364_{secrets.token_hex(3)}"
    loop_id = f"loop_iter364_done_{secrets.token_hex(3)}"
    try:
        await db.loop_sessions.insert_one({
            "loop_id":   loop_id,
            "user_id":   uid,
            "state":     "completed",   # terminal
            "phase":     "ship",
            "updated_at": datetime.now(timezone.utc),
        })
        n = await lb.count_active_loops(db, uid)
        assert n == 0, f"terminal state should not count, got {n}"
    finally:
        await db.loop_sessions.delete_many({"loop_id": loop_id})


# ── Phase 1 · total-budget breach persists resume_reason ─────────────

@pytest.mark.asyncio
async def test_total_budget_exceeded_stamps_resume_reason():
    """Simulate the exact write the loop_engine total-budget branch
    performs on TimeoutError. Downstream (Guard 19 stuck-detector,
    loop_execution_log) keys off `resume_reason`, so if this ever
    silently changes shape, the whole stuck-loop signal disappears."""
    db = _db()
    loop_id = f"loop_iter364_budget_{secrets.token_hex(3)}"
    try:
        await db.loop_sessions.insert_one({
            "loop_id":   loop_id,
            "user_id":   "u_iter364",
            "state":     LoopState.FAILED.value,
            "phase":     "execute",
            "updated_at": datetime.now(timezone.utc),
        })
        await db.loop_sessions.update_one(
            {"loop_id": loop_id},
            {"$set": {"resume_reason": "total_budget_exceeded"}},
        )
        # Guard 19 stuck-count must see this row.
        n_stuck = await lb.count_stuck_loops(db)
        assert n_stuck >= 1, (
            "count_stuck_loops must include total_budget_exceeded rows"
        )
    finally:
        await db.loop_sessions.delete_many({"loop_id": loop_id})


# ── Phase 2 · Tiered gate matrix ─────────────────────────────────────

@pytest.mark.parametrize("user_doc,expected_ok,expected_reason", [
    ({"tier": "founder"},                 True,  ""),
    ({"is_admin": True, "tier": "free"},  True,  ""),
    ({"is_unlimited": True},              True,  ""),
    # 2026-08-21 — founder decision (after checking Admin QA Dashboard
    # Loop Beta panel: healthy kill-switch, 0 stuck loops): Pro/Team
    # unlocked for everyone now, `loop_beta_enabled` no longer required.
    ({"tier": "pro"},                                True,  ""),
    ({"tier": "team"},                               True,  ""),
    ({"tier": "pro",   "loop_beta_enabled": False},  True,  ""),
    ({"tier": "starter", "loop_beta_enabled": True},False, "tier_locked"),
    ({"tier": "free",    "loop_beta_enabled": True},False, "tier_locked"),
    ({},                                            False, "no_user"),
])
def test_is_user_allowed_matrix(user_doc, expected_ok, expected_reason):
    ok, reason = lb.is_user_allowed(user_doc)
    assert ok is expected_ok, f"user={user_doc} → expected ok={expected_ok}, got {ok}"
    assert reason == expected_reason


# ── Phase 3 · Kill-switch env override ───────────────────────────────

@pytest.mark.asyncio
async def test_kill_switch_env_override_wins(monkeypatch):
    """When LOOP_MODE_KILL_SWITCH=true is set in env, the async check
    must return True even if the DB row is unset/false."""
    monkeypatch.setenv("LOOP_MODE_KILL_SWITCH", "true")
    db = _db()
    assert await lb.is_kill_switch_on_async(db) is True


@pytest.mark.asyncio
async def test_kill_switch_db_flip(monkeypatch):
    """With env unset, flipping the DB row via set_kill_switch must
    make the async check return True. Flipping back False → False."""
    monkeypatch.delenv("LOOP_MODE_KILL_SWITCH", raising=False)
    db = _db()
    try:
        await lb.set_kill_switch(db, True, "test flip")
        assert await lb.is_kill_switch_on_async(db) is True
        await lb.set_kill_switch(db, False, "test flip back")
        assert await lb.is_kill_switch_on_async(db) is False
    finally:
        await lb.set_kill_switch(db, False, "cleanup")


# ── Phase 3 · Execution log shape ────────────────────────────────────

@pytest.mark.asyncio
async def test_log_execution_row_shape():
    """Every field the QA dashboard / cost projection queries reads
    from loop_execution_log must exist on the persisted row."""
    db = _db()
    loop_id = f"loop_iter364_log_{secrets.token_hex(3)}"
    uid = f"u_iter364_{secrets.token_hex(3)}"
    try:
        await lb.log_execution(
            db,
            user_id=uid,
            loop_id=loop_id,
            tier="pro",
            status="completed",
            duration_s=42.5,
            stuck_reason=None,
            used_maxx=True,
            used_parallel_agents=False,
            worker_tape_viewed=True,
            agent_count=2,
        )
        row = await db.loop_execution_log.find_one({"loop_id": loop_id})
        assert row is not None
        for key in (
            "user_id", "loop_id", "tier", "status", "duration_s",
            "stuck_reason", "used_maxx", "used_parallel_agents",
            "worker_tape_viewed", "agent_count", "created_at",
        ):
            assert key in row, f"loop_execution_log missing key: {key}"
        assert row["tier"] == "pro"
        assert row["status"] == "completed"
        assert row["used_maxx"] is True
        assert row["agent_count"] == 2
    finally:
        await db.loop_execution_log.delete_many({"loop_id": loop_id})


# ── Phase 3 · Maxx daily cap ────────────────────────────────────────

@pytest.mark.asyncio
async def test_maxx_daily_cap_blocks_at_402(monkeypatch):
    """Once a user has MAXX_DAILY_TASK_CAP rows in maxx_cost_log within
    24h, assert_maxx_daily_budget MUST raise HTTP 402."""
    db = _db()
    uid = f"u_iter364_maxx_{secrets.token_hex(3)}"
    # Ensure a clean, non-founder user doc.
    await db.dev_users.update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "tier": "team",
                  "is_admin": False, "is_unlimited": False}},
        upsert=True,
    )
    try:
        # Seed exactly MAXX_DAILY_TASK_CAP rows in the last 24h.
        docs = [{
            "user_id":    uid,
            "loop_id":    None,
            "created_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "total_cost_usd": 0.0,
        } for _ in range(lb.MAXX_DAILY_TASK_CAP)]
        await db.maxx_cost_log.insert_many(docs)
        with pytest.raises(HTTPException) as ei:
            await lb.assert_maxx_daily_budget(db, uid)
        assert ei.value.status_code == 402
        assert ei.value.detail["error"] == "maxx_daily_cap_reached"
    finally:
        await db.maxx_cost_log.delete_many({"user_id": uid})
        await db.dev_users.delete_one({"user_id": uid})


@pytest.mark.asyncio
async def test_maxx_daily_cap_bypasses_founder():
    """Founders / unlimited accounts must NEVER be blocked by the
    Maxx daily cap — they're internal accounts."""
    db = _db()
    uid = f"u_iter364_founder_{secrets.token_hex(3)}"
    await db.dev_users.update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "tier": "founder",
                  "is_admin": True, "is_unlimited": True}},
        upsert=True,
    )
    try:
        # Seed way over cap — should still pass.
        docs = [{
            "user_id":    uid,
            "loop_id":    None,
            "created_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "total_cost_usd": 0.0,
        } for _ in range(lb.MAXX_DAILY_TASK_CAP * 2)]
        await db.maxx_cost_log.insert_many(docs)
        # Must NOT raise.
        await lb.assert_maxx_daily_budget(db, uid)
    finally:
        await db.maxx_cost_log.delete_many({"user_id": uid})
        await db.dev_users.delete_one({"user_id": uid})


# ── Phase 3 · Auto-trip kill switch ─────────────────────────────────

@pytest.mark.asyncio
async def test_auto_trip_fires_when_stuck_exceeds_threshold(monkeypatch):
    """Seed enough loop_sessions with stuck resume_reasons to exceed
    the threshold and verify auto_trip flips the DB kill switch."""
    monkeypatch.delenv("LOOP_MODE_KILL_SWITCH", raising=False)
    db = _db()
    # Make sure we start with a clean switch.
    await lb.set_kill_switch(db, False, "test setup")
    seeds = []
    try:
        # Threshold is 3 by default; seed threshold+1 stuck rows.
        for _ in range(lb.LOOP_STUCK_TRIP_THRESHOLD + 1):
            lid = f"loop_iter364_trip_{secrets.token_hex(3)}"
            await db.loop_sessions.insert_one({
                "loop_id":       lid,
                "user_id":       "u_test",
                "state":         LoopState.FAILED.value,
                "phase":         "execute",
                "resume_reason": "total_budget_exceeded",
                "updated_at":    datetime.now(timezone.utc),
            })
            seeds.append(lid)
        trip = await lb.auto_trip_kill_switch_if_stuck(db)
        assert trip is not None, "auto_trip must fire past threshold"
        assert trip["stuck_count"] > lb.LOOP_STUCK_TRIP_THRESHOLD
        assert await lb.is_kill_switch_on_async(db) is True
    finally:
        await db.loop_sessions.delete_many({"loop_id": {"$in": seeds}})
        await lb.set_kill_switch(db, False, "test cleanup")


@pytest.mark.asyncio
async def test_auto_trip_is_noop_when_already_tripped(monkeypatch):
    """If admin has manually flipped the switch, a subsequent auto-
    trip signal must NOT overwrite the reason — otherwise the admin
    loses their audit trail."""
    monkeypatch.delenv("LOOP_MODE_KILL_SWITCH", raising=False)
    db = _db()
    await lb.set_kill_switch(db, True, "manual by admin@aurem")
    seeds = []
    try:
        for _ in range(lb.LOOP_STUCK_TRIP_THRESHOLD + 1):
            lid = f"loop_iter364_noop_{secrets.token_hex(3)}"
            await db.loop_sessions.insert_one({
                "loop_id":       lid,
                "user_id":       "u_test",
                "state":         LoopState.FAILED.value,
                "phase":         "execute",
                "resume_reason": "total_budget_exceeded",
                "updated_at":    datetime.now(timezone.utc),
            })
            seeds.append(lid)
        trip = await lb.auto_trip_kill_switch_if_stuck(db)
        assert trip is None, "auto_trip should be a no-op when already on"
        row = await db.system_flags.find_one({"key": "loop_mode_kill_switch"})
        assert (row or {}).get("reason") == "manual by admin@aurem", (
            "admin's reason must survive the auto-trip no-op"
        )
    finally:
        await db.loop_sessions.delete_many({"loop_id": {"$in": seeds}})
        await lb.set_kill_switch(db, False, "test cleanup")
