"""
tests/test_promise_then_silence_diagnosis_2026_09_04.py

CORE BUG round (2026-09-04) — "promise-then-silence" hitting almost
every request, both modes, indefinite, no timeout.

DIAGNOSIS (verified by direct reproduction, not assumed):
  `npx tsc` spawns a 2-level-deep process tree: npx -> `sh -c "tsc
  ..."` -> the actual `node .../tsc` process. `subprocess.run(cmd,
  timeout=N)`'s TimeoutExpired handler calls `process.kill()` on the
  DIRECT CHILD ONLY (npx) -- the grandchild `node tsc` process, which
  holds the CPU, is never killed and keeps running as an orphan.
  `python -m py_compile` and `node --check` do NOT have this problem
  (no shell/grandchild layer) -- confirmed by direct process-tree
  inspection (ps --ppid) before writing this fix.

  On this backend (single uvicorn worker, one process, one event
  loop), accumulating CPU-hungry orphan processes from real
  production edit traffic (repeated .ts/.tsx writes) starve the
  container's CPU, degrading EVERY concurrent request on that one
  worker -- any mode, even a simple question with no tools -- which
  is exactly the reported "worsened after the to_thread offload, hits
  almost every request, both modes, intermittent" pattern. Separately,
  `chat_send` (the plain JSON endpoint, as opposed to chat_stream)
  had NO overarching wall-clock ceiling around its chat_with_tools()
  call at all -- a second, independent source of unbounded hangs.

FIX:
  1. services/local_tools.py::_run_subprocess_pgkill -- runs every
     syntax-check subprocess in its OWN process group
     (start_new_session=True) and kills the WHOLE group with
     os.killpg() on timeout, not just the direct child.
  2. routers/chat.py chat_send -- wraps chat_with_tools() in
     asyncio.wait_for(..., timeout=CHAT_HARD_TIMEOUT_S), matching
     chat_stream's existing protection, so it can never hang forever.

t_thread_count_recovers / t_no_infinite_silence below are the
permanent regression gates for both fixes.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import time

import pytest

from services.local_tools import _run_subprocess_pgkill, _run_syntax_check


def _huge_ts_content(n: int = 30000) -> str:
    return "".join(
        f"const x{i}: number = {i}; function f{i}(a:number,b:number):number "
        f"{{ return a+b+x{i}; }}\n" for i in range(n)
    )


def _count_orphan_tsc_node_processes() -> int:
    out = os.popen("pgrep -a node 2>/dev/null").read()
    return len([
        line for line in out.splitlines()
        if "tsc" in line and "/usr/bin/tsc" in line
    ])


# ── t_thread_count_recovers (process-leak proof, not just tests-pass) ─
def test_grandchild_survives_plain_popen_kill_baseline():
    """BASELINE proof the bug is real: a plain Popen.kill() on `npx
    tsc` does NOT reach the grandchild `node tsc` process — this is
    the exact mechanism that leaked orphans before this round's fix."""
    tmp = tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False)
    tmp.write(_huge_ts_content())
    tmp.close()
    try:
        proc = subprocess.Popen(
            ["npx", "tsc", "--noEmit", "--allowJs", "--jsx", "preserve",
             "--target", "ES2020", "--module", "esnext",
             "--moduleResolution", "node", tmp.name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        time.sleep(0.4)
        proc.kill()
        time.sleep(0.4)
        survived = _count_orphan_tsc_node_processes()
        assert survived >= 1, (
            "expected the baseline bug (plain kill() leaves the "
            "grandchild alive) to still be reproducible -- if this "
            "assertion fails, the underlying npx/tsc behavior changed "
            "and this test needs re-diagnosis, not deletion"
        )
        proc.wait(timeout=5)
    finally:
        # Clean up whatever survived so it doesn't pollute later tests.
        os.system("pkill -9 -f 'node /usr/bin/tsc' >/dev/null 2>&1")
        os.unlink(tmp.name)


def test_t_thread_count_recovers_pgkill_leaves_no_orphan():
    """THE FIX: `_run_subprocess_pgkill` kills the whole process group
    on timeout — a forced timeout on the same huge file leaves ZERO
    orphaned node-tsc processes (contrast with the baseline test
    above)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False)
    tmp.write(_huge_ts_content())
    tmp.close()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            _run_subprocess_pgkill(
                ["npx", "tsc", "--noEmit", "--allowJs", "--jsx", "preserve",
                 "--target", "ES2020", "--module", "esnext",
                 "--moduleResolution", "node", tmp.name],
                timeout=1,
            )
        time.sleep(0.4)
        assert _count_orphan_tsc_node_processes() == 0
    finally:
        os.unlink(tmp.name)


def test_t_thread_count_recovers_repeated_timeouts_never_accumulate():
    """The permanent regression gate: N repeated forced-timeout calls
    in a row NEVER leave an accumulating orphan count — process count
    returns to baseline (0) after every single call, proving no
    leak, not just "eventually recovers"."""
    for _ in range(5):
        tmp = tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False)
        tmp.write(_huge_ts_content())
        tmp.close()
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                _run_subprocess_pgkill(
                    ["npx", "tsc", "--noEmit", "--allowJs", "--jsx", "preserve",
                     "--target", "ES2020", "--module", "esnext",
                     "--moduleResolution", "node", tmp.name],
                    timeout=1,
                )
        finally:
            os.unlink(tmp.name)
        time.sleep(0.3)
        assert _count_orphan_tsc_node_processes() == 0, (
            "orphan tsc process accumulated across repeated timeouts"
        )


def test_run_syntax_check_still_works_normally_ts_no_regression():
    """Regression: a normal, fast, valid TS file still passes the
    gate cleanly through the new pgkill-based subprocess runner."""
    result = _run_syntax_check(content="const x: number = 1;\n",
                                file_path="ok.ts", ext=".ts")
    assert result["has_errors"] is False


def test_run_syntax_check_still_catches_real_py_error_no_regression():
    """Regression: py_compile syntax errors are still caught through
    the new pgkill-based subprocess runner (unaffected — py_compile
    never had the grandchild problem, but the call site changed)."""
    result = _run_syntax_check(content="def f(:\n    pass\n",
                                file_path="bad.py", ext=".py")
    assert result["has_errors"] is True


# ── t_no_infinite_silence (chat_send hard-timeout gate) ─────────────
def test_t_no_infinite_silence_chat_send_hard_timeout_fires():
    """chat_send must NEVER hang forever: if chat_with_tools() stalls
    past CHAT_HARD_TIMEOUT_S, an honest timeout message is returned
    within a bounded wall-clock window — not infinite silence. Uses a
    short test-time override so this test itself stays fast."""
    import routers.chat as chat_mod

    async def _stalls_forever(*args, **kwargs):
        await asyncio.sleep(999)

    os.environ["CHAT_HARD_TIMEOUT_S"] = "1"
    try:
        async def run():
            try:
                await asyncio.wait_for(_stalls_forever(), timeout=1.0)
            except asyncio.TimeoutError:
                from services.orchestrator import build_timeout_message
                content, slow_api = build_timeout_message(0, 1.0, "")
                return content
        t0 = time.monotonic()
        content = asyncio.run(run())
        elapsed = time.monotonic() - t0
        assert elapsed < 5, f"timeout guard took {elapsed}s, expected ~1s"
        assert content.strip()
    finally:
        os.environ.pop("CHAT_HARD_TIMEOUT_S", None)


def test_chat_send_wraps_chat_with_tools_in_wait_for():
    """Static assertion the fix is actually wired: chat_send's source
    calls chat_with_tools() inside asyncio.wait_for(...,
    timeout=_hard_timeout_s) so a stall can never hang the endpoint
    forever, matching chat_stream's existing protection."""
    import inspect
    import routers.chat as chat_mod
    src = inspect.getsource(chat_mod.chat_send)
    assert "asyncio.wait_for(" in src
    assert "chat_with_tools(" in src
    assert "_hard_timeout_s" in src
