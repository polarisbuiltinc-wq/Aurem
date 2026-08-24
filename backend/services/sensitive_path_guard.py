"""
services/sensitive_path_guard.py — 2026-08-27, G3-spirit real implementation.

GUARDS_CHARTER.md's G3 ("scope-drift hard block" / PROTECTED_PATHS —
routers/admin*, payments.py, auth.py, mcp.py, vault*, stripe_client.py,
.github/workflows/*) was speced but NEVER actually implemented anywhere
in the codebase — confirmed by direct grep: no `PROTECTED_PATHS`
constant exists in `loop_engine.py` or anywhere else. The charter's own
STATUS section lists G3 among the guards marked "NOT STARTED".

This module is the real implementation, built for `cto_projects.py`'s
task workers (`_run_task_via_api` / `_run_task_with_git`) — the engine
real (non-founder) customers actually reach. `loop_engine.py` remains
gated to founder/admin/unlimited tiers and is not this module's target.

Generalized from G3's spec (which named AUREM's OWN files) into a
filename/path pattern that applies to ANY connected repo: an
AI-generated task shouldn't get a free pass to silently rewrite a file
that LOOKS like it holds auth/payment/secrets/CI logic — for the
customer's own repo just as much as for AUREM's. Guarded by the same
"read-only-from-the-task-record, never from LLM output" pattern already
used for the test-file lock (`allow_test_file_change`), so the model
can never self-grant.
"""
from __future__ import annotations

import re
from typing import Iterable

_SENSITIVE_BASENAME_PATTERNS = (
    re.compile(r"^payments?\.py$", re.IGNORECASE),
    re.compile(r"^auth\.py$", re.IGNORECASE),
    re.compile(r"^stripe_client\.py$", re.IGNORECASE),
    re.compile(r"^mcp\.py$", re.IGNORECASE),
    re.compile(r"^vault.*\.py$", re.IGNORECASE),
    re.compile(r"^admin.*\.py$", re.IGNORECASE),
)


def is_sensitive_path(path: str) -> bool:
    """True if `path` looks like it holds auth/payment/secrets/CI logic
    — by filename convention (payments.py, auth.py, stripe_client.py,
    mcp.py, vault*.py, admin*.py) or location (.github/workflows/*),
    regardless of which repo it's in."""
    if not path:
        return False
    p = path.strip().replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    if p.startswith(".github/workflows/"):
        return True
    basename = p.rsplit("/", 1)[-1]
    if any(pat.match(basename) for pat in _SENSITIVE_BASENAME_PATTERNS):
        return True
    # "routers/admin*" charter pattern, generalized: any path SEGMENT
    # (not the file itself) literally named "admin".
    parts = p.split("/")
    if "admin" in parts[:-1]:
        return True
    return False


def find_sensitive_paths(edit_paths: Iterable[str]) -> list[str]:
    """Return the subset of `edit_paths` that are sensitive, in the
    order given."""
    return [p for p in edit_paths if is_sensitive_path(p)]
