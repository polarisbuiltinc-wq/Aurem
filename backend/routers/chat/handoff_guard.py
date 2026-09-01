"""
routers/chat/handoff_guard.py — shell-command handoff guard.

Extracted out of routers/chat/stream.py (2026-09-08 god-file split,
cohesion follow-up) — these two helpers are self-contained (no
dependency on chat_stream's own closure state) and form one clear
responsibility: detect when the most recent assistant handoff fence
was a shell command, and short-circuit a short follow-up before it
reaches the expensive orchestrator loop. Pure mechanical move — no
behavior change. Still imported by name into routers/chat/stream.py
(`from .handoff_guard import ...`), so existing
`patch("routers.chat.stream._maybe_guard_shell_handoff_followup", ...)`
test mocks keep working unchanged (they patch the name bound inside
stream.py's own namespace, not this module).
"""
from __future__ import annotations
from typing import Optional

from cto_services.db import get_db
from .misc import _HANDOFF_FENCE_RE, _SHELL_COMMAND_TOKENS


def _handoff_brief_is_shell_command(brief: str) -> bool:
    """True iff a handoff brief is clearly a shell command instead of
    a file-edit task. Matches both raw shell text ("pip install x")
    and JSON envelopes ({"command": "pip install x", "files": []}).
    """
    if not brief:
        return False
    blob = brief.lower()
    # Cheap empty-files signal: '"files": []' or '"files":[]' inside
    # a JSON-shaped brief is a strong indicator the LLM wrapped a
    # shell command instead of editing source.
    has_empty_files = '"files": []' in blob or '"files":[]' in blob
    has_command_key = '"command"' in blob
    if has_empty_files and has_command_key:
        return True
    return any(tok in blob for tok in _SHELL_COMMAND_TOKENS)


async def _maybe_guard_shell_handoff_followup(
    *, body, user_id: str,
) -> Optional[str]:
    """Iter 172 — Catch follow-ups to a shell-command handoff before
    they reach the expensive orchestrator loop.

    Failure mode this fixes:
        Turn 1: User asks about Twilio
        Turn 2: AUREM (wrongly) emits an ```aurem-handoff containing
                {"command": "pip install twilio", "files": []}
        Turn 3: User types something like "install", "do it",
                "do it fix the issue properly", "now install it"
        Turn 4: Old code → ship-shortcut OR orchestrator burns 180-365s
                trying to "ship" a shell command. Hang from the user's POV.

    Now: if the most recent assistant message has a shell-command
    handoff fence AND the user's reply is a short follow-up (<= 60
    chars, no file path, no new error context), we return a clear
    "this needs a different mechanism" message instantly. Real
    substantive replies (with file paths, errors, or >60 chars) fall
    through to the normal path.
    """
    db = get_db()
    if db is None:
        return None
    prompt = (body.prompt or "").strip()
    if not prompt or len(prompt) > 60:
        return None
    # Cheap signal: the user is referencing a real path → let it through
    if "/" in prompt or "\\" in prompt:
        return None
    sess = await db.chat_sessions.find_one(
        {"user_id": user_id, "session_id": body.session_id},
        {"messages": 1, "_id": 0},
    )
    msgs = (sess or {}).get("messages") or []
    # Walk back at most 4 turns to find the most recent assistant
    # handoff fence. If it's a shell command, intercept.
    for m in reversed(msgs[-8:]):
        if not isinstance(m, dict):
            continue
        if m.get("role") != "assistant":
            continue
        match = _HANDOFF_FENCE_RE.search(m.get("content") or "")
        if not match:
            # First assistant turn we saw had no handoff — nothing to
            # guard against. Don't keep walking back further or we'll
            # falsely fire on unrelated old handoffs.
            return None
        if _handoff_brief_is_shell_command(match.group(1)):
            return (
                "Heads-up: my previous spec was a shell command "
                "(`pip install` / `npm install` / etc.), which the "
                "`aurem-handoff` mechanism can't ship — it only "
                "commits **file edits** to your repo.\n\n"
                "What I CAN do instead:\n"
                "• Add the dependency to your manifest "
                "(`requirements.txt`, `package.json`, `Pipfile`, …) — "
                "your deploy pipeline installs it on next deploy.\n\n"
                "Reply with something like:\n"
                "  - _\"add twilio to requirements.txt\"_\n"
                "  - _\"add @stripe/stripe-js to package.json\"_\n\n"
                "and I'll spec the file edit cleanly."
            )
        # Found a non-shell handoff → defer to normal flow.
        return None
    return None
