"""core/boundaries.py — Resilience Layer Phase 1 (2026-08-25).

Trust-boundary coercion. `coerce()` is the primitive that would have
turned the production incident ("'str' object has no attribute
'get'") into a clean, classified `ContractError` at the point of
entry instead of an unhandled `AttributeError` deep inside business
logic.

Scope note: this pass ships the primitive + wires it into the new
error-path tests. A full codebase-wide audit replacing every
unguarded `.get()` call site (as the original spec's "Requirement 1"
asked) is explicitly OUT of scope for this pass — that is a large,
mechanical, review-heavy change across hundreds of call sites and was
deferred pending real failure-rate data from the fitness-invariant
triage (see CHANGELOG 2026-08-25).
"""
from __future__ import annotations

import json
from typing import Any, Type

from core.errors import ContractError

__all__ = ["ContractError", "coerce", "normalize_payload"]


def coerce(value: Any, expected_type: Type, *, context: str = "") -> Any:
    """Return `value` if it already matches `expected_type`.

    If `expected_type` is `dict` and `value` is a JSON-encoded `str`,
    attempt `json.loads()` and accept the result only if it's a dict.
    Anything else raises `ContractError` — never lets a shape
    mismatch reach the caller's own `.get()`/`.items()` call.
    """
    if isinstance(value, expected_type):
        return value
    if expected_type is dict and isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError) as e:
            raise ContractError(
                f"{context or 'payload'}: expected dict, got a str that "
                f"isn't valid JSON",
            ) from e
        if not isinstance(parsed, dict):
            raise ContractError(
                f"{context or 'payload'}: expected dict, JSON parsed to "
                f"{type(parsed).__name__}",
            )
        return parsed
    raise ContractError(
        f"{context or 'payload'}: expected {expected_type.__name__}, "
        f"got {type(value).__name__}",
    )


def normalize_payload(raw: Any) -> dict:
    """`str` or `dict` in, `dict` out. Anything else → `ContractError`."""
    return coerce(raw, dict, context="normalize_payload")
