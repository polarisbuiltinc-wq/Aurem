"""
tests/test_event_loop_not_starved_2026_09_02.py

FIX #A verification (2026-09-02): `write_repo_file`'s syntax gate now
offloads `_run_syntax_check` via `asyncio.to_thread`, so the event
loop stays free for concurrent requests (e.g. a `/loop/{id}/status`
poll) while a real (or mocked, slow) subprocess check runs.

Both tests below go through the REAL `write_repo_file` function
(no mocking of the function itself) -- a deliberate syntax error in
the submitted content makes the syntax gate block the commit and
return BEFORE any real GitHub call is attempted, so no GitHub
mocking is needed either. Only `_run_subprocess_pgkill` (the
process-group-aware subprocess runner `_run_syntax_check` calls
into, 2026-09-04) is mocked, to make the "slow tool" duration
deterministic.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

from services.local_tools import write_repo_file


class _FakeBinCtx:
    bin_id = "user-1"
    repo_owner = "octo"
    repo_name = "mine"
    branch = "main"
    pat = "fake-token"
    is_founder = False
    pid = "p1"


def _ctx():
    return {"user_id": "user-1", "project_id": "p1", "bin_ctx": _FakeBinCtx()}


def _syntax_error_args(name: str) -> dict:
    # Valid Python (passes Vanguard's own quick ast.parse check, which
    # runs BEFORE the dedicated syntax gate) -- the mocked
    # `subprocess.run` below is what makes the DEDICATED syntax gate
    # (the thing we're testing) report a failure, so write_repo_file
    # returns right after that offloaded call, before ever reaching a
    # real GitHub commit.
    return {"path": name, "content": "x = 1\n"}


class _FakeFailedCompleted:
    returncode = 1
    stdout = ""
    stderr = "SyntaxError: invalid syntax"


def _slow_subprocess_run(*a, **k):
    time.sleep(0.3)  # stands in for a real py_compile call
    return _FakeFailedCompleted()


async def test_t_event_loop_not_blocked_during_write():
    """A single write_repo_file call with a (mocked) slow syntax-check
    subprocess must NOT block a concurrently-scheduled heartbeat --
    the event loop stays free to service other requests."""
    tick_gaps = []

    async def heartbeat():
        last = time.monotonic()
        for _ in range(10):
            await asyncio.sleep(0.02)
            now = time.monotonic()
            tick_gaps.append(now - last)
            last = now

    with patch("services.local_tools._run_subprocess_pgkill", side_effect=_slow_subprocess_run):
        hb = asyncio.ensure_future(heartbeat())
        await asyncio.sleep(0.01)  # let the heartbeat start first
        result = await write_repo_file(_ctx(), _syntax_error_args("a.py"))
        await hb

    assert result.get("ok") is False
    assert result.get("error") == "syntax_gate_blocked"
    assert max(tick_gaps) < 0.15, (
        f"heartbeat starved for {max(tick_gaps):.3f}s during a single "
        f"write -- the syntax gate may not be offloaded any more"
    )


async def test_t_multifile_write_does_not_starve_poll():
    """N sequential file writes (as a real multi-file Loop edit would
    do), each hitting the (mocked) slow syntax-check subprocess, must
    NOT starve a concurrent status-poll-style task running throughout
    -- proving the fix holds for the compounding, multi-file case that
    originally produced the 60s timeout."""
    poll_gaps = []
    poll_ticks = 0

    async def status_poll():
        nonlocal poll_ticks
        last = time.monotonic()
        for _ in range(20):
            await asyncio.sleep(0.05)
            now = time.monotonic()
            poll_gaps.append(now - last)
            last = now
            poll_ticks += 1

    async def write_three_files():
        for i in range(3):
            r = await write_repo_file(_ctx(), _syntax_error_args(f"f{i}.py"))
            assert r.get("error") == "syntax_gate_blocked"

    with patch("services.local_tools._run_subprocess_pgkill", side_effect=_slow_subprocess_run):
        poll_task = asyncio.ensure_future(status_poll())
        await write_three_files()
        await poll_task

    assert poll_ticks == 20, "status poll never completed all its ticks -- it was starved out"
    assert max(poll_gaps) < 0.2, (
        f"status poll starved for {max(poll_gaps):.3f}s during the "
        f"multi-file write -- this is the exact 60s-timeout mechanism "
        f"the fix was meant to remove"
    )
