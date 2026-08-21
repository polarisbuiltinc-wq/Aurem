"""
services/response_confidence.py — 2026-08-21

Mitigation for the "cold-start / council-recall mismatch" bug: a
weakly-matched past example (score just above the retriever's
_MIN_SCORE=0.25 weak-match gate — see ora_council_retriever.py) can
still get echoed by the model almost verbatim, INCLUDING an unrelated
`aurem-handoff` fix proposal, for a completely unrelated trivial
question (e.g. "what is 5+5?" answered with a GitHub-auth "root
cause" + a Ship via CTO button).

This is a SAFETY NET, not the root-cause fix (still under
investigation — the retriever's weak-match band and per-user recall
corpus growth are the leading suspects). It catches the one genuinely
dangerous outcome — an unsolicited code-ship proposal for a question
that carries no bug/fix/code intent at all — and swaps it for a
friendly fallback BEFORE anything is shown to the user, which also
means the `aurem-handoff` fence is gone so MessageBubble's Ship via
CTO button (ShipDialog) can never render for it.
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

_HANDOFF_FENCE_RE = re.compile(r"```aurem-handoff\b")
_ROOT_CAUSE_RE = re.compile(r"root cause[:\s]", re.IGNORECASE)

# Any of these present in the user's own message means they DO want
# a code/bug-fix answer — a diagnosis + Ship button is legitimate.
_FIX_INTENT_TOKENS = {
    "fix", "bug", "bugs", "error", "errors", "broken", "crash", "crashes",
    "fail", "fails", "failing", "failed", "issue", "issues", "debug",
    "add", "implement", "build", "create", "update", "change", "refactor",
    "feature", "ship", "deploy", "commit", "code", "function", "endpoint",
    "api", "route", "component", "file", "files", "test", "tests", "write",
    "install", "upgrade", "migrate", "optimize", "improve", "remove",
    "delete", "repo", "repository", "revert", "rollback", "config",
}

FALLBACK_MESSAGE = (
    "I couldn't find a confident answer to that — try rephrasing, or ask again."
)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def is_fix_intent(user_message: str) -> bool:
    """True iff the user's OWN message signals they actually want a
    bug-fix / code-change answer."""
    return bool(_tokens(user_message) & _FIX_INTENT_TOKENS)


def response_seems_mismatched(user_message: str, final_output: str) -> bool:
    """Cheap, deterministic guard: a response that proposes a code-ship
    action (`aurem-handoff` fence) or a "Root cause:" diagnosis, while
    the user's own message carries zero fix/bug/code intent, is very
    likely a recall/hallucination bleed-through rather than a real
    answer to what was asked."""
    if not final_output:
        return False
    if not (_HANDOFF_FENCE_RE.search(final_output) or _ROOT_CAUSE_RE.search(final_output)):
        return False
    return not is_fix_intent(user_message)
