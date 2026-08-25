"""core/errors.py — Resilience Layer Phase 1 (2026-08-25).

Language-agnostic error taxonomy + user-facing envelope + ref_id.

CRITICAL DESIGN RULE: classification is based on exception TYPE and
STRUCTURE only — never on parsing `str(exc)`. Exception messages may
come from any dependency, in any language; a classifier that greps
the message text breaks the moment that text isn't English.

This complements, and does not replace, the two things that already
existed before this pass:
  - `services/error_classifier.py`  → network/auth/quota/input/internal
    category + status-code extraction, wired into main.py's global
    handler. Reused here (not rebuilt) for the network/auth/quota/
    input branches.
  - `services/error_translator.py`  → CTO task-specific, LLM-assisted
    plain-English rewrite of a failure MESSAGE STRING for the
    TaskProgressCard UI. Operates on text after the fact; orthogonal
    to this module, which classifies the exception OBJECT before any
    text exists.

i18n: English-only content this pass (Phase 1). `translate_error()`
takes a `locale` arg and already falls back to `en` for anything else
— adding `errors_hi.json` later is additive, no call-site changes.
"""
from __future__ import annotations

import json
import os
import uuid
from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    TIMEOUT = "TIMEOUT"
    DEPENDENCY_DOWN = "DEPENDENCY_DOWN"
    AUTH_FAILED = "AUTH_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VERIFY_FAILED = "VERIFY_FAILED"
    CONTEXT_LEAK = "CONTEXT_LEAK"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_UNKNOWN = "INTERNAL_UNKNOWN"
    # W3 · 2026-08 — distinct code for the verify self-heal-exhausted
    # terminal FAILED path (services/loop_engine.py::_do_verify), so
    # this specific case is machine-distinguishable from a generic
    # VERIFY_FAILED. No behavior change — additive metadata only.
    LOOP_SELF_HEAL_EXHAUSTED = "LOOP_SELF_HEAL_EXHAUSTED"


# Only these classes are safe to blindly retry — deterministic failures
# (schema/auth/permission) will fail again given the exact same input.
RETRYABLE_CODES = frozenset({
    ErrorCode.TIMEOUT, ErrorCode.DEPENDENCY_DOWN, ErrorCode.RATE_LIMITED,
})


def new_ref_id() -> str:
    return f"ORA-{uuid.uuid4().hex[:6]}"


class ContractError(ValueError):
    """Raised by core/boundaries.py — always classifies as SCHEMA_MISMATCH."""


def classify_exception(exc: BaseException) -> ErrorCode:
    """Classify by TYPE + STRUCTURE only. Never inspects `str(exc)`."""
    from services.retry_guard import BreakerOpenError
    try:
        from services.http.client import ExternalCallError
    except Exception:
        ExternalCallError = ()  # noqa: N806

    if isinstance(exc, ContractError):
        return ErrorCode.SCHEMA_MISMATCH

    # AttributeError with structured .name/.obj (py3.10+) — dict-shaped
    # access attempted on a non-dict. This is the exact class of the
    # production incident this pass fixes ('str' object has no
    # attribute 'get'). Structural check only: which attribute was
    # missing, and what type the target actually was — never the
    # message text.
    if isinstance(exc, AttributeError):
        name = getattr(exc, "name", None)
        obj = getattr(exc, "obj", None)
        if name in ("get", "items", "keys", "values", "setdefault") \
                and not isinstance(obj, dict):
            return ErrorCode.SCHEMA_MISMATCH
        return ErrorCode.INTERNAL_UNKNOWN

    if isinstance(exc, (TypeError, KeyError, json.JSONDecodeError)):
        return ErrorCode.SCHEMA_MISMATCH

    import asyncio
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ErrorCode.TIMEOUT

    if isinstance(exc, BreakerOpenError):
        return ErrorCode.DEPENDENCY_DOWN
    if ExternalCallError and isinstance(exc, ExternalCallError):
        return ErrorCode.DEPENDENCY_DOWN

    exc_mod = type(exc).__module__ or ""
    exc_name = type(exc).__name__
    if "httpx" in exc_mod or "ConnectError" in exc_name or "ConnectionError" in exc_name:
        return ErrorCode.DEPENDENCY_DOWN
    if "pymongo" in exc_mod or "motor" in exc_mod:
        return ErrorCode.DEPENDENCY_DOWN
    if exc_name == "RateLimitExceeded" or getattr(exc, "status_code", None) == 429 \
            or getattr(exc, "status", None) == 429:
        return ErrorCode.RATE_LIMITED
    if "jwt" in exc_mod or "JWT" in exc_name:
        return ErrorCode.AUTH_FAILED
    if isinstance(exc, PermissionError):
        return ErrorCode.PERMISSION_DENIED

    return ErrorCode.INTERNAL_UNKNOWN


_I18N_DIR = os.path.join(os.path.dirname(__file__), "..", "i18n")
_I18N_CACHE: dict[str, dict] = {}


def _load_catalog(locale: str) -> dict:
    locale = locale if locale in ("en", "hi") else "en"
    if locale not in _I18N_CACHE:
        path = os.path.join(_I18N_DIR, f"errors_{locale}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                _I18N_CACHE[locale] = json.load(f)
        except Exception:
            _I18N_CACHE[locale] = {}
    return _I18N_CACHE[locale]


def translate_error(code: ErrorCode, locale: str = "en") -> dict:
    """Returns {title, what_happened, what_to_try} for the given code.

    Falls back to `en`, then to a generic internal-error entry, if the
    requested locale or code is missing from the catalog — the user
    NEVER sees a raw lookup failure here.
    """
    catalog = _load_catalog(locale)
    entry = catalog.get(code.value)
    if entry is None and locale != "en":
        entry = _load_catalog("en").get(code.value)
    if entry is None:
        entry = _load_catalog("en").get(ErrorCode.INTERNAL_UNKNOWN.value) or {
            "title": "Something went wrong",
            "what_happened": "An unexpected internal error occurred.",
            "what_to_try": ["Try again.", "Contact support if it persists."],
        }
    return entry


def build_error_envelope(exc: BaseException, *, locale: str = "en",
                          ref_id: Optional[str] = None) -> dict:
    """User-facing error contract. Raw exception text NEVER included."""
    code = classify_exception(exc)
    content = translate_error(code, locale)
    return {
        "error_code": code.value,
        "title": content["title"],
        "what_happened": content["what_happened"],
        "what_to_try": content["what_to_try"],
        "can_retry": code in RETRYABLE_CODES,
        "ref_id": ref_id or new_ref_id(),
    }
