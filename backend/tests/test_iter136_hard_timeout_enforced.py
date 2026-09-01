"""Iter 136 regression: HARD_TIMEOUT_S must fire even when the
`_ticker()` task is still emitting tick events.

Bug: the main chat_stream loop used
  ev = await asyncio.wait_for(q.get(), timeout=deadline_remaining)
to detect a missed wall-clock budget. But the in-process `_ticker()`
puts a tick frame into `q` every 0.6s, so `q.get()` always returned
something before `wait_for` could time out. Result: HARD_TIMEOUT_S
(default 150s) was silently ignored — users reported "thinking · 500s"
chat bubbles stuck past the configured budget.

Fix: after each q.get() success, explicitly check `monotonic() >=
deadline_at` AND only treat ticks (not result/mode/error events) as
candidates for the timeout branch.

This test reads the source and verifies the explicit deadline check is
present alongside the wait_for call. A full E2E would require booting
the FastAPI app with a fake LLM worker; the regression we keep hitting
is the missing guard, so a source-level pin is enough.
"""
from __future__ import annotations

import pathlib
import re


# 2026-09-08 StreamState refactor — the deadline-check loop moved
# from stream.py's inline gen() while-loop into watchdog.py's
# `consume_worker_queue()` (mechanical move, same source lines).
CHAT_ROUTER = pathlib.Path(__file__).resolve().parents[1] / "routers" / "chat" / "watchdog.py"


def _src() -> str:
    return CHAT_ROUTER.read_text(encoding="utf-8")


def test_deadline_explicit_check_present() -> None:
    """The chat_stream main loop must compare monotonic() against
    deadline_at AFTER consuming an event, not rely on wait_for alone."""
    src = _src()
    # The loop body must contain both `deadline_at` and an explicit
    # `_t.monotonic() >= deadline_at` comparison.
    assert "deadline_at = _t.monotonic() + HARD_TIMEOUT_S" in src, (
        "deadline_at initialisation missing"
    )
    # The explicit deadline check Iter 136 introduced.
    assert "_t.monotonic() >= deadline_at" in src, (
        "explicit post-get deadline check is missing — wait_for alone is "
        "insufficient because _ticker() keeps q non-empty."
    )


def test_tick_only_triggers_timeout_past_deadline() -> None:
    """Real result/mode/error events arriving slightly past deadline
    must NOT be discarded — only ticks should trigger the timeout branch.

    2026-08-23 — extended to allow an additional `(_past_soft_deadline
    and _is_tick)` OR-clause (added for the proxy-safe soft timeout),
    as long as every clause still requires `_is_tick` — i.e. the core
    invariant this test pins (only ticks trigger the timeout branch,
    never a real result/error event) still holds.
    """
    src = _src()
    # The check must specifically guard on the tick type.
    block = re.search(
        r"_past_deadline\s*=.*?\n.*?_is_tick\s*=.*?\n"
        r"(?:.*?\n)*?"
        r"\s*if ev is None or \(_past_deadline and _is_tick\)"
        r"((?: or \([a-zA-Z_]+ and _is_tick\))*):",
        src,
        re.DOTALL,
    )
    assert block is not None, (
        "deadline check must only trigger on a tick past deadline, not "
        "on any event past deadline (would discard real results)."
    )


# Iter 212m-26: `test_ship_shortcut_has_hard_timeout` removed along
# with the entire ship-shortcut feature. The user demanded the auto-
# ship path be deleted so the manual "🚀 Ship via CTO" button is the
# ONLY way to ship. The general deadline pins above (per chat turn,
# tick-vs-event) remain the active timeout regression suite.
