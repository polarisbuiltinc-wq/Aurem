"""
services/supervised_tasks.py — Session F: background-task supervisor
====================================================================

Problem
-------
The FastAPI lifespan startup spawns 20+ `asyncio.create_task(cron_x())`
calls for long-lived background jobs (integration health probes, daily
digest, backup cron, LongCat reprobe, etc.). If any of those raise
silently or return early, the task is garbage-collected and the cron
just vanishes — no log, no alert, no incident row. The next founder-
visible symptom is often days later ("why hasn't the backup run in
72 hours?").

Solution
--------
A thin `supervise(coro, name=...)` wrapper that:
    1. Wraps a coroutine in `asyncio.create_task` and holds a
       registry reference so it's never GC'd out from under us.
    2. Installs a done-callback that inspects termination:
       • `CancelledError`     → normal shutdown, silent
       • Any other exception  → Guard 20 `open_incident()` + logger
       • Normal completion of a supposed-long-lived cron
                              → treated as unexpected death,
                                same Guard 20 treatment
    3. Exposes a `health_snapshot()` for `/api/health` so a UI can
       show "3 supervised crons dead" without a full DB scan.

Scope
-----
This module ONLY covers the long-lived crons registered via
`supervise(...)`. Per-request fire-and-forget `create_task` calls
(logging inserts, timing records, background heals) legitimately
die with the request they were spawned from — those are NOT the
target of supervision.

Zero mocks: incidents are written to the real `db.incidents`
collection via the existing `services/incident_log.open_incident`
entry-point, same one Guard 20 uses for every other RED alert.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
import traceback
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# Registry of currently-supervised tasks. Keyed by `name` so a second
# `supervise(...)` call with the same name is a no-op if the original
# is still alive (idempotent restart).
_SUPERVISED: dict[str, asyncio.Task] = {}

# Postmortem for tasks that died. Cleared when the task is re-supervised.
# Kept small (last-death-per-name) so `/api/health` payloads stay small.
_DEAD: dict[str, dict] = {}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


async def _record_incident(db_getter: Optional[Callable], name: str,
                            reason: str, detail: str) -> None:
    """Best-effort — a supervisor bug must never crash the pod."""
    if db_getter is None:
        return
    try:
        db = db_getter()
        if db is None:
            return
        from services.incident_log import open_incident
        await open_incident(
            db,
            guard="G-F1-supervised-task",
            title=f"Supervised task '{name}' died: {reason}",
            detail=detail[:800],
            source_key=f"supervised_task:{name}",
            severity="critical",
            follow_up=(
                "Restart the pod or re-invoke the cron manually. "
                "Root cause: check pod logs for the traceback."
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[supervise] incident write failed for %s: %r",
                       name, e)


def _on_task_done(task: asyncio.Task, *, name: str,
                  db_getter: Optional[Callable],
                  long_lived: bool) -> None:
    """Done-callback invoked by the event loop when the task terminates.

    Runs on the loop thread; must NOT block. Any DB work is scheduled
    onto a new fire-and-forget task (the ONE unsupervised task in this
    module — bootstrap paradox: the supervisor's own recorder cannot
    be supervised).
    """
    now = time.time()
    if task.cancelled():
        # Normal shutdown or explicit cancellation — silent.
        _SUPERVISED.pop(name, None)
        return

    exc = task.exception()
    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _DEAD[name] = {
            "died_at": now,
            "died_at_iso": _iso(now),
            "reason": "exception",
            "exc_type": type(exc).__name__,
            "exc_msg": str(exc)[:200],
        }
        logger.error("[supervise] task %s died with exception: %r\n%s",
                     name, exc, tb)
        try:
            asyncio.ensure_future(_record_incident(
                db_getter, name,
                reason=f"exception:{type(exc).__name__}",
                detail=tb,
            ))
        except RuntimeError:
            # Loop is closing — nothing to do.
            pass
    elif long_lived:
        # A long-lived cron returned normally — that's unexpected.
        _DEAD[name] = {
            "died_at": now,
            "died_at_iso": _iso(now),
            "reason": "silent_completion",
            "exc_type": None,
            "exc_msg": None,
        }
        logger.error(
            "[supervise] long-lived task %s completed silently — "
            "this is an unexpected termination for a cron", name,
        )
        try:
            asyncio.ensure_future(_record_incident(
                db_getter, name,
                reason="silent_completion",
                detail=(
                    f"Task '{name}' was registered as a long-lived cron "
                    f"but its coroutine returned normally at "
                    f"{_iso(now)}. Cron functions must loop forever; "
                    "a normal return means the cron will never run again "
                    "until the pod restarts."
                ),
            ))
        except RuntimeError:
            pass
    else:
        # A one-shot task completed cleanly — expected, no-op.
        pass

    _SUPERVISED.pop(name, None)


def supervise(coro: Awaitable, *, name: str,
              db_getter: Optional[Callable] = None,
              long_lived: bool = True) -> asyncio.Task:
    """Wrap `coro` in a supervised `asyncio.Task`.

    Args:
        coro:       Coroutine to schedule.
        name:       Human-readable identifier — used as the incident
                    `source_key` and shown in `/api/health`. Must be
                    stable across pod restarts so recurring failures
                    dedupe correctly in the incidents collection.
        db_getter:  Zero-arg callable returning the Motor db handle.
                    Optional — if None or returns None, the supervisor
                    still logs the failure but skips Guard 20 write.
        long_lived: True for crons (loop-forever). False for one-shot
                    startup tasks (indexes, backfills) where normal
                    return is EXPECTED and NOT an incident-worthy event.

    Returns:
        The `asyncio.Task` — caller can await/cancel it directly.
        A reference is also held in `_SUPERVISED` so the task is
        never garbage-collected before it completes.
    """
    # If a previous task with this name is still alive, don't spawn a
    # duplicate — return the existing one. Idempotent for defensive
    # callers.
    existing = _SUPERVISED.get(name)
    if existing is not None and not existing.done():
        return existing

    task = asyncio.create_task(coro, name=name)
    _SUPERVISED[name] = task
    task.add_done_callback(
        functools.partial(_on_task_done, name=name,
                          db_getter=db_getter, long_lived=long_lived)
    )
    return task


def health_snapshot() -> dict:
    """Return a small dict suitable for embedding in `/api/health`.

    Shape:
        {
          "supervised_count": int,
          "alive":            [name, ...],
          "dead":             [{"name": ..., "died_at_iso": ...,
                                "reason": ..., "exc_type": ...}, ...],
        }

    `dead` only contains tasks that died since pod start (each name
    keeps its LATEST death — re-supervising clears the row).
    """
    alive = [n for n, t in _SUPERVISED.items() if not t.done()]
    dead  = [{"name": n, **info} for n, info in _DEAD.items()]
    return {
        "supervised_count": len(_SUPERVISED),
        "alive":            sorted(alive),
        "dead":             dead,
    }


def _reset_for_tests() -> None:
    """Test-only helper: wipe registry + postmortem. Never called from
    prod code. Used by `tests/test_supervised_tasks.py` to isolate
    each test case."""
    _SUPERVISED.clear()
    _DEAD.clear()
