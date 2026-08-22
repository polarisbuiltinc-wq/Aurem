"""
services/error_classifier.py — 2026-08-24 (Pillar 5, Production-Readiness)

Centralized error classification: network / auth / quota / internal /
input. Reuses the exact status-code-extraction pattern already proven
in `services/tool_executor.py`'s `_extract_status_code` (that module's
mapping is scoped to GitHub tool-call errors only — this generalizes
the same technique app-wide) and `services/http/client.py`'s
`ExternalCallError`/`BreakerOpenError` types for the network/quota
categories.

Wired as the mandatory hop for every UNCAUGHT exception in the app
(see main.py's `_global_exc_handler`) plus FastAPI's
`RequestValidationError` path (input category) — the two places that
are structurally guaranteed to see every request that reaches the
frontend without an endpoint author's own deliberate `HTTPException`
message. Explicit `raise HTTPException(...)` calls throughout routers/
remain the endpoint author's own considered message (out of scope for
a blanket rewrite here — see PRD Pillar 5 notes for the honest scope
boundary).
"""
from __future__ import annotations

import re
from typing import Optional

_STATUS_RE = re.compile(r"\b(?:HTTP|status)[\s:]*?(\d{3})\b", re.IGNORECASE)

CATEGORIES = ("network", "auth", "quota", "internal", "input")

_TEMPLATES = {
    "network": "We couldn't reach an external service just now. Please try again in a moment.",
    "auth":    "Your session isn't valid or has expired. Please sign in again.",
    "quota":   "You've hit a rate or usage limit. Please wait a moment and try again.",
    "input":   "That request couldn't be processed as sent. Please check the details and try again.",
    "internal": "An internal error occurred. Please try again.",
}


def _extract_status_code(exc: Exception) -> Optional[int]:
    """Same technique as tool_executor._extract_status_code, generalized."""
    rsp = getattr(exc, "response", None)
    if rsp is not None:
        sc = getattr(rsp, "status_code", None)
        if isinstance(sc, int):
            return sc
    sc = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if isinstance(sc, int):
        return sc
    m = _STATUS_RE.search(str(exc))
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def classify_error(exc: Exception) -> dict:
    """Returns {category, user_message, http_status, exc_class} —
    NEVER includes the raw exception string or a stack trace. Callers
    log the real exception separately (already done via
    logger.error(..., exc_info=True) in main.py's global handler)."""
    from services.retry_guard import BreakerOpenError
    try:
        from services.http.client import ExternalCallError
    except Exception:
        ExternalCallError = ()  # noqa: N806 — optional import guard

    exc_name = type(exc).__name__
    status = _extract_status_code(exc)

    if isinstance(exc, BreakerOpenError):
        return {"category": "quota", "user_message": _TEMPLATES["quota"],
                "http_status": 503, "exc_class": exc_name}
    if ExternalCallError and isinstance(exc, ExternalCallError):
        return {"category": "network", "user_message": _TEMPLATES["network"],
                "http_status": status or 502, "exc_class": exc_name}

    exc_mod = type(exc).__module__ or ""
    if "httpx" in exc_mod or "TimeoutError" in exc_name or "ConnectError" in exc_name:
        return {"category": "network", "user_message": _TEMPLATES["network"],
                "http_status": status or 502, "exc_class": exc_name}
    if "jwt" in exc_mod or "JWT" in exc_name or exc_name in ("PermissionError",):
        return {"category": "auth", "user_message": _TEMPLATES["auth"],
                "http_status": status or 401, "exc_class": exc_name}
    if status == 429:
        return {"category": "quota", "user_message": _TEMPLATES["quota"],
                "http_status": 429, "exc_class": exc_name}
    if status in (400, 422) or exc_name in ("ValidationError", "ValueError"):
        return {"category": "input", "user_message": _TEMPLATES["input"],
                "http_status": status or 400, "exc_class": exc_name}
    if status in (401, 403):
        return {"category": "auth", "user_message": _TEMPLATES["auth"],
                "http_status": status, "exc_class": exc_name}

    return {"category": "internal", "user_message": _TEMPLATES["internal"],
            "http_status": status or 500, "exc_class": exc_name}
