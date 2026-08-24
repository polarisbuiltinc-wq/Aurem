"""
services/ambiguity_gate.py — 2026-08-25 (formalized), Blueprint Phase 1.3

Single source of truth for the "is this task too vague to act on
blindly" check. Was previously duplicated logic living only inside
`routers/cto_projects.py::_is_ambiguous_task()` (the legacy manual-
ship path) with a documented, NOT-built gap for `services/
loop_engine.py`'s Loop Mode planner — Loop Mode has since been
unlocked for all Pro/Team users (see PRD 2026-08-21 "Loop Mode
UNLOCKED"), so that gap is now a real customer-facing hole, not a
future concern. Extracting into one module so both entry points
(`cto_projects.py::submit_task`, `routers/loop.py::start_loop`) share
identical logic and can never drift apart — the exact lesson already
applied to `loop_beta.is_user_allowed()` for the tier-gate.

Deliberately a cheap, no-LLM-cost regex heuristic (not a repo-map
lookup) — a task with no concrete target (a file path, a quoted
string, or enough words to name something specific) is too
under-specified to act on safely; better to ask than to let the model
guess-and-write/guess-and-ship.
"""
from __future__ import annotations

import re

_VAGUE_TASK_PATTERNS = [
    re.compile(r"^(fix|improve|update|clean up|make|do)\s+(it|this|that|things?|stuff)\.?$"),
    re.compile(r"^fix (the )?bugs?\.?$"),
    re.compile(r"^make it (better|work)\.?$"),
    re.compile(r"^improve( the)? (site|app|code)\.?$"),
]
_FILE_PATH_RE = re.compile(r"[\w./-]+\.(jsx?|tsx?|py|css|json|md|html)\b")

CLARIFICATION_MESSAGE = (
    "That's a bit broad for me to act on safely — could you name a "
    "specific file, page, or feature? For example \"fix the signup "
    "form validation in Signup.jsx\" instead of \"fix it\"."
)


def is_ambiguous_task(task_text: str) -> bool:
    """Pure, testable, no I/O. Returns True if `task_text` is too vague
    to safely act on without a follow-up question."""
    t = (task_text or "").strip().lower()
    if not t:
        return True
    if _FILE_PATH_RE.search(t) or '"' in t or "'" in t or "`" in t:
        return False
    if any(p.match(t) for p in _VAGUE_TASK_PATTERNS):
        return True
    return len(t.split()) < 4
