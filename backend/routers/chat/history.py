"""
routers/chat/history.py — chat history/session list/delete endpoints.
Split from the former routers/chat.py god-file (4184 lines) on
2026-09-08. See routers/chat/__init__.py for the split rationale.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, validator

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.orchestrator import chat_with_tools
from services.llm import call_llm_with_meta, call_emergent_watchdog, cap_for
from services.repo_context import get_repo_context
from services.usage import is_founder_email  # Iter 212m-169 — BINContext role check
from core.task_type import infer_task_type as _infer_task_type  # Iter 212m-177/178 P0-3
from services.chat_helpers import (
    _detect_mode, _deduct_tokens,
    is_fix_confirmation, _safe_provenance, detect_prompt_injection,
    _f12_has_real_signal, _is_transient_proxy_error, _TRANSIENT_PROXY_CODES,
    classify_intent,
    _TITLE_SYSTEM, _generate_title, _maybe_set_title,
    _regenerate_without_recall, _strip_council_block,
    _persist_turn,
    _build_failed_followup, _build_blocked_followup, _build_done_fallback,
    _FOLLOWUP_SYS, _generate_done_followup,
    retrieved_context_for_grounding, apply_output_guards,
)

from . import router

logger = logging.getLogger(__name__)


@router.get("/history")
async def chat_history(
    session_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Return last 200 turns of a session for the current user.
    Iter 330 root-fix — bumped 100 → 200 to align with the write-side
    `$slice: -200` cap (see POST /chat/history). Previously the write
    kept up to 200 turns in Mongo but this read only returned the last
    100, so anything between positions -200 and -100 silently vanished
    on every refresh once the user crossed the 100-turn threshold —
    which happens after ~7 loop-mode runs at 15 turns/loop. The user
    reported this as 'older chats disappear after refresh'."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None or not session_id:
        return {"ok": True, "messages": [], "session_id": session_id}
    doc = await db.chat_sessions.find_one(
        {"session_id": session_id, "user_id": user["user_id"]},
        {"_id": 0, "turns": 1, "title": 1},
    )
    turns = ((doc or {}).get("turns") or [])[-200:]
    # Iter 339j — strip literal nulls (Mongo pads sparse indexes with
    # null on out-of-range positional $set from stale clients). One
    # null crashed the frontend hydration entirely.
    turns = [t for t in turns if isinstance(t, dict) and t.get("role")]
    return {
        "ok": True,
        "messages": turns,
        "session_id": session_id,
        "title": (doc or {}).get("title", ""),
    }



@router.get("/sessions")
async def chat_sessions_list(
    project_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Return up to 20 most-recent chat sessions for the current user.
    Filter to a specific project_id when provided; pass 'home' to get
    sessions that aren't bound to any project."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True, "sessions": []}
    q = {"user_id": user["user_id"]}
    # Iter 356 — our own E2E test runs (prod smoke suite) create
    # "prod-e2e-*" sessions that must never pollute the user-facing
    # sidebar. Filter them out of every list response.
    from services.test_accounts import E2E_SESSION_PREFIX_RE
    q["session_id"] = {"$not": E2E_SESSION_PREFIX_RE}
    if project_id == "home":
        # Home tab shows un-pinned sessions PLUS legacy sessions that have
        # no project_id field at all (created before per-project chats).
        q["$or"] = [{"project_id": None}, {"project_id": {"$exists": False}}]
    elif project_id:
        q["project_id"] = project_id
    cursor = db.chat_sessions.find(
        q,
        {
            "_id": 0, "session_id": 1, "title": 1, "project_id": 1,
            "last_message": 1, "updated_at": 1, "created_at": 1,
        },
    ).sort("updated_at", -1).limit(20)
    sessions = await cursor.to_list(length=20)
    return {"ok": True, "sessions": sessions}



@router.delete("/sessions/{session_id}")
async def chat_session_delete(
    session_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Delete a single chat session belonging to the current user."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    r = await db.chat_sessions.delete_one(
        {"session_id": session_id, "user_id": user["user_id"]}
    )
    return {"ok": True, "deleted": r.deleted_count}



@router.delete("/sessions/{session_id}/messages")
async def chat_session_clear_messages(
    session_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 131 — wipe all turns in a session but KEEP the session
    alive (preserves session_id + title + project link). Powers the
    'Clear chat' button in the chat-window toolbar so a user can
    reset a long conversation without losing its sidebar entry."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    r = await db.chat_sessions.update_one(
        {"session_id": session_id, "user_id": user["user_id"]},
        {"$set": {
            "turns": [],
            "updated_at": time.time(),
            "last_message": "",
        }},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "session not found")
    return {"ok": True, "cleared": True, "session_id": session_id}


# ─── Iter 53 — Post-commit wrap-up message ─────────────────────────────
# When a Mode C task finishes (status=done) the chat used to fall silent.
# The user only saw "✅ Pushed <sha>" on the status card and had to ask
# "is it fixed?" — which then timed out because we re-classified that as
# a new task with no codebase context. This endpoint produces the
# explicit closing message ORA owes the user: what was changed, whether
# the original ask is likely resolved, and one concrete verification
# step. Idempotent — only fires once per task.

