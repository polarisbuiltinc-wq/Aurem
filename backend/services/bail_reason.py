"""
services/bail_reason.py — R2 (2026-08-31)

DETERMINISTIC classifier for WHY response_confidence.py's gate bailed,
replacing the useless "try rephrasing, or ask again" fallback with a
concrete next step. No LLM in this layer — the model already tried
twice (routers/chat.py's retry-once flow) and both attempts were
rejected by the gate; this is a pure heuristic over the user's OWN
words, same discipline as response_confidence.py's own deterministic
regex gates.

Every bail carries a `reason` field:
  - "missing_data"  — the request is clear, but a concrete value it
                       needs (hours, phone number, ...) is absent.
                       Ask for THAT value, plain words, one question.
  - "out_of_scope"   — the request needs something ORA structurally
                       can't do (call someone, process a payment...).
  - "low_confidence" — genuinely ambiguous; ONE specific clarifying
                       question, never "rephrase" / "tell me more".
"""
from __future__ import annotations

import re

# (keyword regex, expected-value-pattern regex, plain-language question)
_DATA_TOPICS: list[tuple[re.Pattern, re.Pattern, str]] = [
    (
        re.compile(r"\bhours?\b|\bopen\b|\bopening\b|\bclosing\b", re.IGNORECASE),
        re.compile(r"\b\d{1,2}\s*(:\d{2})?\s*(am|pm|a\.m\.|p\.m\.)\b", re.IGNORECASE),
        "What are your opening hours? Tell me the times (e.g. \"9am to 5pm\") and I'll add them.",
    ),
    (
        re.compile(r"\bphone\b|\bnumber\b|\bcall us\b", re.IGNORECASE),
        re.compile(r"\b\d[\d\-\s().]{6,}\d\b"),
        "What's the phone number? Send me the digits and I'll add it.",
    ),
    (
        re.compile(r"\baddress\b|\blocation\b|\bwhere.{0,10}located\b", re.IGNORECASE),
        re.compile(r"\b\d+\s+\w+"),
        "What's the address? Send me the street and city and I'll add it.",
    ),
    (
        re.compile(r"\bprice\b|\bcost\b|\bpricing\b", re.IGNORECASE),
        re.compile(r"[$\u20ac\u00a3]\s?\d|\b\d+\s?(dollars|usd|bucks)\b", re.IGNORECASE),
        "What's the price? Send me the number and I'll add it.",
    ),
    (
        re.compile(r"\bemail\b|\be-mail\b", re.IGNORECASE),
        re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
        "What's the email address? Send it over and I'll add it.",
    ),
]

_OUT_OF_SCOPE_RE = re.compile(
    r"\bcall my customers\b|\bmail (a|the) (letter|package)\b|"
    r"\bprocess (a|the) payment\b|"
    r"\bpost (this|that) on (facebook|instagram|twitter|x)\b",
    re.IGNORECASE,
)

_OUT_OF_SCOPE_MESSAGE = (
    "I can't do that directly, but I can update the text, images, or "
    "layout on your site — want me to do that instead?"
)
_LOW_CONFIDENCE_MESSAGE = (
    "Just to make sure I get this right — what exactly should change, "
    "and where on your site? (e.g. \"add our phone number to the "
    "bottom of my main page\")"
)

# R2 — a deterministic ban list. If the model's OWN reply ever emits
# one of these anyway, it is caught and replaced (see
# strip_banned_fallback_phrases below) rather than shown as-is.
_BANNED_FALLBACK_RE = re.compile(
    r"try\s+rephrasing|try\s+again|ask\s+again|try\s+a\s+different\s+way|"
    r"\brephrase\b|"
    # R2 addendum (2026-08-31) — Filter 14.3 broadened per founder's
    # full ruleset doc: "I'm not confident" / "please clarify" /
    # "be more specific" are the SAME dead-end pattern as "rephrase",
    # just worded differently. Caught here so the whole reply is
    # replaced with a concrete next step instead of these either.
    r"i'?m\s+not\s+confident|i\s+am\s+not\s+confident|"
    r"could\s?n'?t\s+find\s+a\s+confident\s+answer|"
    r"could\s+not\s+find\s+a\s+confident\s+answer|"
    r"please\s+clarify|clarify\s+your\s+request|"
    r"can\s+you\s+be\s+more\s+specific|"
    r"provide\s+more\s+(detail|context|information)",
    re.IGNORECASE,
)


def classify_bail(user_message: str) -> dict:
    """Deterministic — same input always gives the same output.
    Returns {"reason": ..., "message": <plain-language next step>}."""
    msg = user_message or ""
    if _OUT_OF_SCOPE_RE.search(msg):
        return {"reason": "out_of_scope", "message": _OUT_OF_SCOPE_MESSAGE}
    for keyword_re, value_re, question in _DATA_TOPICS:
        if keyword_re.search(msg) and not value_re.search(msg):
            return {"reason": "missing_data", "message": question}
    return {"reason": "low_confidence", "message": _LOW_CONFIDENCE_MESSAGE}


def contains_banned_fallback_phrase(text: str) -> bool:
    return bool(_BANNED_FALLBACK_RE.search(text or ""))


def strip_banned_fallback_phrases(text: str, user_message: str) -> str:
    """Safety net for R2's copy ban: if a banned phrase ("try
    rephrasing", ...) slips into a reply from ANY path (not just the
    confidence-gate fallback this module primarily serves), replace
    the WHOLE reply with a concrete next step instead of leaving the
    useless phrase in front of the user."""
    if not contains_banned_fallback_phrase(text):
        return text
    return classify_bail(user_message)["message"]
