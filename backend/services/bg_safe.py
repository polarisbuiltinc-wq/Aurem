"""
services/bg_safe.py — BG-task exception safety net (Feb 2026)

FastAPI's `BackgroundTasks.add_task(fn, …)` runs `fn` AFTER the
response is sent.  Any exception `fn` raises is caught by FastAPI's
runner but not surfaced to the user (response is already sent) and
— critically — is only visible in stdout logs. It does NOT reach
Sentry unless the task body opts in.

This module provides `safe_bg(fn)`, a decorator that:
  · Wraps sync OR async callables.
  · Catches any Exception raised inside the task.
  · Logs a full stack trace via the standard logger.
  · Ships the exception to Sentry with `kind=bg_task_failed` +
    the function name as a tag, so grouping stays clean.
  · Swallows the exception so nothing propagates upward.

Usage pattern (in a router):
    from services.bg_safe import safe_bg

    @safe_bg
    async def _finalize_upgrade(user_id: str, session_id: str):
        ...   # DB writes, third-party calls, whatever

    background_tasks.add_task(_finalize_upgrade, user_id, sess.id)

Founder brief 2026-02-08: "catch errors at every boundary — API
route, background jobs, webhook receivers, payment callbacks.
Every uncaught error is a stack trace waiting to leak."
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _capture(exc: Exception, fn_name: str) -> None:
    """Ship to Sentry with structured tags. Silent if Sentry isn't
    initialised so this file is import-safe in tests."""
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("kind", "bg_task_failed")
            scope.set_tag("bg_fn", fn_name)
            sentry_sdk.capture_exception(exc)
    except Exception:
        # Sentry init failure MUST NOT re-raise into the task runner —
        # that would defeat the whole point of this wrapper.
        pass


def safe_bg(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorate a BG-task callable so it can never crash the runner.

    Works transparently for both `def` and `async def` functions —
    inspects the callable at wrap time and returns a matching
    wrapper. Exceptions inside the task body are logged + shipped
    to Sentry, then swallowed.
    """
    fn_name = getattr(fn, "__name__", "<anonymous>")

    if asyncio.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def _async_wrapper(*args: Any, **kwargs: Any) -> None:
            try:
                await fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "bg_task_failed: %s(%s)", fn_name,
                    _args_summary(args, kwargs))
                _capture(exc, fn_name)
        return _async_wrapper

    @functools.wraps(fn)
    def _sync_wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "bg_task_failed: %s(%s)", fn_name,
                _args_summary(args, kwargs))
            _capture(exc, fn_name)
    return _sync_wrapper


def _args_summary(args: tuple, kwargs: dict) -> str:
    """Compact one-line summary of task args for the log line —
    never dumps full payloads (may contain secrets), just types + a
    truncated repr for user_id-ish shortcuts."""
    parts: list[str] = []
    for a in args[:4]:
        s = repr(a)
        parts.append(s if len(s) <= 60 else s[:57] + "...")
    if len(args) > 4:
        parts.append(f"...+{len(args) - 4} more")
    for k in list(kwargs)[:4]:
        v = repr(kwargs[k])
        parts.append(f"{k}={v if len(v) <= 40 else v[:37] + '...'}")
    return ", ".join(parts)
