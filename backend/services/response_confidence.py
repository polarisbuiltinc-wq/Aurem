"""
services/response_confidence.py — 2026-08-21, hardened 2026-08-22

Layered defense for the "cold-start / council-recall mismatch" bug:
a weakly-matched past example (score just above the retriever's
_MIN_SCORE=0.25 weak-match gate — see ora_council_retriever.py) can
still get echoed by the model almost verbatim, INCLUDING an unrelated
`aurem-handoff` fix proposal, for a completely unrelated trivial
question (e.g. "what is 5+5?" answered with a GitHub-auth "root
cause" + a Ship via CTO button).

Root cause is still NOT identified (could not be reproduced under
verbose logging in preview as of 2026-08-22 — see
routers/chat.py's `chat.confidence_check` log lines and
services/ora_council_retriever.py's recall log line for the
instrumentation added to keep hunting it). This module is the
GUARANTEE layer: even if the cause is never found, the user must
never see a mismatched response.

Two checks, from strictest to broadest:
  1. `is_definitional_mismatch` — a HARD rule (founder-specified):
     a short (<=10 words), plain user message with no code/error/
     fix intent of its own can NEVER legitimately produce a response
     containing a file path, a "Root cause:" diagnosis, or an
     `aurem-handoff` fence. If it does, it's a mismatch, full stop.
  2. `response_seems_mismatched` — the broader fallback used for
     longer/ambiguous messages: an unsolicited code-ship
     (`aurem-handoff`) or diagnosis is only legitimate when the
     user's OWN message actually carries fix/bug/code intent.
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

_HANDOFF_FENCE_RE = re.compile(r"```aurem-handoff\b")
_ROOT_CAUSE_RE = re.compile(r"root cause[:\s]", re.IGNORECASE)
_FILE_PATH_RE = re.compile(
    r"\b[\w\-./]+\.(?:py|js|jsx|ts|tsx|json|md|yml|yaml|css|html|txt|sql|env|sh)\b"
)
# Literal code/error SIGNALS in the user's OWN message — fenced code,
# a real file path, a stack trace, a URL, or an HTTP status code.
# Deliberately NOT keyword-based (bug/error/fix are handled by
# `is_fix_intent` below) — this only catches actual code artifacts.
_CODE_SIGNAL_RE = re.compile(
    r"```|" + _FILE_PATH_RE.pattern + r"|traceback|stack trace|https?://|\b[45]\d\d\b"
)

# Any of these present in the user's own message means they DO want
# a code/bug-fix answer — a diagnosis + Ship button is legitimate.
_FIX_INTENT_TOKENS = {
    "fix", "fixes", "fixed", "bug", "bugs", "error", "errors", "broken",
    "crash", "crashes", "fail", "fails", "failing", "failed", "issue",
    "issues", "debug", "add", "implement", "build", "create", "update",
    "change", "refactor", "feature", "ship", "deploy", "commit", "code",
    "function", "endpoint", "api", "route", "component", "file", "files",
    "test", "tests", "write", "install", "upgrade", "migrate", "optimize",
    "improve", "remove", "delete", "repo", "repository", "revert",
    "rollback", "config",
}

_SIMPLE_WORD_LIMIT = 10

FALLBACK_MESSAGE = (
    "I couldn't find a confident answer to that — try rephrasing, or ask again."
)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def is_fix_intent(user_message: str) -> bool:
    """True iff the user's OWN message signals they actually want a
    bug-fix / code-change answer."""
    return bool(_tokens(user_message) & _FIX_INTENT_TOKENS)


def _looks_like_code(user_message: str) -> bool:
    """True iff the user's OWN message contains an actual code/error
    artifact (fenced code, file path, traceback, URL, HTTP status)."""
    return bool(_CODE_SIGNAL_RE.search(user_message or ""))


def _response_has_fix_signal(final_output: str) -> bool:
    return bool(
        _HANDOFF_FENCE_RE.search(final_output)
        or _ROOT_CAUSE_RE.search(final_output)
    )


def has_ship_suggestion(final_output: str) -> bool:
    """True iff `final_output` carries an actual ```aurem-handoff fence
    — i.e. the ONE thing that renders a "Ship via CTO" button on the
    frontend (see MessageBubble.jsx). Deliberately narrower than
    `_response_has_fix_signal`: a bare "Root cause:" sentence with no
    fence never shows a Ship button, so suppressing it isn't a
    suppressed *ship suggestion* — just a suppressed diagnosis."""
    return bool(_HANDOFF_FENCE_RE.search(final_output or ""))


def is_definitional_mismatch(user_message: str, final_output: str) -> bool:
    """HARD rule — a short/plain question can never legitimately get a
    "Root cause:" diagnosis or an aurem-handoff ship-proposal back.
    NOTE: deliberately does NOT trigger on a bare file-path mention —
    "who handles billing?" → "that's in services/billing.py" is a
    perfectly legitimate short codebase answer, not a mismatch (see
    tests/test_citation_guard_persist_ordering.py, which already
    covers unverified-file-path correction via CitationGuard)."""
    if not final_output:
        return False
    words = (user_message or "").split()
    if len(words) > _SIMPLE_WORD_LIMIT:
        return False
    if is_fix_intent(user_message) or _looks_like_code(user_message):
        return False
    return _response_has_fix_signal(final_output)


def response_seems_mismatched(user_message: str, final_output: str) -> bool:
    """True iff this turn should NEVER be shown to the user as-is —
    combines the hard short-message rule with the broader fix-intent
    heuristic for longer messages."""
    if not final_output:
        return False
    if is_definitional_mismatch(user_message, final_output):
        return True
    if not _response_has_fix_signal(final_output):
        return False
    return not is_fix_intent(user_message)
