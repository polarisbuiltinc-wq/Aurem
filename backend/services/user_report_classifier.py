"""
services/user_report_classifier.py — P7-D (2026-08-31)

Deterministic classifier: is the user saying "ORA/your app is
broken" (its own UI, button, reply, panel) vs describing a problem
on THEIR OWN website ("my homepage's button doesn't work")? No LLM
— pure regex, same discipline as the other guard modules in this
rework.

Confusable pair, decided by SUBJECT:
  "the approve button isn't showing"        -> ORA's own UI (self-bug)
  "the button on my website doesn't work"   -> the user's site (normal task)
"""
from __future__ import annotations

import re

# Possessive references to the OWNER'S OWN property always win — if
# present, this is a normal website task, never a self-bug report,
# even if the sentence also matches an "ORA-ish" pattern below.
_OWN_SITE_RE = re.compile(
    r"\b(my|our)\s+(website|site|homepage|home\s?page|page|store|shop|"
    r"business)\b",
    re.IGNORECASE,
)

_ORA_SELF_BUG_PATTERNS = [
    # NOTE: deliberately narrower than "...not working" — that phrase
    # is the single most common LEGITIMATE website bug-fix request
    # ("the checkout button doesn't work") and must never be treated
    # as a report about ORA itself. Only true UI-rendering verbs
    # (show/appear/load/render) plus an ORA-surface word (approve/
    # approval) count as a self-bug signal.
    re.compile(
        r"(the |that )?(approve|approval)\s*button\b.{0,40}\b"
        r"(not|didn'?t|isn'?t|won'?t)\b.{0,20}"
        r"(show|appear|load|render)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bbutton\b.{0,40}\b(not|didn'?t|isn'?t|won'?t)\b.{0,20}"
        r"(show up|appear|load|render)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(your|the|its)\s+(screen|preview|panel|box)\b.{0,30}"
        r"(blank|empty|white|stuck|frozen)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(reply|message|response)\b.{0,20}"
        r"(cut( it)? off|stopped|ended mid|truncated|cut short)",
        re.IGNORECASE,
    ),
    re.compile(
        r"keeps? (saying|telling) (me )?(to )?"
        r"(try again|rephrase|ask again)",
        re.IGNORECASE,
    ),
    re.compile(
        r"nothing (happened|worked|changed) (when|after) "
        r"(i|you)\b.{0,15}(click|tap|approve)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\byou('re| are)? (broken|stuck|frozen|not working)\b",
        re.IGNORECASE,
    ),
]


def is_user_reporting_ora_bug(text: str) -> bool:
    """Deterministic — True only when the message is about ORA's OWN
    UI/reply/panel. Always False when the message names the owner's
    own site/page/business (the possessive guard wins over any
    pattern below)."""
    text = text or ""
    if _OWN_SITE_RE.search(text):
        return False
    return any(p.search(text) for p in _ORA_SELF_BUG_PATTERNS)
