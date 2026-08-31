"""
services/self_bug.py — P7 SELF-REPAIR (2026-08-31)

ORA recognizing its OWN bugs (not the user's website) — detect,
diagnose, log, learn. Honest scope: this module NEVER writes to
ORA's own deployed code. It only logs a structured bug report + an
optional PROPOSED patch (a string) for a human/CI to review — the
same PR-only + human-approve standard used for every user repo
(see services/local_tools.py write paths). There is no unattended
self-apply path here, by construction: `emit()`/`diagnose()` are
read/log-only.

Same determinism discipline as the other new guard modules in this
rework — no LLM in detection/classification; `diagnose()` only ever
states a cause that is actually IN `_KNOWN_CAUSES`, never invents one
to sound confident (no-false-claims).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from pymongo import ReturnDocument

from cto_services.db import get_db

logger = logging.getLogger(__name__)

SELF_BUG_TYPES = frozenset({
    "missing_button", "truncated_reply", "dead_end_leak", "jargon_leak",
    "tool_error", "stalled_silence", "blank_ui", "user_reported",
})

# type -> the ONLY cause we ever state for it. Anything not backed by
# a row here comes back as confidence="uncertain" — never a guess.
_KNOWN_CAUSES = {
    "missing_button": "the Approve-the-fix button's rendering gate (extractHandoffBrief) didn't find a valid fence in the reply",
    "truncated_reply": "the reply ended before a complete sentence — the completeness guard caught it",
    "dead_end_leak": "the reply contained a banned dead-end phrase (try rephrasing / not confident) before the guard rewrote it",
    "jargon_leak": "the reply contained a raw filename or dev term before the voice filter caught it",
    "tool_error": "a tool call ORA made returned an error",
    "stalled_silence": "no visible progress was emitted for an extended period during tool work",
    "blank_ui": "a UI panel rendered with no content",
    "user_reported": "the user described ORA's own behavior (not their website) as broken",
}
_HIGH_SEVERITY_TYPES = frozenset({"missing_button", "tool_error", "user_reported"})


@dataclass
class SelfBugEvent:
    type: str
    source: str
    evidence: str
    context: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@dataclass
class Diagnosis:
    what_user_saw: str
    what_ora_detected: str
    likely_cause: str
    evidence: str
    confidence: str  # "confirmed" | "likely" | "uncertain"
    proposed_fix: Optional[str] = None
    patch: Optional[str] = None
    severity: str = "low"


def diagnose(event: SelfBugEvent) -> Diagnosis:
    """Honest, no-invented-cause diagnosis. Only states a cause this
    module actually KNOWS from `event.type` — never guesses beyond
    that, and always frames the fault as ORA's own, never the user's
    website (see services/self_bug_reply_guard.py for the reply that
    uses this)."""
    if event.type not in SELF_BUG_TYPES:
        return Diagnosis(
            what_user_saw=event.evidence or "something didn't work as expected",
            what_ora_detected="an unrecognized signal",
            likely_cause="uncertain — no known-cause mapping for this event type",
            evidence=event.evidence or "",
            confidence="uncertain",
        )
    cause = _KNOWN_CAUSES[event.type]
    confidence = "confirmed" if event.evidence else "likely"
    severity = "high" if event.type in _HIGH_SEVERITY_TYPES else "low"
    return Diagnosis(
        what_user_saw=event.evidence or f"a {event.type.replace('_', ' ')} issue",
        what_ora_detected=f"{event.source} flagged a {event.type.replace('_', ' ')}",
        likely_cause=cause,
        evidence=event.evidence or "",
        confidence=confidence,
        severity=severity,
    )


def signature(event_type: str, context: dict | None = None) -> str:
    """Deterministic recurrence key — the SAME kind of self-bug always
    maps to the same signature regardless of exact wording, so the
    team sees 'this bug, 7 times' instead of 7 fresh diagnoses."""
    subject = (context or {}).get("subject") or event_type
    return f"{event_type}:{subject}"


async def emit(event_type: str, evidence: str = "", context: dict | None = None,
                source: str = "guard") -> Optional[SelfBugEvent]:
    """Best-effort, never raises — a self-bug LOGGING failure must
    never break the real chat turn it's reporting on."""
    if event_type not in SELF_BUG_TYPES:
        logger.warning("self_bug.emit: unknown type %r", event_type)
        return None
    event = SelfBugEvent(type=event_type, source=source, evidence=evidence or "",
                         context=context or {})
    diagnosis = diagnose(event)
    db = get_db()
    if db is None:
        return event
    try:
        await db.ora_self_bugs.insert_one({
            "type": event.type,
            "source": event.source,
            "evidence": event.evidence,
            "context": event.context,
            "what_user_saw": diagnosis.what_user_saw,
            "what_ora_detected": diagnosis.what_ora_detected,
            "likely_cause": diagnosis.likely_cause,
            "confidence": diagnosis.confidence,
            "severity": diagnosis.severity,
            "proposed_fix": diagnosis.proposed_fix,
            "ts": event.ts,
        })
        await _record_recurrence(db, signature(event.type, event.context))
    except Exception as e:  # pragma: no cover
        logger.warning("self_bug.emit insert failed: %r", e)
    return event


async def _record_recurrence(db, sig: str) -> int:
    """Upserts the learned-pattern store. Returns the new times_seen.
    Real learning here = pattern-recognition + a recurrence counter,
    NOT model weight updates — matches the honest scope in the
    module docstring."""
    doc = await db.self_bug_learned.find_one_and_update(
        {"signature": sig},
        {"$inc": {"times_seen": 1}, "$set": {"last_seen": time.time()}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    return (doc or {}).get("times_seen", 1)


async def known_handling(sig: str) -> Optional[dict]:
    """If this signature has been seen before, return its learned
    record (times_seen/last_seen) so the caller can skip re-diagnosis
    and go straight to the known ownership+path-forward reply."""
    db = get_db()
    if db is None:
        return None
    try:
        return await db.self_bug_learned.find_one({"signature": sig}, {"_id": 0})
    except Exception as e:  # pragma: no cover
        logger.warning("self_bug.known_handling lookup failed: %r", e)
        return None


async def self_bug_open(session_id: str, window_seconds: float = 30.0) -> bool:
    """True if a self-bug was logged for this session within the last
    `window_seconds` — signals the CURRENT turn's reply should follow
    the self-bug reply pattern (ownership + path-forward, never
    blaming the user — see services/self_bug_reply_guard.py)."""
    if not session_id:
        return False
    db = get_db()
    if db is None:
        return False
    try:
        cutoff = time.time() - window_seconds
        doc = await db.ora_self_bugs.find_one(
            {"context.session_id": session_id, "ts": {"$gte": cutoff}},
            sort=[("ts", -1)],
        )
        return doc is not None
    except Exception as e:  # pragma: no cover
        logger.warning("self_bug.self_bug_open lookup failed: %r", e)
        return False
