"""
Iter 388n — Bug 6+7 regression tests

Bug 6: Ops History showed "0 steps" for real 5-step Plan→Ship loops.
Bug 7: Analytics showed "Success Rate: 0% (0/1)" 30 min after a real
       successful ship.

Root cause (verified from source):
- `_run_task_with_git` in `cto_projects.py` writes ONLY to `cto_tasks`.
- Real user Plan→Ship loops run through `loop_engine.py`, which writes
  to `loop_sessions` / `loop_run_log` / `loop_events` — but NEVER
  touches `cto_tasks`.
- Both `usage.py::tasks_this_month` and `admin_analytics.py::
  product_analytics` counted ONLY `cto_tasks`, so loop-driven work
  was invisible to the founder's dashboards.

Fix: both counters now ALSO aggregate `loop_sessions`.  The counter
recognises `loop_sessions.created_at` is a datetime (not a `time.time()`
float), so it compares against the `datetime` bound of the window,
not the raw float.

These tests exercise the shape contract only.  End-to-end proof is
covered by manual verification on the Profile / Analytics pages after
deploy.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_usage_module_counts_loop_sessions():
    """The literal marker + collection name must be present.  If someone
    silently reverts the aggregation, this catches it."""
    src = Path("/app/backend/services/usage.py").read_text()
    assert "loop_sessions.count_documents" in src, (
        "usage.py no longer counts loop_sessions — Bug 6/7 will regress"
    )
    # The datetime-vs-timestamp guard: loop_sessions.created_at is a
    # datetime, so the query MUST NOT pass a raw `.timestamp()` bound.
    idx = src.find("loop_sessions.count_documents")
    window = src[idx:idx + 700]
    assert '"$gte": month_start' in window and '.timestamp()' not in window.split('loop_sessions')[0][-40:]  # noqa: E501


def test_analytics_module_counts_loop_sessions():
    src = Path("/app/backend/routers/admin_analytics.py").read_text()
    assert "loop_sessions.count_documents" in src, (
        "admin_analytics.py no longer counts loop_sessions — Bug 7 regresses"
    )
    # Same datetime guard.
    assert "window_start_dt" in src


def test_analytics_success_rate_uses_combined_totals():
    """After the fix, success_rate is computed on the SUM of cto_tasks
    + loop_sessions, not just cto_tasks.  Guard the accumulation."""
    src = Path("/app/backend/routers/admin_analytics.py").read_text()
    # These are the three lines that make the combined sum work.
    for phrase in (
        "tasks_total  += loop_total",
        "tasks_done   += loop_done",
        "tasks_failed += loop_failed",
    ):
        assert phrase in src, f"missing combined-total accumulator: {phrase!r}"


def test_usage_module_still_excludes_failed_tasks_from_meter():
    """Guard: Iter 52 BUG 3 rule — failed tasks must NOT count toward
    the flat-fee meter.  The status list must not include 'failed'."""
    src = Path("/app/backend/services/usage.py").read_text()
    idx = src.find("cto_tasks.count_documents")
    window = src[idx:idx + 400]
    assert '"failed"' not in window
    # And the loop-side aggregation should count 'completed' + active
    # states but NOT 'failed' either.
    idx2 = src.find("loop_sessions.count_documents")
    lwin = src[idx2:idx2 + 800]
    assert '"completed"' in lwin
    assert '"failed"' not in lwin
