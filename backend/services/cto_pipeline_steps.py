"""
services/cto_pipeline_steps.py — 2026-09-08 Phase 2 (lighter split).

Shared safety-critical pipeline stages used by BOTH
`routers.cto_projects._run_task_via_api` and `_run_task_with_git`.

History: these 3 gates (hallucination / syntax / lint) originally
lived only inline inside `_run_task_via_api`. The 2026-09-08 safety
fix hoisted them to module-level functions inside `cto_projects.py`
so both workers could call the SAME gates in the SAME order (closing
a real safety hole — the git-binary worker had none of these three).
This follow-up moves those same 3 functions (zero logic change) into
this standalone file, so `cto_projects.py` doesn't have to carry their
~230 lines directly. The workers themselves stay in `cto_projects.py`
— only these already-shared gate functions moved.

`ai_sys` (the system prompt used for the two auto-retry nudges) is an
explicit parameter rather than a module import, to avoid a circular
import between this file and `routers.cto_projects` (which is where
`_AI_SYS` is built).
"""
from __future__ import annotations

from typing import Optional

from services.llm import call_llm
from services.cto_projects_helpers import _log, _emit, _retry, _hallucination_reasons


def _check_js_syntax(filepath: str, content: str) -> Optional[str]:
    """Return an error string for invalid JS/TS/JSX/TSX, None if
    valid OR if neither esbuild nor node is installed (so we
    degrade gracefully — never block on missing parsers).

    Tries `esbuild` first (understands JSX/TSX/decorators), then
    falls back to `node --check` (structural-only, no JSX)."""
    import subprocess as _sp_mod
    import tempfile as _tf
    import os as _os
    suffix = _os.path.splitext(filepath)[1] or ".js"
    tmp = None
    try:
        with _tf.NamedTemporaryFile(
            suffix=suffix, mode="w", delete=False, encoding="utf-8",
        ) as fh:
            fh.write(content)
            tmp = fh.name
        # 1) esbuild — proper JSX-aware parser
        try:
            r = _sp_mod.run(
                ["esbuild", tmp, "--bundle=false", "--log-level=error"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0 and (r.stderr or "").strip():
                return (r.stderr or r.stdout).strip()[:300]
            return None
        except FileNotFoundError:
            pass   # fall through to node
        # 2) node --check — structural only, no JSX support
        r = _sp_mod.run(
            ["node", "--check", tmp],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return (r.stderr or r.stdout).strip()[:200]
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None
    finally:
        if tmp:
            try:
                _os.unlink(tmp)
            except Exception:
                pass


def _syntax_errors(blocks: dict[str, str]) -> list[str]:
    """Syntax validation — catch broken code before it reaches GitHub.
    AST check for Python, `node --check`/esbuild for JS/TS, `json`
    for JSON. Shared by both task workers (see `_run_syntax_gate`)."""
    out: list[str] = []
    for _spath, _scontent in blocks.items():
        if not _scontent or not _scontent.strip():
            continue
        if _spath.endswith(".py"):
            try:
                import ast as _ast
                _ast.parse(_scontent)
            except SyntaxError as _se:
                out.append(
                    f"{_spath}: SyntaxError line {_se.lineno or 1}: {_se.msg}"
                )
        elif _spath.endswith((".js", ".jsx", ".ts", ".tsx")):
            js_err = _check_js_syntax(_spath, _scontent)
            if js_err:
                out.append(f"{_spath}: {js_err}")
        elif _spath.endswith(".json"):
            try:
                import json as _jparse
                _jparse.loads(_scontent)
            except Exception as _je:
                out.append(f"{_spath}: invalid JSON: {_je}")
    return out


async def _run_hallucination_gate(task_id: str, edits: dict, contents: dict,
                                   user_msg: str, ai_sys: str) -> tuple[dict, Optional[str]]:
    """P0-4a HALLUCINATION GATE (pre-push, before Vanguard) — SHARED by
    both task workers. If the model "rewrote" a file we actually read
    but kept almost none of its real lines, the content is invented.
    One targeted retry re-injects the REAL file; still bad -> returns
    an error string (caller must fail the task, never commit).
    Returns (possibly-updated edits, error_or_None)."""
    _hallu = _hallucination_reasons(edits, contents)
    if not _hallu:
        return edits, None
    await _log(task_id,
               "🚧 hallucination gate tripped — regenerating with the "
               "real file re-injected:\n  - " + "\n  - ".join(_hallu),
               "warning")
    _real_blob = "\n\n".join(
        f"FILE: {p}\n```\n{contents.get(p) or contents.get(p.lstrip('./'))}\n```"
        for p in edits
        if (contents.get(p) or contents.get(p.lstrip("./"))))
    _h_nudge = (
        "Your previous edit did NOT match the real file — it "
        "invented code that does not exist. Below is the REAL, "
        "current content of each file. Re-apply the requested "
        "change as a MINIMAL modification of this exact content. "
        "Preserve every existing line unless the task requires "
        "changing it.\n\n" + _real_blob
    )
    reply3 = await _retry(
        lambda: call_llm(
            messages=[{"role": "user",
                       "content": user_msg + "\n\n" + _h_nudge}],
            system=ai_sys, max_tokens=3500, temperature=0.0,
        ),
        what="AI hallucination-retry", task_id=task_id,
    )
    from services.llm_file_parser import parse_file_blocks as _pfb
    _edits2 = _pfb(reply3)
    if _edits2:
        edits = _edits2
    _hallu = _hallucination_reasons(edits, contents)
    if _hallu:
        err = ("AI kept producing content that does not match the "
               "real file (refusing to push):\n  - "
               + "\n  - ".join(_hallu))
        return edits, err
    await _log(task_id, "✅ hallucination-retry produced a faithful edit",
               "success")
    return edits, None


async def _run_syntax_gate(task_id: str, edits: dict,
                            user_msg: str, ai_sys: str) -> tuple[dict, Optional[str]]:
    """Syntax validation — catch broken code before it reaches GitHub.
    SHARED by both task workers. Runs `_syntax_errors`, auto-retries
    once with the exact errors fed back, then returns an error string
    on persistent failure (caller must fail the task, never commit).
    Returns (possibly-updated edits, error_or_None)."""
    await _emit(task_id, "Validating generated code…", kind="phase_verify", pct=78)
    syntax_errors = _syntax_errors(edits)
    if syntax_errors:
        await _log(
            task_id,
            "⚠️ Syntax errors detected — auto-regenerating with feedback",
            "warning",
        )
        await _emit(task_id, "Syntax errors found — regenerating…", pct=79)
        _syn_nudge = (
            "Your previous response generated code with these syntax "
            "errors:\n  - "
            + "\n  - ".join(syntax_errors)
            + "\n\nRegenerate the COMPLETE corrected files in the same "
            "FILE: <path>\\n```\\n…\\n``` format. Ensure every function, "
            "class, and block is properly closed. Do not truncate any "
            "file. Output ALL files you edited, not just the broken ones."
        )
        reply3 = await _retry(
            lambda: call_llm(
                messages=[{"role": "user",
                           "content": user_msg + "\n\n" + _syn_nudge}],
                system=ai_sys, max_tokens=3500, temperature=0.0,
            ),
            what="AI syntax-fix auto-retry", task_id=task_id,
        )
        new_edits: dict[str, str] = {}
        from services.llm_file_parser import parse_file_blocks
        new_edits.update(parse_file_blocks(reply3))
        if new_edits:
            edits = {**edits, **new_edits}
        syntax_errors = _syntax_errors(edits)
    if syntax_errors:
        _err_str = "\n  - ".join(syntax_errors[:3])
        err = (
            "Generated code has syntax errors after auto-retry:\n  - "
            + _err_str
            + "\n\nTry rephrasing: specify the exact function or class "
            "to change, or split the work into smaller files."
        )
        return edits, err
    return edits, None


async def _run_lint_gate(task_id: str, edits: dict) -> tuple[dict, dict, Optional[str]]:
    """Design-linter gate — auto-fix safe issues (console.log,
    transition: all), then block on any critical finding (hardcoded
    secrets, etc). SHARED by both task workers. Returns
    (possibly-auto-fixed edits, lint_result, blocking_error_or_None)."""
    try:
        from services.design_linter import lint_file_blocks, auto_fix_blocks
        edits, fix_log = auto_fix_blocks(edits)
        if fix_log:
            total_fixes = sum(len(v) for v in fix_log.values())
            await _log(task_id, f"🛠️ Auto-fixed {total_fixes} safe lint issue(s) across {len(fix_log)} file(s)", "info")
        lint_result = lint_file_blocks(edits)
    except Exception:
        lint_result = {"blocked": False, "issues": [], "warnings": [], "summary": ""}
    if lint_result.get("blocked"):
        await _log(task_id, f"⛔ Linter blocked the commit: {len(lint_result['issues'])} critical issue(s)", "error")
        for reason in lint_result.get("block_reasons", [])[:5]:
            await _log(task_id, f"  • {reason}", "error")
        err = ("Design linter blocked commit:\n" + lint_result.get("summary", ""))[:2000]
        return edits, lint_result, err
    if lint_result.get("warnings"):
        await _log(task_id, f"⚠️ Linter: {len(lint_result['warnings'])} non-blocking warning(s)", "warning")
    return edits, lint_result, None
