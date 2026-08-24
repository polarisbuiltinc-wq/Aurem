#!/usr/bin/env python3
"""
scripts/ci_check_raw_exception_leak.py — 2026-08-24, Guard 22

Phase 2.3 blueprint gap: error translation existed only as a
CONVENTION (`services/error_classifier.py::classify_error(e)` must be
called before an exception reaches a user-facing sink) — nothing
structurally stopped a new raw `str(exception)` leak from being
merged, which is exactly how the real ReRootsBeauty incident happened
("'str' object has no attribute 'get'" reaching a customer's task
log) and how the Mode D `ora_reply` leak happened separately.

This is a real, CI-enforced, AST-based static check (not a runtime
type — Python has no way to make `str` itself refuse exception
values, so a lint gate is the practical equivalent the blueprint
explicitly names as an acceptable alternative). It flags any
exception variable bound in an `except ... as e:` handler that flows
— via `str(e)` or an f-string `{e}` — directly into a known
user-facing sink (`_log(...)`, or an assignment to a variable named
`ora_reply`) WITHOUT `classify_error(` appearing anywhere in that same
statement first.

This is intentionally narrow (few sink names, exact patterns) to
avoid false positives on legitimate internal `logger.warning("...%r", e)`
calls, which are never customer-visible and use safe %-formatting,
not string interpolation.

Usage:
    python scripts/ci_check_raw_exception_leak.py [--override]

Exit codes:
    0 — no violations, or override present
    1 — violation(s) found without override
    2 — invocation / IO error
"""
from __future__ import annotations

import ast
import os
import sys

_SCAN_DIRS = ("routers", "services", "cto_services", "core")
_SINK_CALL_NAMES = {"_log", "_emit_step"}
_SINK_ASSIGN_TARGETS = {"ora_reply", "error_plain", "user_message"}
# send_founder_alert is intentionally excluded: it's an internal,
# founder/admin-only notification where full raw technical detail is
# the DESIRED behavior, not a leak. This lint is only concerned with
# sinks that reach a customer (task step logs, chat replies).


def _contains_classify_error(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if name == "classify_error":
                return True
    return False


def _raw_exc_usages(node: ast.AST, exc_names: set[str]) -> list[str]:
    """Return exc var names that appear as str(exc) or f"...{exc}..." anywhere in `node`."""
    hits = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "str":
            for a in n.args:
                if isinstance(a, ast.Name) and a.id in exc_names:
                    hits.append(a.id)
        if isinstance(n, ast.JoinedStr):
            for v in n.values:
                if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.value.id in exc_names:
                    hits.append(v.value.id)
    return hits


def _check_file(path: str) -> list[str]:
    violations = []
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src, filename=path)
    except (OSError, SyntaxError):
        return []

    for handler in [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]:
        if not handler.name:
            continue
        exc_names = {handler.name}
        for stmt in handler.body:
            for n in ast.walk(stmt):
                is_sink_call = (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id in _SINK_CALL_NAMES
                )
                is_sink_assign = (
                    isinstance(n, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id in _SINK_ASSIGN_TARGETS
                        for t in n.targets
                    )
                )
                if not (is_sink_call or is_sink_assign):
                    continue
                if _contains_classify_error(n):
                    continue  # already sanitized in this same statement
                raw_hits = _raw_exc_usages(n, exc_names)
                if raw_hits:
                    violations.append(
                        f"{path}:{n.lineno}: raw exception '{raw_hits[0]}' flows into a "
                        f"user-facing sink without services.error_classifier.classify_error() — "
                        f"wrap it first (see routers/cto_projects.py:3658 for the pattern)."
                    )
    return violations


def main(argv: list[str]) -> int:
    override = "--override" in argv
    backend_root = os.path.join(os.path.dirname(__file__), "..")
    violations: list[str] = []
    for d in _SCAN_DIRS:
        full = os.path.join(backend_root, d)
        if not os.path.isdir(full):
            continue
        for root, _, files in os.walk(full):
            for fn in files:
                if fn.endswith(".py"):
                    violations.extend(_check_file(os.path.join(root, fn)))

    if violations:
        print(f"Found {len(violations)} raw-exception-leak violation(s):\n")
        for v in violations:
            print(f"  ::error::{v}")
        if override:
            print("\n[coverage-approved]-style override present — not blocking, but please fix soon.")
            return 0
        print(
            "\nEach of these can leak a raw Python exception message to a "
            "customer (the exact class of bug behind the ReRootsBeauty "
            "incident). Wrap with services.error_classifier.classify_error(e) "
            "before it reaches the sink, or add '[raw-error-approved]' to the "
            "commit message if this is a deliberate, reviewed exception."
        )
        return 1
    print("OK — no raw-exception-leak violations found.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
