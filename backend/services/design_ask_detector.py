"""
services/design_ask_detector.py — P8-B (2026-08-31)

Deterministic classifier: is this message a design/visual/brand ask
("redesign our brand identity," "make my site look better," "our
colors feel dated")? No LLM — pure regex, same discipline as
services/user_report_classifier.py.

A design ask is DIFFERENT from a normal website fix ("fix the typo
on my about page") — it needs the P8-B "never refuse" guard applied,
not the ordinary fix flow.
"""
from __future__ import annotations

import re

_DESIGN_ASK_RE = re.compile(
    r"\b(redesign|rebrand|re-brand|refresh|moderniz\w*)\b|"
    r"make it (look|feel) (better|nicer|more \w+)|"
    r"\bstyle(?:ing)? (the|our|my)\b|"
    r"(our|my) (brand|logo|colou?rs?|fonts?|typography|aesthetics?|"
    r"vibe|theme)\b.{0,30}(feel|look|dated|old|boring|outdated)?|"
    r"(website|site) (look|design)\b",
    re.IGNORECASE,
)

# A normal, scoped website FIX must never be swept up as a design ask
# just because it mentions a visual word in passing (the typo case).
_SCOPED_FIX_RE = re.compile(
    r"\b(fix|correct|update) (the |a |my |our )?(typo|spelling|text|"
    r"bug|link|button click|broken link)\b",
    re.IGNORECASE,
)


def is_design_ask(text: str) -> bool:
    """Deterministic — same input always gives the same answer."""
    text = text or ""
    if _SCOPED_FIX_RE.search(text):
        return False
    return bool(_DESIGN_ASK_RE.search(text))
