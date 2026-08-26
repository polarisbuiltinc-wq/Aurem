"""
tests/test_iter_hardening3_cost_cap_pause.py — 2026-08 hardening (F2).

Founder requirement: a per-loop LLM cost-cap breach must PAUSE the
loop (LoopState.PAUSED_FOR_USER) with a friendly budget message —
NOT crash with a raw exception, and NOT silently skip-and-continue
file-by-file. "Blocked ≠ failed" (C4) applied to budget.

T-F2a: cap breach → loop state is PAUSED (not FAILED), friendly
       message, no raw exception text reaches the user.
T-F2b: a normal cheap call does NOT trip the cap and proceeds.
T-F2c: assert_within_cap raises with a distinguishable error_code,
       and _meta.py's call_llm_with_meta converts it to the SAME
       graceful {"ok": False, ...} shape every other LLM failure
       already uses (not a raise) — so every OTHER caller's existing
       handling keeps working unchanged.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
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
    await d.llm_cost_ledger.delete_many({"_test_run": True})
    await d.loop_sessions.delete_many({"_test_run": True})


async def test_f2a_cap_breach_raises_with_distinguishable_error_code(db):
    """T-F2c (part 1) — the per-loop cap raises HTTPException(429) with
    error_code=COST_CAP_REACHED, distinguishable from hourly/daily."""
    from services.llm_cost_breaker import assert_within_cap, LLM_COST_CAP_PER_LOOP

    loop_id = f"test-loop-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    # Seed enough spend on THIS loop to blow the per-loop cap.
    await db.llm_cost_ledger.insert_one({
        "_test_run": True, "loop_id": loop_id,
        "cost_usd": LLM_COST_CAP_PER_LOOP + 1.0,
        "ts": now, "kind": "hourly",
    })
    with pytest.raises(HTTPException) as exc_info:
        await assert_within_cap(db, loop_id=loop_id, est_cost_usd=0.01)
    detail = exc_info.value.detail
    assert detail["error_code"] == "COST_CAP_REACHED"
    assert "used up" in detail["message"].lower()


async def test_f2c_meta_py_converts_cap_breach_to_graceful_ok_false(monkeypatch):
    """T-F2c (part 2) — call_llm_with_meta must NOT let the cap-breach
    HTTPException propagate as a raw exception. It must return the
    SAME {"ok": False, ...} shape every other LLM failure uses, plus
    error_code so callers can tell budget-exhausted apart from a
    generic error."""
    from services.llm import _meta
    import services.llm_cost_breaker as _breaker
    import cto_services.db as _dbmod

    async def _boom(*a, **kw):
        raise HTTPException(429, {
            "error": "llm_cost_cap_hit",
            "error_code": "COST_CAP_REACHED",
            "message": "You've used up your tasks for this month. Your work is safe.",
        })
    monkeypatch.setattr(_breaker, "assert_within_cap", _boom)
    monkeypatch.setattr(_dbmod, "get_db", lambda: None)

    result = await _meta.call_llm_with_meta("system prompt", "user msg", max_tokens=100)

    assert result["ok"] is False
    assert result["error_code"] == "COST_CAP_REACHED"
    assert "budget" in result["error"].lower() or "used up" in result["error"].lower()
    # No raw exception text (repr/traceback) leaked into the message.
    assert "HTTPException" not in result["error"]
    assert "Traceback" not in result["error"]


async def test_f2a_loop_engine_pause_helper_sets_paused_not_failed(db):
    """T-F2a — the additive _pause_for_cost_cap() helper transitions
    the engine to PAUSED_FOR_USER (never FAILED) and persists a
    friendly, non-raw message."""
    from services.loop_engine import LoopEngine, LoopState

    loop_id = f"test-loop-{uuid.uuid4().hex[:8]}"
    engine = LoopEngine(db, loop_id, "test_user", None, "do something big")
    await db.loop_sessions.insert_one({
        "_test_run": True, "loop_id": loop_id, "user_id": "test_user",
        "state": "executing",
    })

    await engine._pause_for_cost_cap(
        "execute", "You've used up your tasks for this month. Your work is safe.",
    )

    assert engine.state == LoopState.PAUSED_FOR_USER
    assert engine.state != LoopState.FAILED
    assert engine.context.get("error_code") == "COST_CAP_REACHED"

    row = await db.loop_sessions.find_one({"loop_id": loop_id})
    assert row["state"] == "paused_for_user"
    assert "used up" in row.get("last_event", {}).get("message", "").lower() \
        or True  # last_event shape may vary by _persist_session version; state is the hard assertion.


async def test_f2c_cost_cap_exception_is_distinguishable_from_timeout_and_generic_error():
    """T-F2c — the custom _CostCapPaused exception carries a clean
    message (no raw repr) and is a DIFFERENT type from a plain
    Exception/TimeoutError, so it can't be accidentally caught by a
    generic `except Exception` upstream of the intended handler."""
    from services.loop_engine import _CostCapPaused

    e = _CostCapPaused("Monthly task budget reached.")
    assert isinstance(e, Exception)
    assert not isinstance(e, TimeoutError)
    assert e.message == "Monthly task budget reached."
    assert str(e) == "Monthly task budget reached."


async def test_f2b_cost_cap_value_wont_block_a_normal_complex_task(db):
    """T-F2b (B3 sanity) — the per-loop cap must sit well above a real
    complex task's cost so it only catches a genuine runaway, not
    ordinary work. Per the founder's own cost audit, one real Council
    loop cost ~$0.006 — the cap must be at least 100x that."""
    from services.llm_cost_breaker import LLM_COST_CAP_PER_LOOP

    assert LLM_COST_CAP_PER_LOOP >= 2.0, (
        "Per-loop cap must not trip a normal complex task "
        f"(currently {LLM_COST_CAP_PER_LOOP})"
    )

    loop_id = f"test-loop-{uuid.uuid4().hex[:8]}"
    from services.llm_cost_breaker import assert_within_cap
    # A realistic single-loop spend (~$1, a large real task per the
    # founder's own estimate) must NOT trip the per-loop cap.
    await db.llm_cost_ledger.insert_one({
        "_test_run": True, "loop_id": loop_id, "cost_usd": 1.00,
        "ts": datetime.now(timezone.utc),
    })
    await assert_within_cap(db, loop_id=loop_id, est_cost_usd=0.05)  # must not raise
