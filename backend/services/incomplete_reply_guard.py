"""
services/incomplete_reply_guard.py — R2 addendum, Filter 14.2 (2026-08-31)

DETERMINISTIC guard against the "started a sentence, never finished
it" bug: a reply that ends with a dangling promise ("let me...",
"here's what I found:", "...") must be completed before it reaches
the user. No LLM regeneration here (that would cost latency/$ on
every turn and isn't deterministic) — a fixed, honest completion
line is appended instead, same "guarantee layer" discipline as
no_dead_end_guard.py / bail_reason.py.

Deliberately narrow (3 concrete patterns only) to avoid false
positives on legitimately short/complete replies ("Done." / a single
word / a markdown list with no trailing period) — see the module
docstring discussion in the R1-R5 handoff for why a blanket
"no terminal punctuation" rule was rejected.
"""
from __future__ import annotations

import re

_DANGLING_PHRASE_RE = re.compile(
    r"(let me\b[^.!?]*$|i('|')?ll now\b[^.!?]*$|i will now\b[^.!?]*$"
    r"|here'?s what[^.!?:]*:?\s*$|more (details|coming)[^.!?]*$"
    r"|in a (moment|bit|second)[^.!?]*$|going to (check|look|dig)\b[^.!?]*$)",
    re.IGNORECASE,
)

COMPLETION_LINE = "That's the main point. Want the full details?"


def is_incomplete(text: str) -> bool:
    """Deterministic — same input always gives the same answer."""
    if not text:
        return False
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped.endswith(":") or stripped.endswith("..."):
        return True
    if _DANGLING_PHRASE_RE.search(stripped):
        return True
    return False


def ensure_complete(text: str) -> str:
    """Appends a short, honest completion line when the reply dangles;
    a no-op otherwise."""
    if not text or not is_incomplete(text):
        return text
    stripped = text.rstrip()
    stripped = stripped.rstrip(".").rstrip(":").rstrip(".")
    return f"{stripped}. {COMPLETION_LINE}"
