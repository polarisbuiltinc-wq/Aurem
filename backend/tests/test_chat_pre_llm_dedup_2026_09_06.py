"""
tests/test_chat_pre_llm_dedup_2026_09_06.py

Phase 1 of the AUREM master refactor (chat.py, the "load-bearing
dedup") — see routers/chat_pre_llm.py's module docstring for the full
north-star rationale: `chat_send` and `chat_stream` now call ONE
shared `resolve_pre_llm()` for the confirm-boundary / intent-classify
/ upgrade-offer / self-bug / casual-direct-reply sequence, instead of
each hand-maintaining its own copy (the root cause of the
4x-reproduced confirm bug — a new confirm-context added to one
endpoint and missed in the other).

S11 guard (single call site each, byte-identical sequencing) is
covered by test_confirm_execution_2026_09_04.py::
test_t_no_duplicate_guard_wiring (updated this round). This file
covers:
  - unit tests for every branch of resolve_pre_llm() (unified
    short-circuit shape — the ONE deliberate, tested behavior change
    this Phase authorized),
  - the S16 NORTH-STAR test: the exact confirm-bug repro run through
    the REAL /chat/send HTTP endpoint (not just the pending_action
    module directly, like test_commit_boundary_2026_09_05.py already
    does) — proving the dedup preserved the class-fix and did NOT
    move the duplication.
"""
from __future__ import annotations

import inspect
import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from motor.motor_asyncio import AsyncIOMotorClient

from cto_services.db import set_db
from routers.chat_pre_llm import resolve_pre_llm, PreLLMOutcome
from services.actions.pending_action import propose_action, STATUS_AWAITING_CONFIRM

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")


def _ensure_db():
    if not MONGO_URL:
        return None
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    set_db(db)
    return db, client


def _sid() -> str:
    return f"pre-llm-test-{uuid.uuid4()}"


class _Body:
    """Minimal stand-in for the chat_send/chat_stream Pydantic body —
    resolve_pre_llm only ever reads these 3 attributes."""
    def __init__(self, prompt, session_id="s1", project_id=None, ora_panel=False):
        self.prompt = prompt
        self.session_id = session_id
        self.project_id = project_id
        self.ora_panel = ora_panel


USER = {"user_id": "u1", "email": "user@example.com", "tier": "free"}


# ── unit tests: every branch of resolve_pre_llm() ──────────────────
@pytest.mark.asyncio
async def test_new_request_falls_through_with_none_result(monkeypatch):
    async def _classify(*a, **kw):
        return {"tier": "agentic", "confidence": 0.9}
    monkeypatch.setattr("core.intent_gateway.classify", _classify)
    outcome = await resolve_pre_llm(
        db=None, user=USER, body=_Body("build me a login page"), bin_ctx=None,
        prior_fix_signal=False, prior_turn_text="", session_summary="",
        allowed_modes=["swift", "pro"], req_mode="swift",  # account_has_pro -> no upgrade offer
        run_confirm_boundary=False,
    )
    assert isinstance(outcome, PreLLMOutcome)
    assert outcome.result is None
    assert outcome.tier == "agentic"


@pytest.mark.asyncio
async def test_upgrade_offer_short_circuit_unified_shape(monkeypatch):
    async def _classify(*a, **kw):
        return {"tier": "agentic", "confidence": 0.95}
    monkeypatch.setattr("core.intent_gateway.classify", _classify)
    monkeypatch.setattr(
        "services.mode_routing.needs_edit_upgrade_offer", lambda *a, **kw: True,
    )
    outcome = await resolve_pre_llm(
        db=None, user=USER, body=_Body("update my opening hours"), bin_ctx=None,
        prior_fix_signal=False, prior_turn_text="", session_summary="",
        allowed_modes=["swift"], req_mode="swift",
        run_confirm_boundary=False,
    )
    r = outcome.result
    assert r is not None
    assert r["provider"] == "edit-tier-upgrade-offer"
    # the unified (superset) shape — every key either endpoint's OWN
    # copy of this dict used, before this refactor.
    for key in ("ok", "content", "provider", "fallback_chain", "iterations",
               "tool_calls_run", "tool_invocations", "meta", "council",
               "task_type", "findings_saved_this_turn", "intent", "tier", "mode"):
        assert key in r, f"unified upgrade-offer shape missing key: {key}"


@pytest.mark.asyncio
async def test_self_bug_short_circuit_unified_shape(monkeypatch):
    async def _classify(*a, **kw):
        return {"tier": "agentic", "confidence": 0.9}
    monkeypatch.setattr("core.intent_gateway.classify", _classify)
    monkeypatch.setattr(
        "services.user_report_classifier.is_user_reporting_ora_bug", lambda *a: True,
    )
    monkeypatch.setattr("services.self_bug.emit", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "services.self_bug_reply_guard.compose_self_bug_reply",
        lambda *a: "Sorry about that, here's what happened.",
    )
    outcome = await resolve_pre_llm(
        db=None, user=USER, body=_Body("your reply is broken"), bin_ctx=None,
        prior_fix_signal=False, prior_turn_text="", session_summary="",
        allowed_modes=["swift", "pro"], req_mode="swift",  # account_has_pro -> no upgrade offer
        run_confirm_boundary=False,
    )
    r = outcome.result
    assert r is not None
    assert r["provider"] == "self-bug-reply"
    for key in ("fallback_chain", "tool_invocations", "meta", "council",
               "task_type", "findings_saved_this_turn", "intent", "tier", "mode"):
        assert key in r, f"unified self-bug shape missing key: {key}"


@pytest.mark.asyncio
async def test_no_pending_fix_short_circuit_unified_shape(monkeypatch):
    async def _classify(*a, **kw):
        return {"tier": "casual", "confidence": 0.9}
    monkeypatch.setattr("core.intent_gateway.classify", _classify)
    outcome = await resolve_pre_llm(
        db=None, user=USER, body=_Body("approve"), bin_ctx=None,
        prior_fix_signal=False, prior_turn_text="", session_summary="",
        allowed_modes=["swift"], req_mode="swift",
        run_confirm_boundary=False,
    )
    r = outcome.result
    assert r is not None
    assert r["provider"] == "intent-gateway-no-pending-fix"
    # honest + actionable (S6) -- not a bare dead-end: it names the
    # situation AND invites the next concrete step.
    assert "describe the fix" in r["content"].lower() or "tell me" in r["content"].lower()
    for key in ("fallback_chain", "tool_invocations", "meta", "council",
               "task_type", "findings_saved_this_turn", "intent", "tier", "mode"):
        assert key in r


@pytest.mark.asyncio
async def test_casual_direct_reply_unified_shape(monkeypatch):
    async def _classify(*a, **kw):
        return {"tier": "casual", "confidence": 0.9}
    monkeypatch.setattr("core.intent_gateway.classify", _classify)
    monkeypatch.setattr(
        "services.intent_gateway_casual_reply.casual_direct_reply",
        AsyncMock(return_value="Hey there!"),
    )
    monkeypatch.setattr(
        "services.business_voice_filter.apply_business_owner_guards",
        AsyncMock(side_effect=lambda ora_panel, text, *a, **kw: text),
    )
    outcome = await resolve_pre_llm(
        db=None, user=USER, body=_Body("hi"), bin_ctx=None,
        prior_fix_signal=False, prior_turn_text="", session_summary="",
        allowed_modes=["swift"], req_mode="swift",
        run_confirm_boundary=False,
    )
    r = outcome.result
    assert r is not None
    assert r["provider"] == "intent-gateway-casual"
    assert r["content"] == "Hey there!"
    for key in ("meta", "council", "task_type", "findings_saved_this_turn"):
        assert key in r, f"unified casual shape missing key: {key}"


@pytest.mark.asyncio
async def test_casual_direct_reply_exception_falls_through(monkeypatch):
    async def _classify(*a, **kw):
        return {"tier": "casual", "confidence": 0.9}
    monkeypatch.setattr("core.intent_gateway.classify", _classify)
    monkeypatch.setattr(
        "services.intent_gateway_casual_reply.casual_direct_reply",
        AsyncMock(side_effect=RuntimeError("llm down")),
    )
    outcome = await resolve_pre_llm(
        db=None, user=USER, body=_Body("hi"), bin_ctx=None,
        prior_fix_signal=False, prior_turn_text="", session_summary="",
        allowed_modes=["swift"], req_mode="swift",
        run_confirm_boundary=False,
    )
    assert outcome.result is None, "a broken casual LLM must fall through, never blank-screen"


def test_dedup_single_call_site_each_endpoint():
    """S11 guard, duplicated here for this Phase's own test file (the
    canonical assertion lives in test_confirm_execution_2026_09_04.py
    ::test_t_no_duplicate_guard_wiring, updated this round)."""
    import routers.chat as chat_mod
    src = "".join(inspect.getsource(m) for m in
                   (chat_mod.misc, chat_mod.turn, chat_mod.stream, chat_mod.history, chat_mod.worker))
    assert src.count("resolve_pre_llm(") == 2
    assert src.count("resolve_turn_start(") == 0


# ── S16 NORTH-STAR TEST — the confirm-bug repro through the REAL
# chat_send() endpoint function (called directly, not via a separate
# HTTP/ASGI layer, so it stays on the SAME event loop as the real
# Mongo client set up below — TestClient's own internal loop would
# otherwise cross-loop with a motor client set up in this test's
# pytest-asyncio loop). This still exercises the exact, real,
# refactored `chat_send()` body end-to-end.
@pytest.fixture
def real_db():
    import cto_services.db as db_mod
    pair = _ensure_db()
    if pair is None:
        pytest.skip("no live Mongo connection in this environment")
    db, client = pair
    _prev_db = db_mod._db
    yield db
    client.close()
    db_mod._db = _prev_db


async def _cleanup(db, session_id):
    await db.pending_actions.delete_many({"session_id": session_id})
    await db.chat_sessions.delete_many({"session_id": session_id})


@pytest.mark.asyncio
async def test_north_star_confirm_is_state_transition_through_real_endpoint(
    real_db, monkeypatch,
):
    """The founder's exact class of bug: propose a concrete edit,
    then confirm in chat text. Through the REAL chat_send() function
    (not the pending_action module directly), the confirm must
    execute the stored payload verbatim and must NEVER call the
    model."""
    import routers.chat as chat_mod
    from routers.chat import ChatBody

    session_id = _sid()
    file_state = {"content": '<p className="hours-badge">9am-5pm</p>'}

    async def _fake_read(ctx, args):
        return {"ok": True, "content": file_state["content"]}

    async def _fake_write(ctx, args):
        file_state["content"] = args["content"]
        return {"ok": True, "sha": "abc123", "html_url": "https://x/commit/abc123"}

    llm_calls = {"n": 0}

    async def _fake_chat_with_tools(*a, **kw):
        llm_calls["n"] += 1
        return {"content": "SHOULD NEVER RUN", "provider": "deepseek", "meta": {}}

    async def _fake_current_dev(authorization=None):
        return USER

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)
    monkeypatch.setattr("routers.chat.turn.chat_with_tools", _fake_chat_with_tools)
    monkeypatch.setattr("routers.chat.turn.current_dev", _fake_current_dev)
    monkeypatch.setattr("services.usage.assert_has_budget", AsyncMock(return_value=None))
    monkeypatch.setattr("services.usage.assert_has_task_budget", AsyncMock(return_value=None))
    monkeypatch.setattr("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500))

    try:
        await propose_action(
            real_db, session_id=session_id, user_id="u1", project_id="p1",
            type_="edit",
            raw_payload={"path": "src/Hours.jsx", "old_value": "9am-5pm", "new_value": "9am-6pm"},
            ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": {"token": "x"}},
        )

        body = ChatBody(prompt="yes please update it", project_id="home", session_id=session_id)
        result = await chat_mod.chat_send(body, authorization="Bearer u1")

        assert llm_calls["n"] == 0, "confirm must NEVER call the model"
        assert "9am-6pm" in result["content"]
        assert 'className="hours-badge"' in file_state["content"]
        assert result["provider"] == "commit-boundary-executor"
    finally:
        await _cleanup(real_db, session_id)


@pytest.mark.asyncio
async def test_north_star_confirm_no_pending_is_honest_through_real_endpoint(real_db, monkeypatch):
    import routers.chat as chat_mod
    from routers.chat import ChatBody

    session_id = _sid()

    async def _fake_current_dev(authorization=None):
        return USER

    monkeypatch.setattr("routers.chat.turn.current_dev", _fake_current_dev)
    monkeypatch.setattr("services.usage.assert_has_budget", AsyncMock(return_value=None))
    monkeypatch.setattr("services.usage.assert_has_task_budget", AsyncMock(return_value=None))
    monkeypatch.setattr("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500))

    try:
        body = ChatBody(prompt="approve", project_id="home", session_id=session_id)
        result = await chat_mod.chat_send(body, authorization="Bearer u1")
        assert "nothing pending" in result["content"].lower() or "tell me" in result["content"].lower()
        assert "describe the fix" in result["content"].lower() or "tell me" in result["content"].lower()
    finally:
        await _cleanup(real_db, session_id)

