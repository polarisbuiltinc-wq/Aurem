"""
services/cto_projects_helpers.py — safe mechanical extraction from
routers/cto_projects.py (2026-08-26 coverage-floor extraction batch).

Pure/standalone helpers moved out verbatim (zero logic changes) to
shrink routers/cto_projects.py. Re-exported at the top of
`routers/cto_projects.py` so every existing bare-name call site inside
its endpoints/workers, every `from routers.cto_projects import X`, and
every `patch("routers.cto_projects.X", ...)` in the pre-existing test
suite keep working unchanged (Python re-export semantics).

NOT moved (deliberately, same posture as chat.py's `chat_stream` /
loop_engine.py's `LoopEngine`): `_run_task_via_api`, `_run_task_with_git`,
`_run_task`, `_run_rollback`, `_run_rollback_via_api`,
`_run_rollback_with_git`, `_rollback_log`, `_enqueue_cto_task` — the
actual git/API worker execution + rollback pipelines. Several of these
read the module-level `_GIT_AVAILABLE` flag and are patched directly by
existing tests at `routers.cto_projects.*`; moving them would require a
shared-mutable-flag or lazy-import workaround for no real maintainability
gain, so they stay put. `_frontend_subset`, `get_repo_token`, and
`_run_warm_agents` also stay — 3 existing tests do a literal
`open(cto_projects.py).read()` source-text check for their exact
definitions.
"""
from __future__ import annotations
import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Live progress streams (Iter 73) ──────────────────────────────────────
# In-memory per-task asyncio.Queue used to fan out worker steps to the
# /cto/tasks/{id}/stream SSE endpoint so chat bubbles render a live
# "worker tape" (reading files… thinking… committing…) instead of a
# silent spinner.  Queues are popped once the task emits a done/fail
# terminal frame, or when the stream times out (5 min wall-clock).
_task_queues: dict[str, asyncio.Queue] = {}


async def _emit(task_id: str, step: str,
                kind: str = "step", pct: Optional[int] = None,
                **extra) -> None:
    """Push one progress frame onto the task's live SSE queue.

    Non-blocking; safe to call even if no consumer is listening (the queue
    just buffers up to 256 frames then drops oldest).

    Any **extra keyword args are merged into the frame so callers can ship
    structured payloads (e.g. `agents=["backend","frontend"]` for the
    parallel-mode worker tape)."""
    if not task_id:
        return
    q = _task_queues.get(task_id)
    if q is None:
        q = asyncio.Queue(maxsize=256)
        _task_queues[task_id] = q
    frame = {"type": kind, "step": step, "pct": pct, "ts": time.time()}
    if extra:
        # Don't let callers overwrite the canonical fields.
        for k, v in extra.items():
            if k not in frame:
                frame[k] = v
    try:
        q.put_nowait(frame)
    except asyncio.QueueFull:
        # Drop the oldest frame to make room for the new one rather than
        # blocking the worker — the SSE client will see a small gap.
        try:
            q.get_nowait()
            q.put_nowait(frame)
        except Exception:
            pass


def _parse_repo(url: str) -> tuple[str, str]:
    from fastapi import HTTPException
    p = url.rstrip("/").replace(".git", "").replace("https://github.com/", "").split("/")
    if len(p) < 2:
        raise HTTPException(400, "Bad GitHub URL — expected https://github.com/owner/repo")
    return p[0], p[1]


async def _run_project_indexing(
    *, db, project_id: str, user_id: str,
    github_token: str, github_owner: str, github_repo: str, branch: str,
) -> None:
    """Background indexing wrapper for Iter 212m-75.

    Runs build_brain_v2 and writes the result to cto_projects so the
    FE polling endpoint /indexing-status can report progress.
    Errors are swallowed and persisted as `indexing_error`.
    """
    try:
        from services.project_brain import build_brain_v2
        await build_brain_v2(
            db=db, project_id=project_id, user_id=user_id,
            github_token=github_token, github_owner=github_owner,
            github_repo=github_repo, branch=branch,
        )
        await db.cto_projects.update_one(
            {"project_id": project_id, "user_id": user_id},
            {"$set": {
                "indexing_status": "ready",
                "indexed_at":      time.time(),
                "indexing_error":  None,
            }},
        )
        logger.info("project indexing complete: %s", project_id)
    except Exception as e:
        logger.warning("project indexing failed for %s: %r", project_id, e)
        await db.cto_projects.update_one(
            {"project_id": project_id, "user_id": user_id},
            {"$set": {
                "indexing_status": "error",
                "indexing_error":  str(e)[:500],
                "indexed_at":      time.time(),
            }},
        )


# Both endpoints scope to the project's connected GitHub PAT (decrypted
# from Mongo) and the project's branch. They are read-only and never
# touch the working tree on disk; everything goes through the GitHub
# REST API so no `git` binary is required.
_BROWSE_SKIP_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".cache", ".pytest_cache", ".mypy_cache",
    "coverage", ".turbo", ".vercel", ".idea", ".vscode",
}
_BROWSE_SKIP_EXTS = {
    "lock", "log", "map",
    "png", "jpg", "jpeg", "gif", "webp", "ico", "svg", "bmp",
    "mp4", "mov", "mp3", "wav", "ogg",
    "ttf", "otf", "woff", "woff2", "eot",
    "zip", "tar", "gz", "7z", "rar",
    "pdf", "exe", "dll", "so",
}
_BROWSE_MAX_FILE_BYTES = 200 * 1024  # 200 KB cap per file


def _browse_keep_path(path: str, size: int) -> bool:
    """Return True if a tree blob should appear in the browseable list."""
    if not path:
        return False
    parts = path.split("/")
    if any(p in _BROWSE_SKIP_DIRS for p in parts):
        return False
    ext = parts[-1].rsplit(".", 1)[-1].lower() if "." in parts[-1] else ""
    if ext in _BROWSE_SKIP_EXTS:
        return False
    if size and size > _BROWSE_MAX_FILE_BYTES:
        return False
    return True


def _classify_phase(step: str) -> Optional[str]:
    """Iter 168 — map a free-form step string to a coarse phase bucket
    so the live task popup can render phase chips without us having to
    touch every _log() callsite. Returns one of:
    phase_read / phase_think / phase_write / phase_verify / phase_commit
    or None if the step doesn't fit a phase (it just appears as a plain
    log line then)."""
    s = (step or "").lower()
    if any(k in s for k in (
        "📡", "📄", "reading", "fetched", "fetching",
        "cloning", "cloned", "injected", "🗂", "📋",
    )):
        return "phase_read"
    if any(k in s for k in (
        "🧠", "thinking", "plan:", "planning", "deepseek", "claude review",
    )):
        return "phase_think"
    if any(k in s for k in (
        "✏️", "💾", "writing", "regenerating", "auto-fixed", "linter",
        "validating", "sandbox",
    )):
        return "phase_write"
    if any(k in s for k in (
        "🛡", "vanguard", "verify", "verified",
    )):
        return "phase_verify"
    if any(k in s for k in (
        "🚀", "committing", "pushed", "commit", "pushing",
    )):
        return "phase_commit"
    return None


async def _log(task_id: str, step: str, status: str = "info"):
    from cto_services.db import get_db
    db = get_db()
    # Iter 168 — persist phase bucket alongside the raw step text so
    # the LiveTaskPopup can render phase chips from polled steps[].
    phase = _classify_phase(step)
    if db is not None:
        doc = {"step": step, "status": status, "ts": time.time()}
        if phase:
            doc["kind"] = phase
        await db.cto_tasks.update_one(
            {"task_id": task_id},
            {"$push": {"steps": doc}},
        )
    # Also fan out to the live SSE queue so chat bubbles can render the
    # worker tape in real time (Iter 73).  status→kind: error→fail, others→step.
    # Phase classification overrides the generic step kind when found
    # so SSE consumers can drive phase UI too.
    kind = "fail" if status == "error" else (phase or "step")
    await _emit(task_id, step, kind=kind)


async def _set_status(task_id: str, **fields):
    from cto_services.db import get_db
    db = get_db()
    if db is not None:
        # Iter 212m-12 — auto-translate failure errors into a
        # non-technical Hinglish explanation with concrete steps so
        # founders aren't staring at raw stack traces. We only run
        # the translator when the new status is `failed` AND a
        # raw error string is being set.
        if fields.get("status") == "failed" and fields.get("error"):
            try:
                # 2026-08-28 · P0 hotfix — an INTERNAL_CALL_ERROR (a bug
                # in AUREM's own calling code, e.g. a missing-argument
                # TypeError) must NEVER go through the LLM rewrite below.
                # The raw text often contains words that happen to look
                # like user-data field names (e.g. "author_email",
                # "author_name"), and the LLM has been observed
                # confidently inventing "update your profile" guidance
                # from that — blaming the user for AUREM's own bug. Use
                # the already-correct, human-reviewed catalog message
                # instead, same one loop_engine.py's _fail_ship() uses.
                if fields.get("error_code") == "INTERNAL_CALL_ERROR":
                    from core.errors import translate_error, ErrorCode
                    catalog = translate_error(ErrorCode.INTERNAL_CALL_ERROR)
                    fields["error_plain"]      = catalog["what_happened"]
                    fields["error_steps"]      = catalog["what_to_try"]
                    fields["error_suggestion"] = ""
                    fields["error_source"]     = "internal_call_error_catalog"
                else:
                    from services.error_translator import translate
                    friendly = await translate(fields["error"])
                    fields["error_plain"]      = friendly.get("plain") or ""
                    fields["error_steps"]      = friendly.get("steps") or []
                    fields["error_suggestion"] = friendly.get("suggestion") or ""
                    fields["error_source"]     = friendly.get("source") or "unknown"
            except Exception as _e:                  # noqa: BLE001
                # Translator must never block the failure write —
                # fall back to leaving only `error` populated.
                logger.warning("error_translator wedged: %r", _e)
        await db.cto_tasks.update_one({"task_id": task_id}, {"$set": fields})


def _sh(cmd: list, cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def _load_design_system() -> str:
    """Load the AUREM design-system prompt once at module import. If the
    file is missing (e.g. fresh deploy), we degrade gracefully — the
    base _AI_SYS still ships."""
    try:
        import pathlib
        p = pathlib.Path(__file__).parent.parent / "prompts" / "aurem_design_system.md"
        if p.exists():
            return "\n\n# AUREM DESIGN SYSTEM — when emitting frontend code (.jsx/.tsx/.css/.html), follow EVERY rule below:\n\n" + p.read_text()
    except Exception:
        pass
    return ""


# Patterns the verifier rejects — AI sometimes sneaks placeholders past
# the prompt. We catch them client-side BEFORE pushing to GitHub so the
# user never sees a commit that silently truncates their file.
_TRUNCATION_PATTERNS = [
    "... rest of file",
    "... existing code",
    "... unchanged",
    "...(truncated)",
    "... (truncated)",
    "// rest of file",
    "/* existing code */",
    "/* ... */",
    "# ... existing",
    "# rest of file",
    "<keep the rest",
    "<rest of file",
    "<existing code",
    "[rest of file",
    "[existing code",
    "// keep existing",
    "// ... (",
    "/* TODO: keep",
]


def _looks_truncated(path: str, body: str) -> Optional[str]:
    """Return a human reason if `body` looks like an AI-truncated edit,
    else None. Run on every FILE block before we push."""
    if not body or not body.strip():
        return "empty file body"
    low = body.lower()
    for pat in _TRUNCATION_PATTERNS:
        if pat.lower() in low:
            return f"contains placeholder '{pat}'"
    # Very short edits to non-trivial files are suspicious too — but we
    # only flag them when the body has fewer than 3 non-blank lines AND
    # the extension suggests code (not config/markdown).
    non_blank = sum(1 for ln in body.splitlines() if ln.strip())
    is_codey = path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"))
    if is_codey and non_blank < 3:
        return f"only {non_blank} non-blank lines for a code file"
    return None


# Iter 36: bulletproof retry wrapper for transient upstream failures.
# Wraps an async coroutine factory in exponential-backoff retry. Every
# failed attempt is logged to the task feed so the user sees WHAT went
# wrong, not just a silent "task failed". This is what makes Ship via
# CTO self-heal on rate-limit / 5xx / network blips instead of giving up.
async def _retry(coro_factory, *, what: str, task_id: str,
                 attempts: int = 3, base_sleep: float = 1.5):
    """Run `coro_factory()` up to `attempts` times with exp backoff
    (1.5s → 3s → 6s …). Re-raises the LAST exception if every attempt fails."""
    last_exc: Optional[Exception] = None
    for i in range(1, attempts + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            # 2026-08-25 — never surface the raw exception string in
            # the live worker tape (see root-cause note on the outer
            # handlers below). A fast, non-LLM classification is
            # enough for a mid-flight retry warning.
            from services.error_classifier import classify_error
            _safe_msg = classify_error(e)["user_message"]
            await _log(
                task_id,
                f"⏳ {what} failed (attempt {i}/{attempts}): {_safe_msg}",
                "warning",
            )
            if i < attempts:
                await asyncio.sleep(base_sleep * (2 ** (i - 1)))
    assert last_exc is not None
    raise last_exc


def _hallucination_reasons(blocks: dict, originals: dict) -> list[str]:
    """Iter 212m-177 — P0-4a. Flag proposed full-file rewrites that keep
    almost none of the REAL file's lines (hallucinated content)."""
    out: list[str] = []
    for _p, _new in blocks.items():
        _orig = originals.get(_p) or originals.get(_p.lstrip("./"))
        if not _orig:
            continue          # brand-new file — allowed
        _olines = [l.strip() for l in _orig.splitlines() if l.strip()]
        if len(_olines) < 10:
            continue
        _nset = {l.strip() for l in (_new or "").splitlines() if l.strip()}
        _kept = sum(1 for l in _olines if l in _nset)
        _ratio = _kept / len(_olines)
        if _ratio < 0.4:
            out.append(
                f"{_p} — proposed rewrite keeps only "
                f"{int(_ratio * 100)}% of the real file's lines "
                f"(likely hallucinated content)")
    return out
