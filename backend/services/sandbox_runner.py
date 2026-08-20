"""
services/sandbox_runner.py — Sandboxed code execution via e2b.dev.

Lets ORA validate generated code BEFORE committing.  This is the gap vs
Cursor / Claude Code / Augment — they all run tests in a sandbox.

Env: `E2B_API_KEY` — without it everything silently no-ops so the worker
pipeline NEVER blocks on missing config.  Get a free key at e2b.dev
(100 sandbox-hours/month on the free tier).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _key() -> str:
    return os.environ.get("E2B_API_KEY", "")


async def run_python_check(code: str, filename: str = "check.py",
                           timeout: int = 15) -> dict:
    """Run a Python snippet inside e2b. Returns {ok, stdout, stderr, skipped}."""
    if not _key():
        return {"ok": True, "skipped": True, "reason": "E2B_API_KEY not set"}
    try:
        import asyncio
        # +10s wait_for margin over the e2b-side timeout so a hung
        # sandbox call can't leak the worker thread indefinitely.
        return await asyncio.wait_for(
            asyncio.to_thread(_run_python_check_blocking, code, timeout),
            timeout=timeout + 10,
        )
    except asyncio.TimeoutError:
        logger.warning("sandbox_runner.run_python_check timed out after %ds", timeout + 10)
        return {"ok": True, "skipped": True, "reason": "e2b sandbox call timed out"}
    except ImportError:
        return {"ok": True, "skipped": True, "reason": "e2b-code-interpreter not installed"}
    except Exception as e:
        logger.warning("sandbox_runner.run_python_check failed: %r", e)
        return {"ok": True, "skipped": True, "reason": str(e)}


def _run_python_check_blocking(code: str, timeout: int) -> dict:
    """2026-08-20 — the actual e2b work, run off the event loop via
    `asyncio.to_thread()`.

    Root cause fixed here: `e2b_code_interpreter.Sandbox` (the SYNC
    client — logs as `e2b.api.client_sync`) was previously called
    directly inside an `async def` with no executor. Sandbox
    create/run/kill round-trips over the network to e2b.app, ~1-2s
    typical, blocked the ENTIRE asyncio event loop for that whole
    duration — starving every other coroutine including the FastAPI
    `/health` handler. Production evidence: nginx logs showed
    `upstream timed out (110) ... GET /health` clustering exactly
    around e2b sandbox create/execute/delete log lines. This function
    now runs on a worker thread so a slow sandbox call only blocks
    itself, never the loop.
    """
    from e2b_code_interpreter import Sandbox          # type: ignore
    sbx = Sandbox.create(api_key=_key(), timeout=timeout)
    try:
        ex = sbx.run_code(code)
    finally:
        try:
            sbx.kill()
        except Exception:
            pass
    has_err = bool(getattr(ex, "error", None))
    # New SDK exposes logs.stdout/stderr lists separately from results.
    logs = getattr(ex, "logs", None)
    stdout_lines = list(getattr(logs, "stdout", None) or [])
    stderr_lines = list(getattr(logs, "stderr", None) or [])
    results_str = "\n".join(str(r) for r in (getattr(ex, "results", None) or []))
    stdout = (("".join(stdout_lines)) + ("\n" + results_str if results_str else "")).strip()
    return {
        "ok":        not has_err,
        "stdout":    stdout[:2000],
        "stderr":    (str(ex.error)[:1000] if has_err else "\n".join(stderr_lines)[:1000]),
        "exit_code": 1 if has_err else 0,
        "skipped":   False,
    }


async def run_tests_in_sandbox(test_files: dict[str, str],
                               source_files: dict[str, str],
                               timeout: int = 30) -> dict:
    """Run pytest over the generated source + test files in e2b."""
    if not _key():
        return {"ok": True, "skipped": True, "reason": "E2B_API_KEY not set"}
    try:
        import asyncio
        return await asyncio.wait_for(
            asyncio.to_thread(
                _run_tests_in_sandbox_blocking, test_files, source_files, timeout,
            ),
            timeout=timeout + 15,   # extra margin for pip install + pytest run
        )
    except asyncio.TimeoutError:
        logger.warning("sandbox_runner.run_tests_in_sandbox timed out after %ds", timeout + 15)
        return {"ok": True, "skipped": True, "reason": "e2b sandbox call timed out"}
    except ImportError:
        return {"ok": True, "skipped": True, "reason": "e2b-code-interpreter not installed"}
    except Exception as e:
        logger.warning("sandbox_runner.run_tests_in_sandbox failed: %r", e)
        return {"ok": True, "skipped": True, "reason": str(e)}


def _run_tests_in_sandbox_blocking(test_files: dict[str, str],
                                    source_files: dict[str, str],
                                    timeout: int) -> dict:
    """Blocking e2b work for run_tests_in_sandbox — see
    _run_python_check_blocking's docstring for why this runs off the
    event loop via asyncio.to_thread()."""
    from e2b_code_interpreter import Sandbox          # type: ignore
    sbx = Sandbox.create(api_key=_key(), timeout=timeout)
    try:
        for p, c in {**source_files, **test_files}.items():
            sbx.files.write(p, c)
        sbx.run_code(
            "import subprocess;"
            " subprocess.run(['pip','install','pytest','-q'], check=False)"
        )
        result = sbx.run_code(
            "import subprocess;"
            " r=subprocess.run(['python','-m','pytest','-v','--tb=short'],"
            " capture_output=True,text=True);"
            " print(r.stdout); print('---STDERR---'); print(r.stderr)"
        )
    finally:
        try:
            sbx.kill()
        except Exception:
            pass
    logs = getattr(result, "logs", None)
    stdout_lines = list(getattr(logs, "stdout", None) or [])
    results_str = "\n".join(str(r) for r in (getattr(result, "results", None) or []))
    out = ("".join(stdout_lines) + "\n" + results_str).strip()
    return {
        "ok":      " FAILED" not in out and " ERROR" not in out,
        "passed":  out.count(" PASSED"),
        "failed":  out.count(" FAILED"),
        "errors":  out.count(" ERROR"),
        "output":  out[:3000],
        "skipped": False,
    }


async def validate_generated_files(edits: dict[str, str],
                                   task_description: str = "") -> dict:
    """Main entry point called by the worker pipeline.

    - If pytest test files are present alongside source, run pytest.
    - Else run a structural syntax import-check on the Python files.
    - Anything else (JS/TS/CSS) is left to the in-pipeline node/esbuild gate.
    Always non-blocking — returns {ok: True, skipped: True} on missing
    env or any exception.
    """
    if not _key() or not edits:
        return {"ok": True, "skipped": True}
    py = {p: c for p, c in edits.items() if p.endswith(".py")}
    if not py:
        return {"ok": True, "skipped": True, "reason": "no python files"}
    tests  = {p: c for p, c in py.items() if "test_" in p or p.endswith("_test.py")}
    source = {p: c for p, c in py.items() if p not in tests}
    checks: dict = {}
    if tests and source:
        checks["tests"] = await run_tests_in_sandbox(tests, source)
    elif source:
        snippet = (
            "import ast\nerrs = []\n"
            + "\n".join(
                f"try: ast.parse({c!r})\n"
                f"except SyntaxError as e: errs.append(({p!r}, str(e)))"
                for p, c in list(source.items())[:5]
            )
            + "\nprint('ERRORS:', errs)\n"
        )
        checks["syntax"] = await run_python_check(snippet)
    all_ok = all(r.get("ok", True) for r in checks.values())
    return {"ok": all_ok, "checks": checks, "skipped": not checks}
