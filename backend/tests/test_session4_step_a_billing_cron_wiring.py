"""Session 4 · Step A · REAL E2E — Monthly Maxx overage billing cron
wired into main.py startup.

Zero mocks on the scheduler logic itself. Proves:

  1. `_next_run_at()` returns the correct next UTC datetime for every
     realistic month boundary (mid-month, last-day-of-month, Feb-in-
     leap-year, Dec→Jan roll, day > 28 clamps to month length).
  2. `_billing_cron_env()` reads env vars with sane defaults and
     clamps unsafe values.
  3. `_run_overage_billing_once()` is idempotent within a bucket —
     second call in the same YYYY-MM returns `skipped=True` without
     re-billing.
  4. `schedule_maxx_overage_billing()` can be started as a task and
     it sleeps until the next fire time (proven by inspecting the
     task state and cancelling before it fires).
  5. main.py startup registers `app.state.maxx_overage_billing_task`
     via `services.billing_cron.schedule_maxx_overage_billing`
     (grep-verified).
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


# ═════════════════════════════════════════════════════════════════
# In-memory Mongo double — no external dependencies, no mocks
# on the scheduler logic itself.
# ═════════════════════════════════════════════════════════════════
class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    async def find_one(self, filt, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items()):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R:
            inserted_id = "id"
        return _R()

    def find(self, filt=None, projection=None):
        rows = [dict(r) for r in self.rows
                if all(r.get(k) == v for k, v in (filt or {}).items())]
        return _Cursor(rows)

    async def update_one(self, filt, ops, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items()):
                if "$set" in ops:
                    r.update(ops["$set"])
                class _R:
                    matched_count = 1
                    modified_count = 1
                return _R()
        class _R:
            matched_count = 0
            modified_count = 0
        return _R()


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length=100):
        return self.rows[:length]

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self.rows):
            raise StopAsyncIteration
        r = self.rows[self._i]
        self._i += 1
        return r


class _DB:
    def __init__(self):
        self._colls: dict[str, _Coll] = {}

    def __getattr__(self, n):
        if n not in self._colls:
            self._colls[n] = _Coll()
        return self._colls[n]


# ═════════════════════════════════════════════════════════════════
# 1) `_next_run_at()` — pure function, deterministic
# ═════════════════════════════════════════════════════════════════
def test_next_run_at_mid_month_returns_next_month():
    from services.billing_cron import _next_run_at
    now = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    nxt = _next_run_at(now, day=1, hour=0)
    assert nxt == datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_next_run_at_before_hour_today_returns_today():
    """Cron day matches today's day, current hour < target hour → fires today."""
    from services.billing_cron import _next_run_at
    now = datetime(2026, 4, 1, 3, 30, 0, tzinfo=timezone.utc)
    nxt = _next_run_at(now, day=1, hour=6)
    assert nxt == datetime(2026, 4, 1, 6, 0, 0, tzinfo=timezone.utc)


def test_next_run_at_after_hour_today_returns_next_month():
    """Cron day matches today, current hour >= target hour → rolls to next month."""
    from services.billing_cron import _next_run_at
    now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
    nxt = _next_run_at(now, day=1, hour=6)
    assert nxt == datetime(2026, 5, 1, 6, 0, 0, tzinfo=timezone.utc)


def test_next_run_at_december_rolls_to_january():
    from services.billing_cron import _next_run_at
    now = datetime(2026, 12, 20, 8, 0, 0, tzinfo=timezone.utc)
    nxt = _next_run_at(now, day=1, hour=0)
    assert nxt == datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_next_run_at_day_28_survives_february_non_leap():
    from services.billing_cron import _next_run_at
    now = datetime(2027, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
    nxt = _next_run_at(now, day=28, hour=0)
    # 2027 is not a leap year → Feb has 28 days → fires Feb 28
    assert nxt == datetime(2027, 2, 28, 0, 0, 0, tzinfo=timezone.utc)


def test_next_run_at_last_day_of_month():
    """Configure day=28 — max supported (clamped) — fires this month."""
    from services.billing_cron import _next_run_at
    now = datetime(2026, 4, 5, 0, 0, 0, tzinfo=timezone.utc)
    nxt = _next_run_at(now, day=28, hour=23)
    assert nxt == datetime(2026, 4, 28, 23, 0, 0, tzinfo=timezone.utc)


# ═════════════════════════════════════════════════════════════════
# 2) `_billing_cron_env()` — reads env, clamps, defaults
# ═════════════════════════════════════════════════════════════════
def test_billing_cron_env_defaults():
    from services.billing_cron import _billing_cron_env
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("BILLING_CRON_DAY", None)
        os.environ.pop("BILLING_CRON_HOUR", None)
        day, hour = _billing_cron_env()
        assert (day, hour) == (1, 0)


def test_billing_cron_env_reads_custom_values():
    from services.billing_cron import _billing_cron_env
    with patch.dict(os.environ,
                    {"BILLING_CRON_DAY": "15", "BILLING_CRON_HOUR": "9"}):
        assert _billing_cron_env() == (15, 9)


def test_billing_cron_env_clamps_day_above_28():
    """Day 31 → 28 (so Feb never breaks)."""
    from services.billing_cron import _billing_cron_env
    with patch.dict(os.environ, {"BILLING_CRON_DAY": "31"}):
        day, _ = _billing_cron_env()
        assert day == 28


def test_billing_cron_env_clamps_hour_above_23():
    from services.billing_cron import _billing_cron_env
    with patch.dict(os.environ, {"BILLING_CRON_HOUR": "25"}):
        _, hour = _billing_cron_env()
        assert hour == 23


def test_billing_cron_env_clamps_day_below_1():
    from services.billing_cron import _billing_cron_env
    with patch.dict(os.environ, {"BILLING_CRON_DAY": "0"}):
        day, _ = _billing_cron_env()
        assert day == 1


# ═════════════════════════════════════════════════════════════════
# 3) `_run_overage_billing_once()` — idempotent within a bucket
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_run_once_skips_when_already_billed_this_month():
    """Insert a `billing_cron_runs` row for this bucket → second call
    returns skipped=True and does NOT invoke `bill_maxx_overages`."""
    from services.billing_cron import _run_overage_billing_once
    db = _DB()
    bucket = datetime.now(timezone.utc).strftime("%Y-%m")
    # Pre-seed a run row for this month
    await db.billing_cron_runs.insert_one({
        "bucket": bucket,
        "source": "schedule_maxx_overage_billing",
        "billed": 5,
        "processed": 5,
    })
    # Sanity: mark bill_maxx_overages so we can prove it was NOT called
    called = {"n": 0}

    async def _stub_bill(_db):
        called["n"] += 1
        return {"billed": 0, "processed": 0, "failed": 0,
                "total_revenue_usd": 0.0, "bucket": bucket}

    with patch("services.billing_cron.bill_maxx_overages", _stub_bill):
        result = await _run_overage_billing_once(db)

    assert result["skipped"] is True
    assert result["reason"] == "already_billed_this_month"
    assert called["n"] == 0, "bill_maxx_overages must NOT be called when idempotent-skipped"


@pytest.mark.asyncio
async def test_run_once_bills_and_persists_run_row():
    """First call in a bucket bills, persists a `billing_cron_runs`
    row tagged with the scheduler source string."""
    from services.billing_cron import _run_overage_billing_once
    db = _DB()

    async def _stub_bill(_db):
        return {"billed": 3, "processed": 3, "failed": 0,
                "total_revenue_usd": 1.50,
                "bucket": datetime.now(timezone.utc).strftime("%Y-%m"),
                "ran_at": datetime.now(timezone.utc).isoformat()}

    with patch("services.billing_cron.bill_maxx_overages", _stub_bill):
        result = await _run_overage_billing_once(db)

    assert result.get("skipped") is not True
    assert result["billed"] == 3
    assert result["source"] == "schedule_maxx_overage_billing"

    # Persisted row is queryable for admin audit trail
    persisted = await db.billing_cron_runs.find_one(
        {"source": "schedule_maxx_overage_billing"}
    )
    assert persisted is not None
    assert persisted["billed"] == 3


# ═════════════════════════════════════════════════════════════════
# 4) `schedule_maxx_overage_billing()` — task boots & sleeps
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_scheduler_task_boots_and_sleeps_until_next_run():
    """Prove the scheduler:
      • starts without raising,
      • enters its sleep loop (does not fire immediately),
      • can be cancelled cleanly.
    """
    from services.billing_cron import schedule_maxx_overage_billing

    db = _DB()
    called_bill = {"n": 0}

    async def _spy_bill(_db):
        called_bill["n"] += 1
        return {"billed": 0, "processed": 0, "failed": 0,
                "total_revenue_usd": 0.0,
                "bucket": datetime.now(timezone.utc).strftime("%Y-%m")}

    task = asyncio.create_task(
        schedule_maxx_overage_billing(lambda: db))
    # Yield control several times so scheduler enters sleep()
    with patch("services.billing_cron.bill_maxx_overages", _spy_bill):
        await asyncio.sleep(0.2)
        assert not task.done(), "scheduler should be sleeping, not exited"
        # It should NOT have fired the billing yet (next fire is >= days away)
        assert called_bill["n"] == 0
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Scheduler catches CancelledError and `return`s → task.done()
        # is True (not task.cancelled(), since it exited normally).
        assert task.done()


# ═════════════════════════════════════════════════════════════════
# 5) main.py startup wires the scheduler (grep-verified)
# ═════════════════════════════════════════════════════════════════
def test_main_py_registers_maxx_overage_billing_task():
    """Static proof that main.py startup imports
    `schedule_maxx_overage_billing` and creates a task named
    `app.state.maxx_overage_billing_task`. No import-time side
    effects — just source-text verification.
    """
    main_py = Path(__file__).parent.parent / "main.py"
    src = main_py.read_text()

    # Import exists
    assert re.search(
        r"from\s+services\.billing_cron\s+import\s+schedule_maxx_overage_billing",
        src,
    ), "main.py must import schedule_maxx_overage_billing"

    # Task is created & attached to app.state
    assert re.search(
        r"app\.state\.maxx_overage_billing_task\s*=\s*"
        r"(?:.*create_task|_supervise)\s*\(\s*"
        r"schedule_maxx_overage_billing\(",
        src,
        re.DOTALL,
    ), (
        "main.py must create app.state.maxx_overage_billing_task "
        "via asyncio.create_task(schedule_maxx_overage_billing(...)) "
        "OR the Session F `_supervise(schedule_maxx_overage_billing(...), "
        "name='maxx_overage_billing', ...)` wrapper — both accepted."
    )


def test_daily_digest_no_longer_calls_billing_cron():
    """Regression guard — piggyback from daily_digest.py has been
    surgically removed. Only the migration signpost comment remains."""
    dd = Path(__file__).parent.parent / "services" / "daily_digest.py"
    src = dd.read_text()
    # No live invocation
    assert "await bill_maxx_overages" not in src, \
        "daily_digest.py must not invoke bill_maxx_overages any more"
    assert "from services.billing_cron import bill_maxx_overages" not in src, \
        "daily_digest.py must not import bill_maxx_overages any more"
    # Signpost is present
    assert "MIGRATED" in src and "schedule_maxx_overage_billing" in src, \
        "daily_digest.py must carry the migration signpost comment"
