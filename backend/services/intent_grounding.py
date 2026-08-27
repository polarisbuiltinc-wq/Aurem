"""
services/intent_grounding.py — 2026-08-27, P1 (Journey/Intent-Grounding
build round).

Root cause fixed here: `services/ambiguity_gate.is_ambiguous_task()` flags
ANY message under 4 words as "too broad" — including a bare "yes" typed
in direct reply to ORA's OWN concrete proposal (a scan report, a numbered
fix list). That heuristic is correct for a genuinely vague opener ("fix
it", "improve the app") but wrong for a confirmatory reply to a proposal
ORA itself just made — a "yes" there is FULLY SCOPED by the proposal it's
replying to, not ambiguous at all.

This module is the single place that:
  1. Detects a short confirmatory reply (yes/ok/ship it/go ahead/etc).
  2. Resolves it against a `pending_scan` a Mode E audit/scan wrote onto
     the chat_sessions doc (see routers/chat.py's Mode E block).
  3. Returns the RESOLVED scope (findings list) so the caller can skip
     the ambiguity gate entirely and hand the loop a fully-grounded task,
     never a bare "yes".

Deliberately reuses the EXISTING `chat_sessions` collection — no new
collection. `pending_scan` is consumed (cleared) on first successful
resolution so a later unrelated "yes" doesn't silently re-fire it.
"""
from __future__ import annotations

import re
import time
from typing import Optional

# Confirmatory replies — short affirmations that only make sense as a
# reply to something ORA already proposed. Deliberately NOT the same
# list as chat_helpers._FIX_CONFIRM (that one also matches "fix it" /
# "apply the fix", which carry their OWN scope and don't need a prior
# proposal to be meaningful).
_CONFIRMATORY = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok|okay|k|go ahead|go for it|do it|"
    r"ship it|ship them|ship these|fix them|fix these|apply it|"
    r"apply them|sounds good|lgtm|approved?|confirmed?|proceed|"
    r"do that|please do|go)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# A pending scan older than this is stale — don't silently apply a
# 3-day-old audit's findings to a fresh "yes".
_PENDING_SCAN_TTL_S = 2 * 60 * 60  # 2 hours

NO_PENDING_PROPOSAL_MESSAGE = (
    "I don't have a pending proposal to confirm yet — what would you "
    "like me to fix? Name a file, page, or say \"fix the findings from "
    "my last scan\" if you meant that."
)


def is_confirmatory_reply(message: str) -> bool:
    """True for a short affirmative reply with NO scope of its own —
    the exact shape of message that must inherit a prior proposal's
    scope rather than being judged ambiguous on its own words."""
    return bool(_CONFIRMATORY.match((message or "").strip()))


async def get_pending_scan(db, user_id: str, session_id: str) -> Optional[dict]:
    """Returns the {findings, project_id, created_at} pending_scan doc
    if present and not stale, else None."""
    if db is None or not session_id:
        return None
    sess = await db.chat_sessions.find_one(
        {"session_id": session_id, "user_id": user_id},
        {"_id": 0, "pending_scan": 1},
    )
    pending = (sess or {}).get("pending_scan")
    if not pending or not pending.get("findings"):
        return None
    if (time.time() - float(pending.get("created_at", 0))) > _PENDING_SCAN_TTL_S:
        return None
    return pending


async def clear_pending_scan(db, user_id: str, session_id: str) -> None:
    if db is None or not session_id:
        return
    await db.chat_sessions.update_one(
        {"session_id": session_id, "user_id": user_id},
        {"$unset": {"pending_scan": ""}},
    )


def render_grounded_task(findings: list[dict]) -> str:
    """Turns a findings list into an unambiguous task string the
    planner LLM gets as `user_message` — every file+line explicit, so
    there's no reason for it to go hunting for OTHER files."""
    files = sorted({f["filepath"] for f in findings if f.get("filepath")})
    lines = [
        f"Fix ONLY the following {len(findings)} finding(s) from the most "
        f"recent scan. Do not touch any file outside this list "
        f"({', '.join(files)}):",
    ]
    for f in findings:
        loc = f"{f.get('filepath')}:{f.get('line')}" if f.get("line") else f.get("filepath")
        lines.append(f"- {loc} — {f.get('description', '')} "
                     f"(severity={f.get('severity', 'medium')})"
                     + (f" Fix: {f['fix']}" if f.get("fix") else ""))
    return "\n".join(lines)


async def resolve_confirmatory_scope(
    db, user_id: str, session_id: Optional[str], message: str,
) -> dict:
    """Main entry point for a caller (e.g. /loop/start) about to run
    the ambiguity gate.

    Returns:
      {"grounded": False} — not a confirmatory reply, run the normal
                            ambiguity-gate path unchanged.
      {"grounded": True, "task_text": str, "source_findings": list}
                          — confirmatory reply resolved against a real
                            pending proposal; skip the ambiguity gate,
                            use task_text + attach source_findings.
      {"grounded": True, "no_pending": True}
                          — confirmatory reply but NOTHING pending to
                            confirm; caller should ask once, not run
                            the "too broad" ambiguity gate on it.
    """
    if not is_confirmatory_reply(message):
        return {"grounded": False}
    if not session_id:
        return {"grounded": True, "no_pending": True}
    pending = await get_pending_scan(db, user_id, session_id)
    if not pending:
        return {"grounded": True, "no_pending": True}
    findings = pending["findings"]
    await clear_pending_scan(db, user_id, session_id)
    return {
        "grounded": True,
        "task_text": render_grounded_task(findings),
        "source_findings": findings,
    }
