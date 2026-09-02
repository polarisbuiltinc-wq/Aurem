"""
tests/test_git_worker_event_loop_offload_2026_09_09.py

PRODUCTION DEPLOY BUG (founder-reported): a burst of nginx
"upstream timed out ... GET /health" errors during deploy — K8s'
own health probe timing out against a TRIVIAL, zero-I/O endpoint
(`healthz_root()` in main.py, just `return {"ok": True}`) means the
whole backend event loop was blocked/wedged for many seconds, not
that endpoint having a bug.

ROOT CAUSE: `services/cto_projects_helpers.py::_sh()` — a synchronous
`subprocess.run()` git helper (up to 120s timeout per call) — was
called BARE (no `asyncio.to_thread`) from `_run_task_with_git`
(`routers/cto_projects/worker_git.py`) and `_run_rollback_with_git`
(`routers/cto_projects/rollback.py`). Both run via FastAPI
`BackgroundTasks`, which execute on the SAME event loop as every
other request — so a real git clone/push blocked that loop, and
every concurrent request (including the trivial /health probe)
stalled until the git call returned. This is the exact same bug
class the 2026-09-02 "Event-loop-blocking FIX" round already fixed
for `write_repo_file`'s syntax gate and `deploy_readiness.py`, but
explicitly left `_sh()`'s ~14 call sites un-fixed (PRD 2026-09-02
part 3, "FLAGGED, deferred, NOT fixed this round").

FIX (2026-09-09): every `_pkg._sh(...)` call site in both files is
now `await asyncio.to_thread(_pkg._sh, ...)`. Two checks below:
  1. Source-lock — no bare (non-offloaded) `_pkg._sh(` call remains
     in either file (regression guard against a future edit
     accidentally re-introducing a blocking call).
  2. Functional — the actual offload primitive (`asyncio.to_thread`
     wrapping a slow `_sh`-shaped subprocess call) does not starve a
     concurrent heartbeat, same proof style as
     `test_event_loop_not_starved_2026_09_02.py`.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from unittest.mock import patch

_PKG_DIR = Path(__file__).parent.parent / "routers" / "cto_projects"


def _bare_sh_calls(src: str) -> list[str]:
    """Return every `_pkg._sh(` call NOT immediately preceded by
    `asyncio.to_thread(` on the same or previous ~40 chars."""
    bad = []
    for m in re.finditer(r"_pkg\._sh\(", src):
        window = src[max(0, m.start() - 40): m.start()]
        if "asyncio.to_thread(" not in window:
            bad.append(src[max(0, m.start() - 20): m.start() + 20])
    return bad


def test_t_no_bare_blocking_sh_call_in_rollback_worker():
    src = (_PKG_DIR / "rollback.py").read_text()
    bad = _bare_sh_calls(src)
    assert not bad, f"bare (non-offloaded) _pkg._sh( call(s) in rollback.py: {bad}"


def test_t_no_bare_blocking_sh_call_in_git_worker():
    src = (_PKG_DIR / "worker_git.py").read_text()
    bad = _bare_sh_calls(src)
    assert not bad, f"bare (non-offloaded) _pkg._sh( call(s) in worker_git.py: {bad}"


def test_t_asyncio_to_thread_offload_does_not_starve_heartbeat():
    """Proves the actual mechanism: a slow `_sh`-shaped subprocess
    call, run via `asyncio.to_thread`, never blocks a concurrently
    scheduled heartbeat coroutine on the same event loop — the exact
    property that was missing before this fix."""
    from services.cto_projects_helpers import _sh

    tick_gaps = []

    async def heartbeat():
        last = time.monotonic()
        for _ in range(10):
            await asyncio.sleep(0.02)
            now = time.monotonic()
            tick_gaps.append(now - last)
            last = now

    def _slow_subprocess_run(*a, **k):
        time.sleep(0.3)  # stands in for a real git clone/push
        class _R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return _R()

    async def _run():
        with patch("services.cto_projects_helpers.subprocess.run", side_effect=_slow_subprocess_run):
            hb = asyncio.ensure_future(heartbeat())
            await asyncio.sleep(0.01)
            r = await asyncio.to_thread(_sh, ["git", "status"], Path("/tmp"))
            await hb
            return r

    result = asyncio.run(_run())
    assert result.returncode == 0
    assert max(tick_gaps) < 0.15, (
        f"heartbeat starved for {max(tick_gaps):.3f}s during the "
        f"offloaded _sh() call — asyncio.to_thread offload is broken"
    )
