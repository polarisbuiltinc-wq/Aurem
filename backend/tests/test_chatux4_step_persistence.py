"""test_chatux4_step_persistence.py — 2026-08-19

Chat UX #4 (Tier 1) — "📖 Reading repo… ✍️ Writing files…" step trail
used to live only in the frontend's in-memory `messages` state. A page
refresh called GET /chat/history, which never returned a `steps`
field, so the trail vanished. Fix:
  * routers/chat.py `_persist_turn(..., steps=...)` now pins the SSE
    step frames collected during the turn onto the assistant turn doc.
  * GET /chat/history returns the raw turn dict, so `steps` rides
    along for free once persisted.
  * ChatPanel.jsx hydration mapper + MessageBubble.jsx now surface
    `m.steps` for non-streaming (historical) messages too.

These tests cover the backend persistence contract directly against a
live Mongo connection (mirrors test_admin_merge_stripe_registry.py
style — no live LLM call needed).
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from routers.chat import _persist_turn
from cto_services.db import get_db, set_db

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")


def _ensure_db():
    """Fresh Motor client bound to THIS test's event loop — a
    module-level client survives across pytest-asyncio's per-test
    loops and raises 'Event loop is closed' on the 2nd test."""
    if not MONGO_URL:
        return None
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    set_db(db)
    return db


@pytest.mark.asyncio
async def test_persist_turn_stores_steps_capped_at_40():
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")

    user_id = f"test-user-{uuid.uuid4()}"
    session_id = f"test-sess-{uuid.uuid4()}"
    steps = [{"text": f"step {i}", "done": i == 59} for i in range(60)]

    await _persist_turn(
        user_id, session_id, "hello", "hi back", "test-provider",
        steps=steps,
    )
    try:
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
        )
        assert doc is not None, "turn was not persisted"
        assistant_turn = doc["turns"][-1]
        assert assistant_turn["role"] == "assistant"
        assert "steps" in assistant_turn, "steps field missing on assistant turn"
        stored = assistant_turn["steps"]
        assert len(stored) == 40, "steps must be capped at the last 40"
        assert stored[0]["text"] == "step 20"
        assert stored[-1]["text"] == "step 59"
        assert stored[-1]["done"] is True
    finally:
        await db.chat_sessions.delete_one(
            {"session_id": session_id, "user_id": user_id},
        )


@pytest.mark.asyncio
async def test_persist_turn_omits_steps_field_when_none():
    """No `steps` kwarg (e.g. /chat/send, non-streaming paths) must NOT
    write an empty `steps: []` — keeps old persisted docs + the
    frontend's `Array.isArray(t.steps) && t.steps.length > 0` guard
    behaving identically to before this change."""
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")

    user_id = f"test-user-{uuid.uuid4()}"
    session_id = f"test-sess-{uuid.uuid4()}"

    await _persist_turn(user_id, session_id, "hello", "hi back", "test-provider")
    try:
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
        )
        assistant_turn = doc["turns"][-1]
        assert "steps" not in assistant_turn
    finally:
        await db.chat_sessions.delete_one(
            {"session_id": session_id, "user_id": user_id},
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
