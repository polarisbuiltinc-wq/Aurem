"""
services/onboarding_first_task_nudge.py — Connect-flow investigation,
Bug-1 fix (2026-09-01).

Justin Trammell and Jolene Boyles both completed connect → scan →
project-created → success, then got silence: no guided next step, so
they stopped (Justin) or got confused and re-tried the install
(Jolene). This is NOT a connect-flow bug — it's a missing nudge.

Fires ONCE per user (piggybacking on the SAME one-shot gate
`trigger_first_scan()` already uses via `dev_users.first_scan_at` —
no separate flag needed), the moment their first scan completes.
Writes a single ORA-authored assistant turn directly into a new
`chat_sessions` doc for that project, so the message is already
sitting there the next time the owner opens chat — no LLM call, one
concrete example, never the dead-end "what can I help you with?".
"""
from __future__ import annotations

import time


def build_first_task_nudge(*, findings_count: int) -> str:
    """Deterministic, plain business-owner voice. Always names ONE
    concrete first task the owner can act on immediately."""
    if findings_count > 0:
        return (
            f"Great — your site's connected, and I already ran a first check. "
            f"I found {findings_count} thing{'s' if findings_count != 1 else ''} "
            f"I can improve — want me to start with the first one, or tell me "
            f"anything else you'd like changed?"
        )
    return (
        "Great — your site is connected. Here's a first thing I can do: "
        "want me to add your phone number to the bottom of your page, or "
        "check what's slow on your homepage? Tell me which, or type "
        "anything you'd like changed."
    )


async def send_onboarding_first_task_nudge(
    *, db, user_id: str, project_id: str, findings_count: int,
) -> None:
    """Best-effort, never raises — same error-swallowing contract as
    the rest of the onboarding-first-scan flow it's called from."""
    try:
        text = build_first_task_nudge(findings_count=findings_count)
        session_id = f"onboard-nudge-{project_id}"
        now = time.time()
        await db.chat_sessions.update_one(
            {"session_id": session_id, "user_id": user_id},
            {
                "$setOnInsert": {
                    "session_id": session_id,
                    "user_id": user_id,
                    "project_id": project_id,
                    "created_at": now,
                    "title": "Getting started",
                },
                "$set": {
                    "updated_at": now,
                    "last_message": text[:120],
                },
                "$push": {
                    "turns": {
                        "role": "assistant",
                        "content": text,
                        "ts": now,
                        "provider": "onboarding_nudge",
                    },
                },
            },
            upsert=True,
        )
    except Exception:                                              # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "[onboarding-nudge] failed to write first-task nudge for "
            "user=%s project=%s", user_id, project_id, exc_info=True,
        )
