"""
services/no_dead_end_guard.py — R2 addendum, Filter 14.5 (2026-08-31)

DETERMINISTIC guard: a user-facing reply that says "can't" / "cannot"
/ "unable to" must ALSO offer a concrete alternative in the same
reply ("but I can ..."). No LLM in this layer — pure regex over the
model's OWN output, same discipline as bail_reason.py /
business_voice_filter.py.

The model is told to always follow this pattern (see
BUSINESS_OWNER_VOICE_CONTRACT in routers/chat.py); this module is the
guarantee for when it forgets.
"""
from __future__ import annotations

import re

_CANT_RE = re.compile(
    r"\b(can'?t|cannot|can\s+not|unable to|not able to)\b", re.IGNORECASE,
)
_BUT_I_CAN_RE = re.compile(
    r"\bbut\s+i\s+can\b|\bhowever,?\s+i\s+can\b|\binstead,?\s+i\s+can\b"
    r"|\bi\s+can\s+(also|however|instead)\b",
    re.IGNORECASE,
)
# 2026-08-31 (Gate 1 testing_agent finding) — a reply that already
# offers a real next step ("pick your website project from the
# sidebar", "once it's connected, I'll add...") is NOT a dead end,
# even if it also contains the word "can't" earlier ("I can't read
# your pages yet"). Appending DEFAULT_ALTERNATIVE on top of an
# already-real path forward produced an awkward, redundant
# double-ask in production.
_POSITIVE_PATH_FORWARD_RE = re.compile(
    r"pick your website project|here'?s what to do|"
    r"once it'?s connected|the moment it'?s connected|"
    r"connect your (website|site|project)|i'?ll add\b",
    re.IGNORECASE,
)

DEFAULT_ALTERNATIVE = (
    "but I can update the text, images, or layout on your site instead "
    "— want me to do that?"
)


def has_dead_end(text: str) -> bool:
    """True when the reply says it can't do something with no
    accompanying alternative — real ("but I can...") or a positive
    path-forward statement — anywhere in the same reply."""
    text = text or ""
    if not _CANT_RE.search(text):
        return False
    if _BUT_I_CAN_RE.search(text) or _POSITIVE_PATH_FORWARD_RE.search(text):
        return False
    return True


def ensure_alternative(text: str, alternative: str | None = None) -> str:
    """Deterministic — appends a concrete "but I can..." alternative
    when a dead end is detected; a no-op otherwise."""
    if not text or not has_dead_end(text):
        return text
    alt = alternative or DEFAULT_ALTERNATIVE
    stripped = text.rstrip()
    if stripped.endswith((".", "!", "?")):
        # A brand-new sentence after terminal punctuation is
        # capitalised — "But I can..." not "but I can...".
        joiner, alt = " ", alt[0].upper() + alt[1:]
    else:
        joiner = " — "
    return f"{stripped}{joiner}{alt}"
