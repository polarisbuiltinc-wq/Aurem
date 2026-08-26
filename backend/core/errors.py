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
    # Part B · W3 · 2026-08 — language-support small version. Refusal
    # codes for the edit/verify/execute path's single shared read
    # choke point (services/github_api_writer.py::fetch_file).
    FILE_BINARY_NOT_EDITABLE = "FILE_BINARY_NOT_EDITABLE"
    FILE_ENCODING_UNSUPPORTED = "FILE_ENCODING_UNSUPPORTED"
    FILE_LANGUAGE_UNVERIFIED = "FILE_LANGUAGE_UNVERIFIED"
    # Ship/Commit Robustness · 2026-08-26 — the commit OBJECT was
    # created in GitHub's object store (git/commits succeeded) but
    # the branch ref update (the actual "push") was rejected —
    # branch protection, a 409 conflict, etc. Distinct from
    # "nothing was committed at all" (blob/tree/commit step itself
    # failing) so the ship-report path can tell a user the truth:
    # a commit exists (by SHA) but never reached the branch.
    PUSH_FAILED = "PUSH_FAILED"
    # 2026-08 hardening (F2) — LLM cost-cap breach mid-loop. Distinct from
    # VERIFY_FAILED/INTERNAL_UNKNOWN so loop_engine.py can PAUSE (not fail)
    # on this specific code — "blocked ≠ failed" (C4) applied to budget.
    COST_CAP_REACHED = "COST_CAP_REACHED"


# Only these classes are safe to blindly retry — deterministic failures
# (schema/auth/permission) will fail again given the exact same input.
RETRYABLE_CODES = frozenset({
    ErrorCode.TIMEOUT, ErrorCode.DEPENDENCY_DOWN, ErrorCode.RATE_LIMITED,
})


def new_ref_id() -> str:
    return f"ORA-{uuid.uuid4().hex[:6]}"


class ContractError(ValueError):
    """Raised by core/boundaries.py — always classifies as SCHEMA_MISMATCH."""


class BinaryFileError(ValueError):
    """Raised by fetch_file() when the file's first 8 KiB contain a
    NUL byte — the standard binary-content heuristic. Carries `path`
    so callers can surface which file was refused."""

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"binary content detected: {path}")


class UnsupportedEncodingError(ValueError):
    """Raised by fetch_file() when content fails strict UTF-8 decode
    but has no NUL byte (legacy-encoding text, e.g. Latin-1/Cp1252).
    Full legacy-encoding support is out of scope this pass — this is
    a typed refusal, never a silent U+FFFD write-back."""

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"non-UTF-8 text encoding: {path}")


class PushFailedError(RuntimeError):
    """Raised by services/github_api_writer.py::commit_files() when
    the branch-ref-update step (the actual "push") is rejected AFTER
    the commit object itself was already created in GitHub's object
    store. Carries `commit_sha` — the orphaned commit exists (by SHA)
    but is not reachable from any branch — so the ship-report path
    can tell the truth: "committed, push failed" is NOT the same as
    "nothing was committed"."""

    def __init__(self, commit_sha: str, reason: str):
        self.commit_sha = commit_sha
        self.reason = reason
        super().__init__(
            f"commit {commit_sha[:7]} created but push (ref update) "
            f"failed: {reason}"
        )


def classify_exception(exc: BaseException) -> ErrorCode:
    """Classify by TYPE + STRUCTURE only. Never inspects `str(exc)`."""
    from services.retry_guard import BreakerOpenError
    try:
        from services.http.client import ExternalCallError
    except Exception:
        ExternalCallError = ()  # noqa: N806

    if isinstance(exc, ContractError):
        return ErrorCode.SCHEMA_MISMATCH

    if isinstance(exc, BinaryFileError):
        return ErrorCode.FILE_BINARY_NOT_EDITABLE
    if isinstance(exc, UnsupportedEncodingError):
        return ErrorCode.FILE_ENCODING_UNSUPPORTED
    if isinstance(exc, PushFailedError):
        return ErrorCode.PUSH_FAILED

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
