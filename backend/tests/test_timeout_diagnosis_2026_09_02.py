"""
tests/test_timeout_diagnosis_2026_09_02.py

Root-cause investigation (2026-09-02, connect-flow-refinement round,
item #4b) for the "Loop status error - timeout of 60000ms exceeded"
symptom the founder hit while testing a real code edit.

FINDING: `services/local_tools.py::_run_syntax_check` calls
`subprocess.run()` SYNCHRONOUSLY (no `asyncio.to_thread` / executor)
from inside `async def write_repo_file`. Python's asyncio has ONE
event loop per worker; a blocking synchronous call inside a coroutine
freezes that entire loop for its duration -- every OTHER concurrent
request on that worker (including a lightweight `GET /loop/{id}/status`
poll, which is just a single Mongo read) cannot be scheduled until the
blocking call returns. `_run_syntax_check`'s own per-language timeouts
are up to 10s (py/js) or 15s (ts/tsx) EACH; a real multi-file edit
gates each file in sequence, so the compounded block can easily reach
the 60s range the frontend's blanket axios timeout (`lib/api.js`) cuts
off at -- which is exactly the "Loop status error" the user saw.

This is a DIAGNOSIS only (per founder's explicit instruction -- the
fix is a separate follow-up, not bundled with the customer-facing
dead-end removal in this round). `t_timeout_diagnosed` proves the
blocking mechanism directly; the corresponding log line lives in
`write_repo_file` (see the 2026-09-02 comment there).
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch


def test_t_timeout_diagnosed_syntax_gate_blocks_event_loop():
    """Proves the hang mechanism: while `_run_syntax_check` runs its
    (mocked, sleep-based) subprocess call, a CONCURRENT asyncio task
    on the same loop is starved for the full duration instead of
    interleaving -- exactly what would happen to a real
    /loop/{id}/status poll running on the same worker during a real
    syntax-gated write."""
    from services.local_tools import _run_syntax_check

    class _FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def _slow_subprocess_run(*a, **k):
        time.sleep(0.3)  # stands in for a real py_compile/tsc call
        return _FakeCompleted()

    tick_gaps = []

    async def ticker():
        last = time.monotonic()
        for _ in range(15):
            await asyncio.sleep(0.02)
            now = time.monotonic()
            tick_gaps.append(now - last)
            last = now

    def run_blocking_gate():
        with patch("subprocess.run", side_effect=_slow_subprocess_run):
            _run_syntax_check(content="x = 1\n", file_path="f.py", ext=".py")

    async def main():
        t = asyncio.ensure_future(ticker())
        await asyncio.sleep(0.01)  # let the ticker start first
        run_blocking_gate()
        await t

    asyncio.run(main())

    # A tick that should land ~20ms apart got starved for ~300ms while
    # the "subprocess" ran synchronously on the same event loop --
    # proving any other concurrent request (e.g. a status poll) would
    # have been frozen for that same window.
    assert max(tick_gaps) > 0.2, (
        f"expected a >200ms starved tick proving event-loop block, "
        f"got max gap {max(tick_gaps):.3f}s -- gate may no longer be "
        f"blocking (re-check whether it was moved to asyncio.to_thread)"
    )


def test_syntax_gate_still_uses_bare_subprocess_run_not_to_thread():
    """Documents current state (not yet fixed): `_run_syntax_check`'s
    call site in `write_repo_file` has no `to_thread`/`run_in_executor`
    wrapping -- if this test starts failing, the blocking root-cause
    finding above may be stale and BUGS_LEDGER.md should be updated."""
    import inspect
    from services.local_tools import write_repo_file
    src = inspect.getsource(write_repo_file)
    call = "_run_syntax_check(content=content, file_path=path, ext=_ext)"
    assert call in src
    before_call = src.split(call)[0][-400:]
    assert "to_thread" not in before_call
    assert "run_in_executor" not in before_call
