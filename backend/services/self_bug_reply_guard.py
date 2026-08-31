"""
services/self_bug_reply_guard.py — P7-E (2026-08-31)

Deterministic check + composer for the reply ORA gives when IT knows
something on its OWN side is broken (not the user's website). The
pattern is ENFORCED, not just prompted: ownership first, a plain
explanation (no error codes/filenames), a real path forward, and
never a hint that the user's own browser/cache/connection is at
fault. Same guard architecture as no_dead_end_guard.py /
incomplete_reply_guard.py — deterministic regex, no LLM in the check.
"""
from __future__ import annotations

import re

_OWNERSHIP_RE = re.compile(
    r"that'?s (on me|my (mistake|fault|glitch|side)|"
    r"not your (website|site|homepage|page|fault))|"
    r"i('m| am) sorry|sorry about that|my (mistake|fault)|"
    r"you'?re right[,]? and i'?m sorry",
    re.IGNORECASE,
)
# Ownership must show up EARLY (first sentence) — a reply that only
# apologizes as an afterthought at the end doesn't count.
_OWNERSHIP_WINDOW_CHARS = 140

_BLAME_USER_RE = re.compile(
    r"\b(check|checking|clear|clearing|try|trying) your (browser|cache|"
    r"connection|cookies|device|internet)\b|"
    r"\btry (a )?different (browser|device)\b|"
    r"\brefresh(ing)? the page\b|"
    r"\btry rephrasing\b",
    re.IGNORECASE,
)

_PATH_FORWARD_RE = re.compile(
    r"let me try (that )?(once more|again)|"
    r"\bi can still\b|\bi'?ll try (again|once more)\b|"
    r"here'?s what i can (still )?do|"
    r"i've flagged (it|this)",
    re.IGNORECASE,
)

_ERROR_CODE_RE = re.compile(
    r"\b[45]\d{2}\b|traceback|stack ?trace|null ?pointer|"
    r"connectionerror|parse error|"
    r"\b[\w\-./]+\.(?:py|jsx?|tsx?|json|md)\b",
    re.IGNORECASE,
)


def has_ownership(text: str) -> bool:
    text = text or ""
    m = _OWNERSHIP_RE.search(text)
    return bool(m and m.start() <= _OWNERSHIP_WINDOW_CHARS)


def blames_user(text: str) -> bool:
    return bool(_BLAME_USER_RE.search(text or ""))


def has_path_forward(text: str) -> bool:
    return bool(_PATH_FORWARD_RE.search(text or ""))


def has_error_code(text: str) -> bool:
    return bool(_ERROR_CODE_RE.search(text or ""))


def is_compliant_self_bug_reply(text: str) -> bool:
    """All four P7-E rules at once — the single gate the response path
    checks before a self-bug reply reaches the owner."""
    return (
        has_ownership(text)
        and not blames_user(text)
        and has_path_forward(text)
        and not has_error_code(text)
    )


# Ready-made, GUARANTEED-compliant replies — used when there's no
# time/need to regenerate via the model (e.g. a deterministic
# short-circuit for a user_reported self-bug).
_DEFAULT_TEMPLATES = {
    "missing_button": (
        "That's on me — the button that normally lets you approve a "
        "fix didn't finish loading. Let me try that again — no need "
        "to refresh anything on your end. I've flagged this so it "
        "stops happening."
    ),
    "truncated_reply": (
        "That's on me, not your page — I got cut off there. I can "
        "still give you the rest right now, want me to?"
    ),
    "blank_ui": (
        "That's on me, not your site — my screen went blank. Let me "
        "reload it for you right now, and I can still open it in a "
        "new tab if it doesn't come back."
    ),
    "tool_error": (
        "That's on me, not your website — something didn't go "
        "through on my end just now. Let me try that again, and I "
        "can still tell you exactly what's possible right now if it "
        "doesn't work."
    ),
    "user_reported": (
        "You're right, and I'm sorry — that's on me, not your "
        "website. Let me try that again, and I've flagged it so we "
        "fix it properly."
    ),
}
_GENERIC_TEMPLATE = (
    "That's on me, not your website. Let me try again, and if that "
    "doesn't work, I'll tell you exactly what I can still do for you."
)


def compose_self_bug_reply(bug_type: str) -> str:
    """A ready-made reply for `bug_type`, GUARANTEED to pass
    `is_compliant_self_bug_reply()` (locked by a unit test)."""
    return _DEFAULT_TEMPLATES.get(bug_type, _GENERIC_TEMPLATE)
