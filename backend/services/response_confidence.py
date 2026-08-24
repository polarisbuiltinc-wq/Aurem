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
# 2026-08-25 — widened (real live-reproduced miss): a response can
# describe the SAME "ship a fix" action in plain prose without ever
# emitting the literal fence or the exact "root cause:" phrase (e.g.
# "you'd click Ship via CTO to commit that fix") and slip straight
# past the old narrow check. This catches that class of prose without
# touching `has_ship_suggestion()`, which stays fence-only on purpose
# (it gates the actual Ship button, a different, narrower concern).
_TASK_ACTION_PROSE_RE = re.compile(
    r"ship\s+(it\s+)?via\s+cto"
    r"|click[^.\n]{0,25}\bship\b"
    r"|\bcommit\b[^.\n]{0,20}\bfix\b"
    r"|\bdeploy\b[^.\n]{0,20}\bfix\b"
    r"|\bship\b[^.\n]{0,20}\bfix\b",
    re.IGNORECASE,
)
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
# 2026-08-22 — MORE AGGRESSIVE (founder-directed, after an intermittent
# recurrence of the cold-start mismatch on a re-test): deliberately
# dropped the purely DESCRIPTIVE nouns that used to live here (file,
# files, code, api, route, component, function, endpoint, config,
# repo, repository, test, tests) — a plain "what does the payment api
# do?" or "where's the config file?" contains one of those words but
# is NOT a fix request, and having them here meant an unrelated
# diagnosis+Ship response could slip past this gate completely
# untouched just because the user's question happened to mention a
# file/api/route. Keeping only unambiguous problem-report or
# change-request signals (verbs/states, not descriptive nouns) closes
# that gap — accepting that a genuinely good response occasionally
# gets the fallback treatment is the intended tradeoff here.
#
# 2026-08-22 (later same day) — real prod bug: that tightening had a
# blind spot. "Check my code for any security problems" (a completely
# legitimate audit request, no explicit "fix"/"bug" word) would get a
# real, on-topic audit reply back that naturally says "Root cause:"
# or proposes an aurem-handoff fix — and the gate wrongly nuked it as
# a "mismatch" since none of its tokens were in this set. Added
# AUDIT-specific signals (deliberately still not generic descriptive
# nouns like "code"/"file" — "audit"/"vulnerability"/"security" etc.
# are unambiguous code-review intent, unlike "code" or "file" alone).
_FIX_INTENT_TOKENS = {
    "fix", "fixes", "fixed", "bug", "bugs", "error", "errors", "broken",
    "crash", "crashes", "fail", "fails", "failing", "failed", "issue",
    "issues", "debug", "add", "implement", "build", "create", "update",
    "change", "refactor", "feature", "ship", "deploy", "commit",
    "write", "install", "upgrade", "migrate", "optimize",
    "improve", "remove", "delete", "revert",
    "rollback",
    "audit", "audits", "scan", "scans", "review", "reviews",
    "vulnerability", "vulnerabilities", "vulnerable", "insecure",
    "security", "secure", "harden", "hardening", "exploit", "exploits",
}

# 2026-08-22 — raised from 10: widens the coverage of the STRICT,
# deterministic `is_definitional_mismatch` hard rule (below), which
# doesn't rely on the token heuristic at all — a longer plain
# question with no genuine fix intent still can't legitimately come
# back with a diagnosis + Ship proposal.
_SIMPLE_WORD_LIMIT = 20

FALLBACK_MESSAGE = (
    "I couldn't find a confident answer to that — try rephrasing, or ask again."
)


async def persist_confidence_check(db, **fields) -> None:
    """2026-08-25 — passive audit trail for `chat.confidence_check`.
    Founder has no raw log access to Preview/Production; this makes
    every mismatch-check outcome queryable via
    GET /admin/insights/confidence-checks instead of requiring a
    support ticket for log access each time. Fire-and-forget: never
    lets a Mongo hiccup affect the actual chat response — same
    fail-open posture as every other passive audit write in this
    codebase (intent_classifications, cost_revenue_alert_log, etc.)."""
    if db is None:
        return
    try:
        import time as _time
        await db.response_confidence_log.insert_one({**fields, "ts": _time.time()})
    except Exception:
        pass


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
        or _TASK_ACTION_PROSE_RE.search(final_output)
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
