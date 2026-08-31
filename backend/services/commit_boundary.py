"""
services/commit_boundary.py — 2026-09-05 Commit-Boundary class fix
(integration glue).

ONE call site each in routers/chat.py for /chat/send and
/chat/stream. Gated by `COMMIT_BOUNDARY_ENABLED` (backend/.env,
default "true"):

  ON  (default) — routes to the new deterministic PendingAction
       machine (services/actions/pending_action.py). A confirmation
       is resolved as a server-side state transition, never a model
       turn.
  OFF — falls back verbatim to the prior (2026-09-04)
       services/confirm_execution.py guard-based behavior — founder's
       explicit rollback switch, no code change needed to flip back
       if the new mechanism needs to be pulled.
"""
from __future__ import annotations

import os
import re
from typing import Optional


def commit_boundary_enabled() -> bool:
    v = (os.environ.get("COMMIT_BOUNDARY_ENABLED") or "true").strip().lower()
    return v not in ("false", "0", "off", "no")


async def resolve_turn_start(
    db, *, user: dict, session_id: str, project_id: Optional[str],
    prompt: str, bin_ctx,
) -> Optional[dict]:
    """Checked BEFORE tier classification / any LLM call. Returns a
    chat-result-shaped dict when a confirm/cancel intent was resolved
    deterministically; returns None to let the caller fall through to
    normal routing (a genuine new request)."""
    if commit_boundary_enabled():
        from services.actions.pending_action import resolve_confirm
        return await resolve_confirm(
            db, session_id=session_id, user_id=(user or {}).get("user_id"),
            project_id=project_id, prompt=prompt, user=user, bin_ctx=bin_ctx,
        )
    from services.confirm_execution import maybe_execute_pending
    return await maybe_execute_pending(
        db, user=user, session_id=session_id, project_id=project_id,
        prompt=prompt, bin_ctx=bin_ctx,
    )


async def propose_from_turn(
    db, *, session_id: str, user_id: str, project_id: Optional[str],
    provider: str, assistant_reply: str, bin_ctx=None,
) -> None:
    """Called once per real turn at persist-time — the ONLY place a
    confirmable action can be created."""
    if commit_boundary_enabled():
        from services.actions.pending_action import propose_from_turn as _propose
        await _propose(
            db, session_id=session_id, user_id=user_id, project_id=project_id,
            provider=provider, assistant_reply=assistant_reply, bin_ctx=bin_ctx,
        )
        return
    from services.confirm_execution import (
        register_code_fence_pending, register_upgrade_pending, clear_pending_action,
    )
    if "```aurem-handoff" in (assistant_reply or ""):
        _brief_m = re.search(r"```aurem-handoff\s*\n(.*?)```", assistant_reply or "", re.DOTALL)
        await register_code_fence_pending(
            db, session_id=session_id, project_id=project_id,
            proposal_text=assistant_reply,
            brief=(_brief_m.group(1).strip() if _brief_m else ""),
        )
    elif provider == "edit-tier-upgrade-offer":
        await register_upgrade_pending(db, session_id=session_id, plan="pro")
    elif provider != "confirm-executor":
        await clear_pending_action(db, session_id=session_id)


# ── requirement #9: never let the model promise "ready when you
# approve"/"say go" as a SEPARATE trailing question after a real
# aurem-handoff fence. The fence itself IS the ask (Approve button);
# a dangling permission question after it is always misleading under
# this architecture (whether or not this turn's action was
# concretized into anything text-confirmable) and slips through the
# persona's own "no permission-asking" rule occasionally in practice.
_FENCE_RE = re.compile(r"```aurem-handoff.*?```", re.DOTALL)
_TRAILING_PROMISE_RE = re.compile(
    r"(ready when you|shall i|should i|let me know|want me to|"
    r"say\s+go|reply\s+(?:yes|go|approve)|approve\s+this|confirm\s+this)",
    re.IGNORECASE,
)


def strip_false_confirm_promise(content: str) -> str:
    if not content or "```aurem-handoff" not in content:
        return content
    matches = list(_FENCE_RE.finditer(content))
    if not matches:
        return content
    last_end = matches[-1].end()
    trailing = content[last_end:]
    if not trailing.strip():
        return content
    if "?" in trailing and _TRAILING_PROMISE_RE.search(trailing):
        return content[:last_end].rstrip()
    return content
