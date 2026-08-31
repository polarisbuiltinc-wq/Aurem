"""
tests/test_timeout_diagnosis_2026_09_02.py

Root-cause investigation + FIX (2026-09-02, connect-flow-refinement
round, item #4) for the "Loop status error - timeout of 60000ms
exceeded" symptom the founder hit while testing a real code edit.

FINDING (diagnosed first, fixed same round): `services/local_tools.py
::_run_syntax_check` calls `subprocess.run()`. It used to run
SYNCHRONOUSLY (no `asyncio.to_thread`) from inside `async def
write_repo_file`, which froze the whole event loop for its duration --
every OTHER concurrent request on that worker (including a
lightweight `GET /loop/{id}/status` poll, a single Mongo read) could
not be scheduled until the blocking call returned. Per-language
timeouts are up to 10s (py/js) or 15s (ts/tsx) EACH; a real multi-file
edit gates each file in sequence, so the compounded block could reach
the 60s range the frontend's blanket axios timeout cuts off at.

FIX: the call is now offloaded via `asyncio.to_thread` (same function,
same args, same per-language timeouts -- just moved off the event
loop). `test_t_timeout_diagnosed_syntax_gate_blocks_event_loop` below
now proves the OPPOSITE of last round's finding: a concurrent tick is
no longer starved once the call goes through `asyncio.to_thread`.
`test_write_repo_file_offloads_syntax_gate_to_a_thread` and the two
end-to-end tests in `test_event_loop_not_starved_2026_09_02.py` prove
the fix at the real `write_repo_file` call site.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch


def test_t_timeout_diagnosed_syntax_gate_blocks_event_loop():
    """With the fix (asyncio.to_thread), running the (mocked,
    sleep-based) subprocess call directly no longer starves a
    concurrent asyncio task when the call is properly offloaded --
    this proves the offload mechanism itself is sound. (The un-fixed,
    directly-blocking case that used to fail this class of assertion
    is documented in PRD.md's 2026-09-02 entry.)"""
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

    async def run_gate_offloaded():
        with patch("subprocess.run", side_effect=_slow_subprocess_run):
            await asyncio.to_thread(
                _run_syntax_check, content="x = 1\n",
                file_path="f.py", ext=".py",
            )

    async def main():
        t = asyncio.ensure_future(ticker())
        await asyncio.sleep(0.01)  # let the ticker start first
        await run_gate_offloaded()
        await t

    asyncio.run(main())

    # Offloaded via asyncio.to_thread -- the ticker should NOT show a
    # large starved gap (contrast with the un-fixed direct-call case).
    assert max(tick_gaps) < 0.15, (
        f"ticker starved for {max(tick_gaps):.3f}s even with "
        f"asyncio.to_thread -- offload may not be working"
    )


def test_write_repo_file_offloads_syntax_gate_to_a_thread():
    """Source-level confirmation: the real fix landed at the actual
    call site inside write_repo_file (not just proven in isolation
    above)."""
    import inspect
    from services.local_tools import write_repo_file
    src = inspect.getsource(write_repo_file)
    assert "await asyncio.to_thread(\n            _run_syntax_check" in src \
        or "await asyncio.to_thread(_run_syntax_check" in src, (
            "write_repo_file's syntax gate is no longer offloaded via "
            "asyncio.to_thread -- the 2026-09-02 fix may have regressed"
        )

