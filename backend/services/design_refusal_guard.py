"""
services/design_refusal_guard.py — P8-B/C/D/E (2026-08-31)

Deterministic guard for design/brand/visual asks (see
design_ask_detector.py): kills the "I need design assets / brand
guidelines / strategy docs you should have" dead-end refusal, and
the "in this session" / "I don't have access to" jargon leak that
often rides along with it. Same architecture as no_dead_end_guard.py
/ self_bug_reply_guard.py — regex checks, a ready-made compliant
reply for the deterministic short-circuit case, no LLM in the check.
"""
from __future__ import annotations

import re

_REFUSAL_RE = re.compile(
    r"(i\s*(can'?t|cannot|am unable|'?m not able)\b.{0,60}"
    r"(design (asset|guideline)|brand (guideline|book|strategy|asset|docs?)|"
    r"strategy (docs?|documents)))|"
    r"(you(?:'d| would| should)? provide (design (asset|guideline)|"
    r"brand (guideline|book|docs?)))|"
    r"(i (don'?t|do not) (currently )?have (access to )?.{0,40}"
    r"(session|design (asset|guideline)|brand docs?))|"
    r"send (me |us )?your brand (book|guidelines?)",
    re.IGNORECASE,
)

_SESSION_JARGON_RE = re.compile(
    r"\bin (this|the) session\b|\bthis session\b|"
    r"i (don'?t|do not|didn'?t) (currently )?have access to\b|"
    r"access to (your|the) (design (asset|guideline)|brand docs?|"
    r"strategy docs?)",
    re.IGNORECASE,
)

_CAN_DO_NOW_RE = re.compile(
    r"i can (do|start|give you|apply|make)\b.{0,60}"
    r"(right now|now|today|on your site)",
    re.IGNORECASE,
)

_DIRECTIONS_RE = re.compile(
    r"(clean\s*&?\s*minimal|bold\s*&?\s*confident|warm\s*&?\s*friendly|"
    r"premium\s*&?\s*elegant|\btop pick\b)",
    re.IGNORECASE,
)

_ONE_INPUT_QUESTION_RE = re.compile(r"\?")

_SCOPE_LINE_RE = re.compile(
    r"bigger (project|thing|engagement)", re.IGNORECASE,
)


def has_refusal(text: str) -> bool:
    return bool(_REFUSAL_RE.search(text or ""))


def has_session_jargon(text: str) -> bool:
    return bool(_SESSION_JARGON_RE.search(text or ""))


def has_can_do_now(text: str) -> bool:
    return bool(_CAN_DO_NOW_RE.search(text or ""))


def has_concrete_directions(text: str) -> bool:
    return bool(_DIRECTIONS_RE.search(text or ""))


def asks_at_most_one_input(text: str) -> bool:
    """True when there's at most ONE question mark (one thing asked),
    never a multi-item deliverables list."""
    return (text or "").count("?") <= 1


def has_honest_scope_line(text: str) -> bool:
    return bool(_SCOPE_LINE_RE.search(text or ""))


def is_compliant_design_reply(text: str) -> bool:
    """The P8-B guarantee, all at once."""
    return (
        not has_refusal(text)
        and not has_session_jargon(text)
        and has_can_do_now(text)
        and has_concrete_directions(text)
        and asks_at_most_one_input(text)
    )


DEFAULT_DESIGN_REPLY = (
    "Got it — you want a fresher look for your brand and site. I can "
    "do a real visual refresh on your site right now: colors, fonts, "
    "spacing, and layout — and show you a before/after before "
    "anything goes live.\n\n"
    "A few directions that could fit: Clean & minimal (lots of white "
    "space, one accent color, a big clear headline), Bold & confident "
    "(strong color, bold type, sharp contrast), or Warm & friendly "
    "(soft colors, relaxed spacing). My top pick would be Clean & "
    "minimal to start.\n\n"
    "Tell me one word for the feel you want — calm, bold, or premium "
    "— or just say go and I'll start with my top pick. A brand-new "
    "logo or a full brand-strategy book is a bigger project — I can "
    "help you start that too, but the site's look I can do right now."
)


def strip_design_refusal(text: str) -> str:
    """If `text` is a design-ask reply that doesn't meet the FULL P8
    guarantee (is_compliant_design_reply) — a refusal, a session-
    jargon leak, OR simply missing the can-do-now/directions/one-
    input contract — replace it wholesale with the guaranteed default.
    2026-08-31 fix (Gate 2 testing_agent finding): the original
    version only replaced on a literal has_refusal/has_session_jargon
    match, which real model output routinely sidesteps (e.g. 'I focus
    on publishing code — hit me with a GitHub issue' is a refusal in
    spirit but matches neither regex). Gating on the FULL compliance
    check closes that gap — the same function the unit tests already
    treat as the guarantee is now actually wired as the gate."""
    if not text:
        return text
    if not is_compliant_design_reply(text):
        return DEFAULT_DESIGN_REPLY
    return text
