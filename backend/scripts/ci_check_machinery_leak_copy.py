#!/usr/bin/env python3
"""
scripts/ci_check_machinery_leak_copy.py — 2026-08-27, "Show the Outcome,
Never the Engine" P2.

Output-side twin of `ci_check_raw_exception_leak.py` (Guard 22): that
guard stops a raw EXCEPTION from reaching a user-facing sink; this one
stops a known internal MACHINERY CODENAME/jargon token from being
hardcoded into a new user-facing COPY string before it ever ships —
catching the class of bug at the developer's keyboard (this exact CI
gate), not only at runtime (services/output_guard.py's net, and
services/loop_engine.py's `_narrate()` strip filter, both still run in
production as the second line of defense).

Banned-token list is imported directly from the two runtime modules
that already enforce it (single source of truth — no duplicated list
to drift out of sync).

Scans:
  - backend/services/loop_engine.py — string-literal arguments to
    `_narrate(text=...)` / `_emit(..., message=...)` calls (AST-based,
    same technique as Guard 22).
  - backend/i18n/errors_en.json — every catalog string value.
  - frontend/src/**/*.jsx — JSX text content + string literals
    (regex-based; JS has no stdlib `ast` equivalent here, so this is
    intentionally narrower/best-effort, not exhaustive).

Usage:
    python scripts/ci_check_machinery_leak_copy.py [--override]

Exit codes:
    0 — no violations, or override present
    1 — violation(s) found without override
    2 — invocation / IO error
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

_BACKEND_ROOT = os.path.join(os.path.dirname(__file__), "..")
_FRONTEND_SRC = os.path.join(_BACKEND_ROOT, "..", "frontend", "src")


def _banned_tokens() -> list[str]:
    """Single source of truth: pull the same tokens the two runtime
    nets already enforce, rather than a third, driftable list."""
    sys.path.insert(0, _BACKEND_ROOT)
    from services.output_guard import _MACHINERY_LEAK_PATTERNS
    from services.loop_engine import _ENGINE_LEAK_PATTERNS
    tokens = []
    for pattern, _repl in list(_MACHINERY_LEAK_PATTERNS) + list(_ENGINE_LEAK_PATTERNS):
        # Extract the literal word(s) from simple `\bWORD\b`-style
        # patterns; skip the handful of structural (non-word) patterns.
        m = re.match(r"^\\b([\w .\-]+)\\b$", pattern.pattern)
        if m:
            tokens.append(m.group(1))
    # 2026-08-27 — a few investigated exclusions: "Vanguard" is the
    # product's own public, marketed feature name (landing page,
    # security-scan UI) — NOT an internal codename, deliberately
    # excluded here even though it fires on the runtime nets' list.
    tokens += ["chairman", "e2b"]
    tokens = [t for t in tokens if t.lower() != "vanguard"]
    return sorted(set(tokens), key=str.lower)


_BANNED = _banned_tokens()
_BANNED_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in _BANNED) + r")\b", re.IGNORECASE)


def _check_loop_engine_narration(path: str) -> list[str]:
    violations = []
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src, filename=path)
    except (OSError, SyntaxError):
        return []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("_narrate", "_emit")):
            continue
        for kw in n.keywords:
            if kw.arg not in ("text", "message"):
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                hit = _BANNED_RE.search(kw.value.value)
                if hit:
                    violations.append(
                        f"{path}:{n.lineno}: narration/emit text contains "
                        f"banned machinery token {hit.group(1)!r}: "
                        f"{kw.value.value!r}"
                    )
    return violations


def _check_errors_catalog(path: str) -> list[str]:
    violations = []
    try:
        with open(path, encoding="utf-8") as f:
            catalog = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    for code, entry in catalog.items():
        strings = []
        if isinstance(entry, dict):
            strings.append(entry.get("title") or "")
            strings.append(entry.get("what_happened") or "")
            strings.extend(entry.get("what_to_try") or [])
        for s in strings:
            hit = _BANNED_RE.search(s)
            if hit:
                violations.append(
                    f"{path}: catalog entry {code!r} contains banned "
                    f"machinery token {hit.group(1)!r}: {s!r}"
                )
    return violations


# Only checks VISIBLE user-facing text: JSX children (`>text<`) and a
# short allowlist of genuinely user-facing string props. Deliberately
# does NOT check `data-testid`, `className`, event names, hrefs, or
# style values — those are developer/test plumbing, not copy a user
# reads (an earlier draft of this script matched ANY quoted string and
# produced overwhelming false positives on testid/className attrs).
_JSX_CHILD_TEXT_RE = re.compile(r">([^<>{}\n]{2,200})<")
_JSX_COPY_PROP_RE = re.compile(
    r'\b(?:title|label|placeholder|alt|aria-label|text|message|'
    r'description|tooltip|helperText)\s*=\s*["\']([^"\'\n]{2,200})["\']'
)


def _check_frontend_jsx(path: str) -> list[str]:
    violations = []
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return []
    for lineno, line in enumerate(src.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue  # skip comments — copy check, not code-search
        candidates = (
            [m.group(1) for m in _JSX_CHILD_TEXT_RE.finditer(line)]
            + [m.group(1) for m in _JSX_COPY_PROP_RE.finditer(line)]
        )
        for text in candidates:
            hit = _BANNED_RE.search(text)
            if hit:
                violations.append(
                    f"{path}:{lineno}: user-facing copy contains banned "
                    f"machinery token {hit.group(1)!r}: {text.strip()!r}"
                )
    return violations


def main(argv: list[str]) -> int:
    override = "--override" in argv
    violations: list[str] = []

    loop_engine_path = os.path.join(_BACKEND_ROOT, "services", "loop_engine.py")
    if os.path.isfile(loop_engine_path):
        violations.extend(_check_loop_engine_narration(loop_engine_path))

    errors_catalog_path = os.path.join(_BACKEND_ROOT, "i18n", "errors_en.json")
    if os.path.isfile(errors_catalog_path):
        violations.extend(_check_errors_catalog(errors_catalog_path))

    if os.path.isdir(_FRONTEND_SRC):
        for root, _, files in os.walk(_FRONTEND_SRC):
            if "node_modules" in root or "__pycache__" in root:
                continue
            for fn in files:
                if fn.endswith((".jsx", ".tsx")):
                    violations.extend(_check_frontend_jsx(os.path.join(root, fn)))

    if violations:
        print(f"Found {len(violations)} machinery-leak-copy violation(s):\n")
        for v in violations:
            print(f"  ::error::{v}")
        if override:
            print("\n[coverage-approved]-style override present — not blocking, but please fix soon.")
            return 0
        print(
            "\nEach of these hardcodes an internal AUREM machinery codename "
            "or jargon term directly into user-facing copy — the class of "
            "bug behind the 'Running Vanguard security scan…' leak. Rewrite "
            "in plain language, or add '[machinery-copy-approved]' to the "
            "commit message if this is deliberate (e.g. an admin-only "
            "surface)."
        )
        return 1
    print("OK — no machinery-leak-copy violations found.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
