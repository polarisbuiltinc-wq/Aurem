"""
core/parliament/breaker.py — 2026-09-08 Phase 3 god-class split.

`ParliamentCircuitBreaker` + the module-level singleton. Moved
verbatim out of the single core/parliament.py (zero logic change).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger("aurem-dev.parliament")


class ParliamentCircuitBreaker:
    """Tracks LLM call health.  Opens after `FAILURE_THRESHOLD`
    consecutive failures and stays open for `COOLDOWN_SECONDS`.

    States::

        CLOSED → OPEN → HALF_OPEN → CLOSED (or back to OPEN)

    Behaviour:
      - CLOSED    : every call goes through, results are recorded
      - OPEN      : `should_attempt()` returns False — callers
                    fall back to a single (non-council) LLM call
      - HALF_OPEN : exactly one probe call is allowed.  Success →
                    CLOSED; failure → back to OPEN with a fresh
                    cooldown.
    """

    FAILURE_THRESHOLD  = 3
    TIMEOUT_PER_CALL   = 25       # seconds — per single LLM call
    COOLDOWN_SECONDS   = 45       # OPEN → HALF_OPEN wait
    WINDOW_SECONDS     = 60       # sliding window for observability

    def __init__(self):
        self._state            = "closed"
        self._consec_failures  = 0
        self._opened_at        = 0.0
        self._half_open_probe  = False   # True while a probe is in flight
        self._window: deque    = deque(maxlen=128)   # (ts, ok, latency_ms)
        self._lock             = asyncio.Lock()

    # ── State machine ─────────────────────────────────────────────
    @property
    def state(self) -> str:
        # OPEN can auto-transition to HALF_OPEN on read if cooldown
        # has elapsed.  This avoids needing a background task.
        if self._state == "open":
            if time.monotonic() - self._opened_at >= self.COOLDOWN_SECONDS:
                self._state = "half_open"
                self._half_open_probe = False
                logger.info("[circuit_breaker] OPEN → HALF_OPEN "
                            "(cooldown elapsed)")
        return self._state

    def should_attempt(self) -> bool:
        """Returns True iff a call should be attempted right now."""
        st = self.state
        if st == "closed":
            return True
        if st == "open":
            return False
        if st == "half_open":
            # Only one probe at a time.
            if not self._half_open_probe:
                self._half_open_probe = True
                return True
            return False
        return True

    # ── Outcome recording ─────────────────────────────────────────
    def _trim(self, now: float) -> None:
        cutoff = now - self.WINDOW_SECONDS
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def record_success(self, latency_ms: float = 0.0) -> None:
        now = time.monotonic()
        self._trim(now)
        self._window.append((now, True, latency_ms))
        self._consec_failures = 0
        if self._state in ("half_open", "open"):
            logger.info("[circuit_breaker] %s → CLOSED (probe succeeded)",
                        self._state.upper())
            self._state = "closed"
            self._half_open_probe = False

    def record_failure(self, latency_ms: float = 0.0,
                        kind: str = "error") -> None:
        now = time.monotonic()
        self._trim(now)
        self._window.append((now, False, latency_ms))
        self._consec_failures += 1
        if self._state == "half_open":
            logger.warning("[circuit_breaker] HALF_OPEN probe failed → "
                           "OPEN again (kind=%s)", kind)
            self._state = "open"
            self._opened_at = now
            self._half_open_probe = False
            return
        if (self._state == "closed"
                and self._consec_failures >= self.FAILURE_THRESHOLD):
            logger.warning(
                "[circuit_breaker] CLOSED → OPEN "
                "(%d consecutive failures; kind=%s)",
                self._consec_failures, kind,
            )
            self._state = "open"
            self._opened_at = now

    # ── Stats (for logs / introspection) ─────────────────────────
    def stats(self) -> dict:
        self._trim(time.monotonic())
        oks = sum(1 for _, ok, _ in self._window if ok)
        return {
            "state":             self._state,
            "consec_failures":   self._consec_failures,
            "window_total":      len(self._window),
            "window_ok":         oks,
            "window_seconds":    self.WINDOW_SECONDS,
            "cooldown_seconds":  self.COOLDOWN_SECONDS,
            "failure_threshold": self.FAILURE_THRESHOLD,
        }


# Module-level singleton — shared across all Parliament instances.
_GLOBAL_BREAKER = ParliamentCircuitBreaker()
