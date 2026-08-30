"""services/session_summary.py — 2026-08-30, Issue C fix (F2).

Keeps a short (2-3 line) rolling summary on each `chat_sessions` doc so
a session that has grown beyond the dynamic history window (see
`orchestrator.py::_select_history_window`) doesn't fully lose its
earliest turns from the model's context — the summary is ALWAYS
included in the transcript regardless of how much raw turn history
fits this turn.

Fire-and-forget, cheap (only every `_UPDATE_EVERY_N_TURNS` turns, one
short DeepSeek call via the existing `services.llm.call_llm`
transport — no new deps, no new model), and fails silently: a
summary-update hiccup must never affect the chat turn already
returned to the user.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UPDATE_EVERY_N_TURNS = 10
_MAX_TURNS_INTO_SUMMARIZER = 40  # cap the summarizer's own input size


async def maybe_update_summary(db, session_id: str, user_id: str) -> None:
    if db is None or not session_id:
        return
    try:
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0, "turns": 1, "summary": 1, "summary_turn_count": 1},
        )
        if not doc:
            return
        turns = doc.get("turns") or []
        n = len(turns)
        last_at = int(doc.get("summary_turn_count") or 0)
        if n < _UPDATE_EVERY_N_TURNS or (n - last_at) < _UPDATE_EVERY_N_TURNS:
            return
        prior_summary = (doc.get("summary") or "").strip()
        lines = []
        for t in turns[-_MAX_TURNS_INTO_SUMMARIZER:]:
            if not isinstance(t, dict):
                continue
            role = t.get("role", "user")
            content = (t.get("content") or "").strip()[:600]
            if content:
                lines.append(f"[{role.upper()}] {content}")
        transcript = "\n".join(lines)
        if not transcript:
            return
        from services.llm import call_llm
        system = (
            "Summarize this developer-chat conversation in 2-3 short "
            "plain-text lines: what the user is trying to do, what has "
            "been found or done so far, and the likely next step. No "
            "markdown, no preamble, no headers."
        )
        prior_block = f"Previous summary: {prior_summary}\n\n" if prior_summary else ""
        summary = await call_llm(
            [{"role": "user", "content": f"{prior_block}Conversation so far:\n{transcript}"}],
            system=system, max_tokens=120, temperature=0.3,
        )
        summary = (summary or "").strip()
        if not summary:
            return
        await db.chat_sessions.update_one(
            {"session_id": session_id, "user_id": user_id},
            {"$set": {"summary": summary, "summary_turn_count": n}},
        )
    except Exception as e:
        logger.debug("session_summary.maybe_update_summary skipped: %r", e)
