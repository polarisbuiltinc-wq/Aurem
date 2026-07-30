"""Guard 17 — central retry + circuit-breaker utility (Iter 360).

THE single place for outbound-dependency retry logic. Rules
(GUARDS_CHARTER G17): exponential backoff + jitter, max retries,
per-dependency circuit breaker (open after N consecutive fails,
half-open probe after cooldown). No caller may implement its own
retry loop outside this module — existing ones are migrated to it.

Transitions are kept in an in-memory ring (last 200) and best-effort
persisted to Mongo `breaker_events` for the QA row (trip count 7d).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

KNOWN_DEPS = ("openrouter", "deepseek_direct", "groq", "github", "stripe",
              "tavily", "firecrawl", "vercel", "resend", "supabase")

_TRANSITIONS: deque[dict] = deque(maxlen=200)


class BreakerOpenError(Exception):
    """Fast-fail raised when a dependency's breaker is OPEN."""

    def __init__(self, dep: str, retry_after_s: float):
        self.dep = dep
        self.retry_after_s = round(retry_after_s, 1)
        super().__init__(
            f"circuit breaker for '{dep}' is OPEN — retry in ~{self.retry_after_s}s")


class CircuitBreaker:
    def __init__(self, dep: str, fail_threshold: int = 5, cooldown_s: float = 60.0):
        self.dep = dep
        self.fail_threshold = fail_threshold
        self.cooldown_s = cooldown_s
        self.state = "closed"                 # closed | open | half_open
        self.consecutive_fails = 0
        self.opened_at = 0.0
        self.trip_count = 0
        self.last_error = ""
        self._probe_started = 0.0
        self.total_successes = 0
        self.total_failures = 0

    # ── state machine ────────────────────────────────────────────────
    def allow(self) -> bool:
        """True if a call may proceed. open→half_open after cooldown."""
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.monotonic() - self.opened_at >= self.cooldown_s:
                self._transition("half_open", "cooldown elapsed — probe allowed")
                self._probe_started = time.monotonic()
                return True
            return False
        # half_open: one probe in flight; stale probe slot expires in 30s
        if self._probe_started and time.monotonic() - self._probe_started < 30:
            return False
        self._probe_started = time.monotonic()
        return True

    def retry_after_s(self) -> float:
        if self.state != "open":
            return 0.0
        return max(0.0, self.cooldown_s - (time.monotonic() - self.opened_at))

    def record_success(self) -> None:
        self.total_successes += 1
        self.consecutive_fails = 0
        self._probe_started = 0.0
        if self.state != "closed":
            self._transition("closed", "probe succeeded")

    def record_failure(self, reason: str = "") -> None:
        self.total_failures += 1
        self.consecutive_fails += 1
        self.last_error = (reason or "")[:300]
        self._probe_started = 0.0
        if self.state == "half_open":
            self.opened_at = time.monotonic()
            self._transition("open", f"probe failed: {self.last_error[:120]}")
        elif self.state == "closed" and self.consecutive_fails >= self.fail_threshold:
            self.opened_at = time.monotonic()
            self.trip_count += 1
            self._transition(
                "open",
                f"{self.consecutive_fails} consecutive failures: {self.last_error[:120]}")

    def _transition(self, to: str, reason: str) -> None:
        frm, self.state = self.state, to
        evt = {"dep": self.dep, "from": frm, "to": to, "reason": reason,
               "ts": datetime.now(timezone.utc).isoformat()}
        _TRANSITIONS.append(evt)
        logger.warning("[G17] breaker %s: %s → %s (%s)", self.dep, frm, to, reason)
        _persist_event(evt)

    def snapshot(self) -> dict:
        return {"dep": self.dep, "state": self.state,
                "consecutive_fails": self.consecutive_fails,
                "fail_threshold": self.fail_threshold,
                "cooldown_s": self.cooldown_s,
                "retry_after_s": round(self.retry_after_s(), 1),
                "trip_count": self.trip_count,
                "total_successes": self.total_successes,
                "total_failures": self.total_failures,
                "last_error": self.last_error}


_BREAKERS: dict[str, CircuitBreaker] = {d: CircuitBreaker(d) for d in KNOWN_DEPS}


def get_breaker(dep: str) -> CircuitBreaker:
    if dep not in _BREAKERS:
        _BREAKERS[dep] = CircuitBreaker(dep)
    return _BREAKERS[dep]


def snapshot_all() -> dict[str, dict]:
    return {d: b.snapshot() for d, b in _BREAKERS.items()}


def recent_transitions(limit: int = 50) -> list[dict]:
    return list(_TRANSITIONS)[-limit:]


def _persist_event(evt: dict) -> None:
    """Best-effort fire-and-forget insert into breaker_events."""
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is None:
            return
        loop = asyncio.get_running_loop()
        loop.create_task(db.breaker_events.insert_one(dict(evt)))
    except Exception:
        pass


async def trip_counts_7d(db) -> dict[str, int]:
    """Per-dependency opened-count over the last 7 days from Mongo."""
    out: dict[str, int] = {}
    try:
        cutoff = datetime.now(timezone.utc).timestamp() - 7 * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        cur = db.breaker_events.aggregate([
            {"$match": {"to": "open", "ts": {"$gte": cutoff_iso}}},
            {"$group": {"_id": "$dep", "n": {"$sum": 1}}},
        ])
        async for row in cur:
            out[row["_id"]] = row["n"]
    except Exception:
        pass
    return out


def _delay(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with full jitter."""
    return min(cap, base * (2 ** attempt)) * (0.5 + random.random())


async def call_with_retry(
    dep: str,
    fn: Callable[[], Any],
    *,
    max_retries: int = 2,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
) -> Any:
    """Run `fn()` (sync or async, zero-arg) through the `dep` breaker
    with jittered exponential backoff. Raises BreakerOpenError fast
    when the breaker is open; re-raises the last error otherwise."""
    br = get_breaker(dep)
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        if not br.allow():
            raise BreakerOpenError(dep, br.retry_after_s())
        try:
            out = fn()
            if inspect.isawaitable(out):
                out = await out
            br.record_success()
            return out
        except retry_on as e:
            last_exc = e
            br.record_failure(repr(e))
            if attempt >= max_retries or br.state == "open":
                raise
            await asyncio.sleep(_delay(attempt, base_delay, max_delay))
    raise last_exc  # type: ignore[misc]  # unreachable
