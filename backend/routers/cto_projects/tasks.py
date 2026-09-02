"""
routers/cto_projects/tasks.py — AUREM CTO Projects.
Task submission/enqueue (HTTP + programmatic Mode C trigger), status/
scan read, retry (with checkpoint/resume), project task list, and the
live SSE progress stream.

Split from the former monolithic routers/cto_projects.py on
2026-09-08 (responsibility-based extraction, no logic change). Uses
`_pkg.<name>` for anything patched at the package level by the
existing test suite (`current_dev`, `get_db`, `require_db`,
`assert_has_budget`, `assert_has_task_budget`, `_run_task`) — see
preview.py's module docstring for why.
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.usage import is_founder_email
from services.ambiguity_gate import is_ambiguous_task as _is_ambiguous_task
from services.cto_projects_helpers import _task_queues, _emit

import routers.cto_projects as _pkg
from . import router, PENDING_EDITS_TTL_S

logger = logging.getLogger(__name__)


class TaskBody(BaseModel):
    project_id: str
    task: str
    files: List[str] = []
    context: str = ""
    auto_deploy: bool = False
    maxx_mode: bool = False     # iter 40: enable Two-Agent (DeepSeek + Claude review)


async def _enqueue_cto_task(
    user_id: str,
    project_id: Optional[str],
    task_text: str,
    bg: Optional[BackgroundTasks] = None,
    maxx_mode: bool = False,
) -> dict:
    """Iter 46 — programmatic Mode C trigger.

    Used by both /tasks/submit (HTTP) AND the chat-router Mode D→C handoff
    (so "yes fix it" actually queues a real ship task, not a friendly reply).

    Returns:
        {"ok": True, "task_id": "...", "project_id": "..."} on success
        {"ok": False, "reason": "no_project"|"no_pat"|"out_of_budget"} otherwise
    """
    import asyncio as _asyncio
    db = _pkg.get_db()
    if db is None:
        return {"ok": False, "reason": "no_db"}

    proj = None
    if project_id and project_id != "home":
        proj = await db.cto_projects.find_one(
            {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
        )
    if not proj:
        # Fall back to the user's most recently used project.
        proj = await db.cto_projects.find_one(
            {"user_id": user_id},
            sort=[("last_task", -1), ("created_at", -1)],
        )
    if not proj:
        return {"ok": False, "reason": "no_project"}

    task_id = f"t_{uuid.uuid4().hex[:12]}"
    await db.cto_tasks.insert_one({
        "task_id": task_id,
        "project_id": proj["project_id"],
        "user_id": user_id,
        "task": task_text,
        "files": [], "context": "",
        "status": "queued", "steps": [], "commit_sha": None,
        "result": None, "error": None,
        "maxx_mode": bool(maxx_mode),
        "source": "chat_handoff",
        "created_at": time.time(),
    })
    if proj.get("preview_url"):
        from services.preview_capture import capture_before_snapshot_for_task
        if bg is not None:
            bg.add_task(capture_before_snapshot_for_task, db, proj["project_id"], user_id, task_id, proj.get("preview_url"))
        else:
            _asyncio.create_task(capture_before_snapshot_for_task(
                db, proj["project_id"], user_id, task_id, proj.get("preview_url"),
            ))
    from services.pat_vault import get_repo_token_or_error
    user_token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if not user_token:
        await db.cto_tasks.update_one(
            {"task_id": task_id},
            {"$set": {"status": "failed",
                      "error": f"GitHub App auth failed ({_auth_err}): {_auth_detail}",
                      "completed_at": time.time()}},
        )
        return {"ok": False, "reason": "no_pat",
                "task_id": task_id, "project_id": proj["project_id"]}

    # 2026-06 PAT-removal — the old PAT-decrypt-fallback advisory block
    # is gone; get_repo_token is App-only and fails closed upstream.

    if bg is not None:
        bg.add_task(_pkg._run_task, task_id, proj, task_text, [], "",
                    user_token, bool(maxx_mode))
    else:
        # No BackgroundTasks in this caller — fire-and-forget asyncio task.
        _asyncio.create_task(_pkg._run_task(
            task_id, proj, task_text, [], "",
            user_token, bool(maxx_mode),
        ))
    return {"ok": True, "task_id": task_id, "project_id": proj["project_id"]}


@router.post("/tasks/submit")
async def submit_task(
    request: Request,
    body: TaskBody,
    bg: BackgroundTasks,
    authorization: str = Header(None),
) -> dict:
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    # 2026-09-08 — REORDERED (Wave-1 baseline triage real-bug fix):
    # request-validity (does this project_id even belong to this user?)
    # must be settled BEFORE the content-quality ambiguity check below.
    # Previously the ambiguity gate ran first and could return a 200
    # "needs_clarification" response for a request against a project
    # that doesn't exist/isn't owned by this user — masking what should
    # be a clean 400/403/404. Ownership is a cheap DB lookup, so there's
    # no cost reason to defer it past the (also free) ambiguity check.
    #
    # Iter 212m-169 — Build BINContext at task entry.  This does ALL of:
    #   • ownership check (find_one {project_id, user_id})   → 403
    #   • repo_owner / repo_name / branch pull               → 400
    #   • PAT decrypt via services/vault HKDF                → 403
    # so the previous separate ownership guard + inline PAT decrypt are
    # now redundant.  We STILL fetch the full project doc for
    # downstream metadata (repo_index_summary etc. are excluded by
    # the projection below) but only AFTER ownership is proven.
    # Iter 212m-169/170 — Build ORAContext at task entry.
    from services.ora_context import build_ora_context
    _is_fnd_task = bool(
        me.get("is_admin") or me.get("is_unlimited")
        or (me.get("tier") == "founder")
        or is_founder_email(me.get("email"))
    )
    bin_ctx = await build_ora_context(
        user_id=me["user_id"],
        project_id=body.project_id,
        db=db,
        is_founder=_is_fnd_task,
    )
    proj = await db.cto_projects.find_one(
        {"project_id": body.project_id, "user_id": me["user_id"]},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        # Should never trigger — build_bin_context already 403'd — but
        # keep as defence-in-depth for legacy code paths.
        raise HTTPException(404, "Project not found")

    # 2026-08-25 — Priority 2 (ambiguity-gate): a task with no concrete
    # target (no file path, no quoted string, and only vague/generic
    # phrasing) is too under-specified to act on blindly — an LLM will
    # guess at what to change rather than ask, which is exactly the
    # failure mode this session's incident review flagged. Checked
    # BEFORE any budget/rate-limit spend so a vague task costs nothing.
    if _is_ambiguous_task(body.task):
        return {
            "ok": False,
            "needs_clarification": True,
            "message": (
                "That's a bit broad for me to act on safely — could you "
                "name a specific file, page, or feature? For example "
                "\"fix the signup form validation in Signup.jsx\" instead "
                "of \"fix it\"."
            ),
        }
    # Iter 50.1 — Founders skip per-IP rate-limit. They run audits, ship
    # tests, retry tasks in bursts — locking them out at 10/min defeats
    # the whole "founder = full access" rule.
    _is_unlimited = bool(me.get("is_unlimited")) or me.get("tier") == "founder"
    if not _is_unlimited:
        from services.rate_limiter import check_rate_limit_async, client_ip_from_request
        if not await check_rate_limit_async(f"submit:{client_ip_from_request(request)}", 10):
            raise HTTPException(429, "Rate limit exceeded: 10 code tasks/min/IP")
    # THING 1 — hard-stop token enforcement. Raises HTTP 402 if the user has
    # spent their plan_limit + any admin-granted bonus. The AI is NEVER
    # called and no row is written to `cto_tasks`.
    await _pkg.assert_has_budget(me["user_id"])

    # Tier-based monthly task cap (free=10, starter=50, pro/team/founder
    # unlimited). Single source of truth — MONTHLY_TASK_LIMITS in
    # services/usage.py. Replaces the iter-45 free-only counter.
    await _pkg.assert_has_task_budget(me["user_id"])

    # Tier-based feature gate — Maxx mode requires Pro / Team / Founder.
    if body.maxx_mode:
        from services.subscription_tiers import can_use_feature
        if not can_use_feature(me.get("tier"), "maxx_mode"):
            raise HTTPException(403, {
                "error": "feature_locked",
                "feature": "maxx_mode",
                "current_tier": me.get("tier", "free"),
                "upgrade_url": "/settings#pricing",
                "message": (
                    "Maxx mode (Claude reviewer) is a Pro feature. "
                    "Upgrade at auremcto.com/settings to enable it."
                ),
            })

    task_id = f"t_{uuid.uuid4().hex[:12]}"
    await db.cto_tasks.insert_one({
        "task_id": task_id, "project_id": body.project_id,
        "user_id": me["user_id"], "task": body.task,
        "files": body.files, "context": body.context,
        "status": "queued", "steps": [], "commit_sha": None,
        "result": None, "error": None,
        "maxx_mode": bool(body.maxx_mode),
        "created_at": time.time(),
    })
    if proj.get("preview_url"):
        from services.preview_capture import capture_before_snapshot_for_task
        bg.add_task(capture_before_snapshot_for_task, db, body.project_id, me["user_id"], task_id, proj.get("preview_url"))
    # 2026-08-24 · Guard 22 — funnel event: task_submitted (idempotent
    # via one-shot flag). Closes the "connected but Recent Tasks: no
    # data yet" blind spot — cto_tasks already tracks every individual
    # attempt's status, this just marks the FIRST time a user reaches
    # this stage for the aggregate activation-funnel view. Distinct
    # from first_task_shipped (loop_engine.py), which only fires on a
    # SUCCESSFUL ship — a user who submits but never ships was
    # previously invisible between repo_selected and shipped_code.
    try:
        _stamped_sub = await db.dev_users.find_one_and_update(
            {"user_id": me["user_id"], "first_task_submitted_at": {"$exists": False}},
            {"$set": {"first_task_submitted_at": time.time()}},
            projection={"_id": 0, "user_id": 1},
        )
        if _stamped_sub:
            from services.signup_guards import emit_funnel_event
            await emit_funnel_event(
                db, user_id=me["user_id"], event_type="task_submitted",
                metadata={"project_id": body.project_id, "task_id": task_id},
            )
    except Exception as _fne:
        logger.debug("task_submitted funnel emit failed: %r", _fne)
    # Iter 212m-169 — PAT comes from bin_ctx (already decrypted +
    # validated), no more independent _decrypt_pat call.
    user_token = bin_ctx.pat
    bg.add_task(_pkg._run_task, task_id, proj, body.task, body.files, body.context,
                user_token, bool(body.maxx_mode))
    return {"ok": True, "task_id": task_id}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, authorization: str = Header(None)) -> dict:
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    t = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": me["user_id"]}, {"_id": 0}
    )
    if not t:
        raise HTTPException(404, "Task not found")
    # Iter 388g — Personal Track gate on the inline diff-view payload.
    # `edited_files` (unified-diff hunks with dual gutter numbers) is
    # dev-facing only. Personal Track users get the plain task view
    # WITHOUT the structured diff — matches the ORA_DIFF_VIEW_SPEC
    # non-goal ("Do NOT touch the AUREM CTO user chat" for non-devs).
    try:
        _track = str((me or {}).get("track") or "").lower()
        if _track.startswith("personal"):
            t.pop("edited_files", None)
    except Exception:
        pass
    return {"ok": True, "task": t}


@router.get("/tasks/{task_id}/scan")
async def get_task_scan(
    task_id: str,
    authorization: str = Header(None),
) -> dict:
    """Iter 167 — return the post-task regex scan for a completed task.

    Scan is populated by the worker right after `status=done`, so the
    frontend polls this endpoint for up to ~10s after the task finishes.
    Returns `{ok, status, scan}` where `scan` is null if no issues found.
    """
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    t = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": me["user_id"]},
        {"_id": 0, "post_scan": 1, "status": 1},
    )
    if not t:
        raise HTTPException(404, "Task not found")
    return {
        "ok":     True,
        "status": t.get("status"),
        "scan":   t.get("post_scan"),
    }


@router.post("/tasks/{task_id}/retry")
async def retry_task(
    task_id: str,
    bg: BackgroundTasks,
    authorization: str = Header(None),
) -> dict:
    """Iter 36: re-queue a FAILED task as a brand-new task with the same
    payload. We don't mutate the old row — easier audit + the user can
    see what error the original hit. Returns the new `task_id`."""
    me = await _pkg.current_dev(authorization)
    await _pkg.assert_has_budget(me["user_id"])
    await _pkg.assert_has_task_budget(me["user_id"])
    db = _pkg.require_db()
    old = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": me["user_id"]}
    )
    if not old:
        raise HTTPException(404, "Task not found")
    if old.get("status") != "failed":
        raise HTTPException(400,
                            f"Only failed tasks can be retried "
                            f"(current: {old.get('status')})")

    proj = await db.cto_projects.find_one(
        {"project_id": old["project_id"], "user_id": me["user_id"]},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Parent project not found")

    # 2026-08-27 — orphaned-task fix: mint the GitHub App token BEFORE
    # inserting the new task doc. Previously the insert happened first —
    # if the token mint then failed (e.g. revoked installation), the
    # caller got a 403 but a `queued` task doc was left behind forever
    # with no execution and no cleanup. Failing fast here means a 403
    # never creates any DB record at all.
    from services.pat_vault import get_repo_token_or_error
    user_token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if not user_token:
        raise HTTPException(403, f"GitHub App auth failed ({_auth_err}): {_auth_detail}")

    new_task_id = "t_" + uuid.uuid4().hex[:12]
    _maxx = bool(old.get("maxx_mode", False))
    # 2026-08-27 — checkpoint/resume Phase 2: if the failed task already
    # has fresh (TTL-bounded) saved edits from a prior successful
    # generation, carry them forward so `_pkg._run_task` can skip
    # regenerating them entirely. Guarded by `saved_at` age AND — since
    # `pending_edits` is only ever meaningful for the EXACT task text
    # + file set it was generated for, both of which `retry_task`
    # always copies verbatim from `old` below — no separate content
    # fingerprint check is needed; this endpoint never lets a caller
    # change the task text before retrying.
    resume_edits = None
    _pe = old.get("pending_edits")
    if _pe and _pe.get("edits") and _pe.get("saved_at"):
        _saved_at = _pe["saved_at"]
        if not isinstance(_saved_at, datetime):
            _saved_at = None
        if _saved_at is not None:
            if _saved_at.tzinfo is None:
                _saved_at = _saved_at.replace(tzinfo=timezone.utc)
            _age_s = (datetime.now(timezone.utc) - _saved_at).total_seconds()
            if 0 <= _age_s <= PENDING_EDITS_TTL_S:
                resume_edits = _pe
    # Pattern #1 fix from RECURRING_ISSUES.md — the AI failed last time for a
    # reason. Carry that reason forward in the new task's context so the
    # model sees what to avoid. Without this, retry produces the exact same
    # output (especially for "empty file body" rejections).
    prev_err = (old.get("error") or "").strip()
    prev_steps = old.get("steps") or []
    # Surface last error step text too — often more specific than the
    # top-level error field (e.g. per-file Vanguard rejection list).
    last_err_step = next(
        (s.get("step", "") for s in reversed(prev_steps)
         if s.get("status") in ("error", "fail")),
        "",
    )
    augmented_context = old.get("context", "")
    failure_signals = [s for s in (prev_err, last_err_step) if s]
    if failure_signals:
        augmented_context = (
            (augmented_context + "\n\n" if augmented_context else "")
            + "Previous attempt failed:\n"
            + "\n".join(f"  • {s[:300]}" for s in failure_signals)
            + "\n\nDo NOT repeat that failure. If a file body was rejected as "
              "empty, write the FULL implementation (classes, functions, "
              "actual logic) — not just a docstring or `pass`."
        )
    await db.cto_tasks.insert_one({
        "task_id":      new_task_id,
        "user_id":      me["user_id"],
        "project_id":   old["project_id"],
        "task":         old.get("task", ""),
        "files":        old.get("files", []),
        "context":      augmented_context,
        "status":       "queued",
        "maxx_mode":    _maxx,
        "created_at":   time.time(),
        "retry_of":     task_id,
        "resumed_from_checkpoint": bool(resume_edits),
        "steps":        [{"step": f"🔁 retry of {task_id}"
                                  + (" (with failure context)" if failure_signals else "")
                                  + (" — reusing saved edits, skipping regeneration"
                                     if resume_edits else ""),
                          "status": "info",
                          "ts": time.time()}],
    })
    bg.add_task(
        _pkg._run_task,
        new_task_id, proj, old.get("task", ""),
        old.get("files", []), augmented_context, user_token, _maxx,
        resume_edits,
    )
    return {"ok": True, "task_id": new_task_id, "retry_of": task_id,
            "carried_failure_context": bool(failure_signals),
            "resumed_from_checkpoint": bool(resume_edits)}




@router.get("/tasks/project/{project_id}")
async def project_tasks(project_id: str, authorization: str = Header(None)) -> dict:
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    tasks = await db.cto_tasks.find(
        {"project_id": project_id, "user_id": me["user_id"]}, {"_id": 0}
    ).sort("created_at", -1).limit(20).to_list(20)
    return {"ok": True, "tasks": tasks}


@router.get("/tasks/{task_id}/stream")
async def task_stream(task_id: str, authorization: str = Header(None)):
    """SSE stream of live worker steps for a single task (Iter 73).

    Used by the chat bubble's <TaskLiveTape> to render a terminal-style
    progress feed: reading files… → thinking… → committing → done.

    Closes on a `done` or `fail` frame, or after 5 min wall-clock.
    Sends a keepalive `ping` every 2 s of silence so the EventSource
    on slow networks doesn't auto-retry."""
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    task = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": me["user_id"]}, {"_id": 0}
    )
    if not task:
        raise HTTPException(404, "task not found")

    async def generate():
        q = _task_queues.get(task_id)
        if q is None:
            q = asyncio.Queue(maxsize=256)
            _task_queues[task_id] = q

        def _build_synthetic_handoff(t: dict) -> dict:
            """Iter 212m-10 — when the worker finishes before the SSE
            client connects (common for 1-2s commits), the queue is
            empty and we synthesise only a `done` frame from Mongo.
            Without a `task_handoff` frame the floating LiveTaskPopup
            never latches on, so we mint one here too."""
            return {
                "type": "task_handoff",
                "step": "task_handoff",
                "pct": None,
                "ts": time.time(),
                "kind": "task_handoff",
                "project_id": t.get("project_id") or "",
                "sha": (t.get("commit_sha") or "")[:7],
                "source": "task_stream_synthetic",
            }

        # If the task already terminated before the client connected,
        # emit a single synthetic final frame and exit immediately.
        if task.get("status") in ("done", "failed"):
            if task["status"] == "done":
                yield f"data: {json.dumps(_build_synthetic_handoff(task))}\n\n"
            final = {
                "type": "done" if task["status"] == "done" else "fail",
                "step": (f"Done — {task.get('commit_sha','')[:7]}"
                         if task["status"] == "done"
                         else f"Failed — {(task.get('error') or '')[:80]}"),
                "pct": 100,
                "ts": time.time(),
            }
            yield f"data: {json.dumps(final)}\n\n"
            _task_queues.pop(task_id, None)
            return

        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                event = await asyncio.wait_for(q.get(), timeout=2.0)
            except asyncio.TimeoutError:
                yield "data: {\"type\":\"ping\"}\n\n"
                # Poll Mongo — covers the case where the worker finished
                # but its terminal _emit was dropped (e.g. queue full or
                # process restart).
                t = await db.cto_tasks.find_one(
                    {"task_id": task_id}, {"_id": 0, "status": 1,
                                            "commit_sha": 1, "error": 1,
                                            "project_id": 1},
                )
                if t and t.get("status") in ("done", "failed"):
                    if t["status"] == "done":
                        yield f"data: {json.dumps(_build_synthetic_handoff(t))}\n\n"
                    final = {
                        "type": "done" if t["status"] == "done" else "fail",
                        "step": (f"Done — {t.get('commit_sha','')[:7]}"
                                 if t["status"] == "done"
                                 else f"Failed — {(t.get('error') or '')[:80]}"),
                        "pct": 100,
                        "ts": time.time(),
                    }
                    yield f"data: {json.dumps(final)}\n\n"
                    _task_queues.pop(task_id, None)
                    return
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in ("done", "fail"):
                _task_queues.pop(task_id, None)
                return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


