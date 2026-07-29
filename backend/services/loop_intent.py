"""
services/loop_intent.py — Iter 349 · Lightweight read-only intent gate.

PROD P0 (2026-06): "what is the current CI status on main" triggered the
full Loop pipeline (Council + Parliament) and hung at "Generating plan…"
for 90+ s. Read-only questions must never enter Loop Mode.

Design: conservative, zero-LLM, pure-regex heuristic.
  1. ACTION verbs always win — "why is login failing AND fix it" → LOOP.
  2. Explicit loop opt-in phrases ("run this as a loop") → LOOP.
  3. Otherwise read-only ONLY when a clear read signal exists
     (question-word start, read-verb start, or trailing "?").
False negatives (a question still entering Loop) are acceptable;
false positives (a real task diverted to chat) must be ~impossible,
hence the action-verb precedence.
"""
from __future__ import annotations

import re

# Phrases that force Loop Mode regardless of anything else.
_LOOP_OPT_IN = re.compile(
    r"\b(as a loop|loop mode|run (this |it )?as a loop|loop chala|loop mein|loop me\b)",
    re.IGNORECASE,
)

# Write-intent verbs (English + Hinglish). Word-boundary matched so
# "add" never fires inside "address". Any hit → NOT read-only.
_ACTION_VERBS = re.compile(
    r"\b("
    r"fix|add|implement|create|build|make|refactor|deploy|ship|update|"
    r"change|modify|remove|delete|write|rename|install|uninstall|migrate|"
    r"merge|push|commit|upgrade|downgrade|patch|revert|rollback|configure|"
    r"setup|set up|enable|disable|optimize|optimise|improve|integrate|"
    r"replace|generate|scaffold|bump|release|publish|apply|edit|"
    r"banao|bana do|hatao|hata do|badlo|badal do|jodo|jod do|likho|likh do|"
    r"lagao|laga do|daal|theek kar|thik kar|sahi kar"
    r")\b",
    re.IGNORECASE,
)

# Read-only openers: question words + read verbs (English + Hinglish).
_READ_OPENERS = re.compile(
    r"^(what|what's|whats|why|how|when|where|which|who|whose|"
    r"is|are|was|were|does|do|did|can|could|should|would|will|has|have|"
    r"show|list|explain|describe|summarize|summarise|check|tell|status|"
    r"display|print|view|count|compare|find|search|"
    r"kya|kyun|kyu|kaun|kaunsa|konsa|kaisa|kaise|kitna|kitne|kab|kahan|"
    r"dikhao|batao|bata|samjhao|dekho)\b",
    re.IGNORECASE,
)

# Long / multi-paragraph messages are assumed to be real task briefs.
_MAX_READ_ONLY_LEN = 600


def detect_read_only_intent(text: str) -> tuple[bool, str]:
    """Return (is_read_only, reason). Conservative by design."""
    t = (text or "").strip()
    if not t:
        return False, "empty"
    if len(t) > _MAX_READ_ONLY_LEN:
        return False, "too_long_for_read_only"
    if _LOOP_OPT_IN.search(t):
        return False, "explicit_loop_opt_in"
    m = _ACTION_VERBS.search(t)
    if m:
        return False, f"action_verb:{m.group(1).lower()}"
    if _READ_OPENERS.match(t):
        return True, "read_opener"
    if t.rstrip().endswith("?"):
        return True, "trailing_question_mark"
    return False, "no_read_signal"
