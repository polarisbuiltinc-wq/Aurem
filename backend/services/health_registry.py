"""
services/health_registry.py — Unified Health Registry (Feb 2026)

Single source of truth for admin cockpit + notification bell +
per-page tiles. Every "is this thing healthy?" question in the
admin surface must go through this module.

## 3-state contract (mandatory)

    green — check ran, everything passed
    red   — check ran, FAILED (a real problem needing attention)
    gray  — check could not run (missing config / credentials /
            setup). NOT a failure — a "not-set-up-yet" signal.

Never collapse gray into red. A missing GITHUB_ACTIONS_TOKEN is
gray; a live GitHub Actions run that failed is red. These are
categorically different signals to the founder.

## check_fn contract

Each registered check_fn is an `async def` that returns:

    {
        "status":     "green" | "red" | "gray",
        "detail":     str,          # short human-readable line
        "checked_at": iso8601 str,
    }

check_fn MUST call REAL underlying mechanisms (existing guard
handlers, real integration health probes, real infra probes). No
hardcoded returns. No mocks. If the real mechanism fails to load
or raises, the wrapper catches it and returns `red` with the
exception detail — because "our own health check crashed" IS a
real problem.

## Registration

Categories:
    "guard"       — the 15 existing /admin/qa/guardN endpoints,
                    exposed via in-process adapter functions.
    "integration" — Stripe, GitHub OAuth, OpenRouter, Vanguard-CI
                    ingest, etc.
    "infra"       — DB, LLM breakers, supervised tasks,
                    CI-vs-local drift, deploy-vs-push staleness.

Register via `register_check(id, name, category, check_fn)` at
module-import time (see services/health_checks.py).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Type alias
CheckFn = Callable[[], Awaitable[dict]]


class HealthCheck:
    """Registry entry. Holds metadata + the callable check_fn."""

    __slots__ = ("id", "name", "category", "check_fn")

    def __init__(self, id: str, name: str, category: str, check_fn: CheckFn):
        if category not in ("guard", "integration", "infra"):
            raise ValueError(
                f"category must be guard|integration|infra, got {category!r}"
            )
        self.id = id
        self.name = name
        self.category = category
        self.check_fn = check_fn


# Module-level registry. Populated once at boot when
# services/health_checks.py is imported.
_REGISTRY: dict[str, HealthCheck] = {}


def register_check(id: str, name: str, category: str, check_fn: CheckFn) -> None:
    """Register a health check under a stable id.

    Idempotent — re-registering the same id replaces the entry (this
    matters for the pytest reload case). All registered check_fns are
    invoked in parallel by the aggregator, so keep each fast.
    """
    if not id or not name:
        raise ValueError("register_check: id and name are required")
    if not asyncio.iscoroutinefunction(check_fn):
        raise ValueError(
            f"register_check({id!r}): check_fn must be `async def`"
        )
    _REGISTRY[id] = HealthCheck(id=id, name=name, category=category, check_fn=check_fn)


def all_checks() -> list[HealthCheck]:
    """Snapshot of every registered check (stable order for tests)."""
    return sorted(_REGISTRY.values(), key=lambda c: (c.category, c.id))


def get_check(id: str) -> Optional[HealthCheck]:
    return _REGISTRY.get(id)


def count_by_category() -> dict[str, int]:
    out: dict[str, int] = {}
    for c in _REGISTRY.values():
        out[c.category] = out.get(c.category, 0) + 1
    return out


# ─────────────────────────────────────────────────────────────
# Adapter helpers used by the guard-adapter functions.
# Each returns a well-formed check-result dict.
# ─────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def result_green(detail: str) -> dict:
    return {"status": "green", "detail": detail, "checked_at": _now_iso()}


def result_red(detail: str) -> dict:
    return {"status": "red", "detail": detail, "checked_at": _now_iso()}


def result_gray(detail: str) -> dict:
    return {"status": "gray", "detail": detail, "checked_at": _now_iso()}


async def run_check_safely(check: HealthCheck) -> dict:
    """Invoke a registered check_fn with catch-all exception handling.

    If the check raises, we return RED (not gray) — a crashing health
    check IS a real problem. `detail` includes the exception type +
    message so the founder can debug.
    """
    try:
        result = await asyncio.wait_for(check.check_fn(), timeout=8.0)
    except asyncio.TimeoutError:
        return result_red(f"check timed out after 8s")
    except Exception as exc:   # noqa: BLE001
        logger.warning(
            "[health-registry] check %r raised: %r", check.id, exc
        )
        return result_red(f"check crashed: {type(exc).__name__}: {exc}")
    # Validate shape (fail loud if a check_fn returns something wrong).
    if not isinstance(result, dict) or "status" not in result:
        return result_red(
            f"check returned invalid shape: {type(result).__name__}"
        )
    status = result.get("status")
    if status not in ("green", "red", "gray"):
        return result_red(f"check returned bad status: {status!r}")
    # Fill in missing fields with sensible defaults.
    result.setdefault("detail", "")
    result.setdefault("checked_at", _now_iso())
    return result


__all__ = [
    "HealthCheck",
    "register_check",
    "all_checks",
    "get_check",
    "count_by_category",
    "run_check_safely",
    "result_green",
    "result_red",
    "result_gray",
]
