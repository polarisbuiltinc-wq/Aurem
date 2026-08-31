"""
tests/test_commit_boundary_2026_09_05.py

Commit-Boundary class fix (2026-09-05) — founder's architectural
ruling after 4 rounds of prose/guard patches survived and re-broke on
a real non-technical repro: "A confirmation is a deterministic
server-side state transition, not a model turn."

Exercises services/actions/pending_action.py directly (the new
mechanism, COMMIT_BOUNDARY_ENABLED=true path) — see that module's
docstring for the full CBR-1..8 invariant list this enforces.

Named acceptance tests (founder's exact list):
  t_confirm_is_state_transition_not_model_turn
  t_confirm_executes_concretized_payload_not_reproposal
  t_confirm_no_pending_is_honest_actionable
  t_confirm_disambiguates_multiple
  t_prose_cannot_create_action
  t_confirm_always_resolves
  t_factual_claim_not_shown_unverified
  t_done_only_post_verify
  t_turn_always_terminates
  t_pending_persists_across_turns
  t_confirm_idempotent
  t_ttl_expiry_cancels
  t_confirm_bounded_to_verified
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from cto_services.db import set_db
from services.actions.pending_action import (
    STATUS_AWAITING_CONFIRM, STATUS_VERIFIED, STATUS_APPLIED_FAILED,
    STATUS_CANCELLED,
    PROVIDER_EXECUTOR, PROVIDER_NO_PENDING, PROVIDER_DISAMBIGUATE,
    PROVIDER_ERROR, PROVIDER_CANCELLED,
    NO_PENDING_ACTIONABLE_MESSAGE,
    propose_action, propose_from_turn, resolve_confirm,
    get_active_actions, extract_deterministic_edit, classify_confirm_intent,
)

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")
BIN_CTX = {"token": "fake-token"}


def _ensure_db():
    if not MONGO_URL:
        return None
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    set_db(db)
    return db


def _sid() -> str:
    return f"cb-test-{uuid.uuid4()}"


async def _cleanup(db, *session_ids):
    if db is None:
        return
    await db.pending_actions.delete_many({"session_id": {"$in": list(session_ids)}})


# ── t_confirm_is_state_transition_not_model_turn ────────────────────
@pytest.mark.asyncio
async def test_t_confirm_is_state_transition_not_model_turn(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    calls = {"chat_with_tools": 0}
    file_state = {"content": "9am-5pm"}

    async def _fake_chat_with_tools(*a, **kw):
        calls["chat_with_tools"] += 1
        return {"content": "SHOULD NEVER RUN — a regenerated proposal"}

    async def _fake_read(ctx, args):
        return {"ok": True, "content": file_state["content"]}

    async def _fake_write(ctx, args):
        file_state["content"] = args["content"]
        return {"ok": True, "sha": "abc", "html_url": "https://x/commit/abc"}

    monkeypatch.setattr("services.orchestrator.chat_with_tools", _fake_chat_with_tools)
    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)

    try:
        await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="edit", raw_payload={"path": "src/Hours.jsx", "old_value": "9am-5pm", "new_value": "9am-6pm"},
            ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": BIN_CTX},
        )
        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="yes please update it", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        assert result is not None
        assert calls["chat_with_tools"] == 0, "confirm must never call the LLM"
        assert result["provider"] == PROVIDER_EXECUTOR
        assert "9am-6pm" in result["content"]
    finally:
        await _cleanup(db, session_id)


# ── t_confirm_executes_concretized_payload_not_reproposal ───────────
@pytest.mark.asyncio
async def test_t_confirm_executes_concretized_payload_not_reproposal(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    original_file = '<p className="hours-badge">9am-5pm</p>'
    written = {}

    async def _fake_read(ctx, args):
        return {"ok": True, "content": original_file}

    async def _fake_write(ctx, args):
        written["content"] = args["content"]
        return {"ok": True, "sha": "abc123", "html_url": "https://github.com/x/y/commit/abc123"}

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)

    try:
        action = await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="edit", raw_payload={"path": "src/Hours.jsx", "old_value": "9am-5pm", "new_value": "9am-6pm"},
            ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": BIN_CTX},
        )
        assert action["status"] == STATUS_AWAITING_CONFIRM

        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="yes please update it", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        assert "9am-6pm" in written["content"]
        # THE SMOKING-GUN ASSERTION — the class name from the original
        # target file survives verbatim: a literal substitution, not a
        # regenerated (possibly differently-worded) proposal.
        assert 'className="hours-badge"' in written["content"]
        assert "9am-5pm" not in written["content"]
        assert result["provider"] == PROVIDER_EXECUTOR
    finally:
        await _cleanup(db, session_id)


# ── t_confirm_no_pending_is_honest_actionable ────────────────────────
@pytest.mark.asyncio
async def test_t_confirm_no_pending_is_honest_actionable():
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    try:
        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="approve", user={"user_id": "u1"}, bin_ctx=None,
        )
        assert result is not None
        assert result["provider"] == PROVIDER_NO_PENDING
        assert result["content"] == NO_PENDING_ACTIONABLE_MESSAGE
        assert "nothing pending" not in result["content"].lower()
        assert "tell me" in result["content"].lower()  # actionable, not a dead end
    finally:
        await _cleanup(db, session_id)


# ── t_confirm_disambiguates_multiple ─────────────────────────────────
@pytest.mark.asyncio
async def test_t_confirm_disambiguates_multiple(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    written = {}
    files = {"a.jsx": "old-a", "b.jsx": "old-b"}

    async def _fake_read(ctx, args):
        return {"ok": True, "content": files[args["path"]]}

    async def _fake_write(ctx, args):
        written[args["path"]] = args["content"]
        files[args["path"]] = args["content"]
        return {"ok": True, "sha": "s", "html_url": "u"}

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)

    try:
        await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="edit", raw_payload={"path": "a.jsx", "old_value": "old-a", "new_value": "new-a"},
            ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": BIN_CTX},
        )
        await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="edit", raw_payload={"path": "b.jsx", "old_value": "old-b", "new_value": "new-b"},
            ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": BIN_CTX},
        )
        active = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert len(active) == 2

        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="yes", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        assert result["provider"] == PROVIDER_DISAMBIGUATE
        assert "a.jsx" in result["content"] and "b.jsx" in result["content"]
        assert not written  # neither executed yet — no silent pick, no stacking-exec

        # numeric reply resolves ONE, not both.
        result2 = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="2", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        assert result2["provider"] == PROVIDER_EXECUTOR
        assert "b.jsx" in written and "new-b" in written["b.jsx"]
        assert "a.jsx" not in written
        remaining = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert len(remaining) == 1 and remaining[0]["payload"]["path"] == "a.jsx"
    finally:
        await _cleanup(db, session_id)


# ── t_prose_cannot_create_action ─────────────────────────────────────
@pytest.mark.asyncio
async def test_t_prose_cannot_create_action_when_not_concretizable():
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    try:
        await propose_from_turn(
            db, session_id=session_id, user_id="u1", project_id="p1",
            provider="aurem-agentic",
            assistant_reply="```aurem-handoff\nI'll update the hours somewhere soon.\n```",
            bin_ctx=BIN_CTX,
        )
        active = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert active == [], "vague prose with no extractable concrete edit must never become actionable"
    finally:
        await _cleanup(db, session_id)


@pytest.mark.asyncio
async def test_t_prose_cannot_create_action_without_repo_validation():
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    try:
        await propose_from_turn(
            db, session_id=session_id, user_id="u1", project_id="p1",
            provider="aurem-agentic",
            assistant_reply=(
                "I'll change the opening hours from '9am-5pm' to '9am-6pm' "
                "in `src/Hours.jsx`.\n```aurem-handoff\nupdate hours\n```"
            ),
            bin_ctx=None,  # no repo context to validate against
        )
        active = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert active == [], "an unvalidated (concretizable-looking) edit must not become actionable either"
    finally:
        await _cleanup(db, session_id)


def test_extract_deterministic_edit_from_prose():
    text = "I'll change the opening hours from '9am-5pm' to '9am-6pm' in `src/components/Hours.jsx`."
    edit = extract_deterministic_edit(text)
    assert edit == {"path": "src/components/Hours.jsx", "old_value": "9am-5pm", "new_value": "9am-6pm"}


def test_extract_deterministic_edit_returns_none_when_unclear():
    assert extract_deterministic_edit("I'll update your hours soon.") is None


# ── t_confirm_always_resolves ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_t_confirm_always_resolves_on_executor_blowup(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()

    async def _fake_read(ctx, args):
        return {"ok": True, "content": "old-value"}

    async def _fake_write(ctx, args):
        raise RuntimeError("boom")

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)

    try:
        await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="edit", raw_payload={"path": "f.jsx", "old_value": "old-value", "new_value": "new-value"},
            ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": BIN_CTX},
        )
        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="yes", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        assert result is not None
        assert "went wrong" in result["content"].lower() or "try again" in result["content"].lower()
    finally:
        await _cleanup(db, session_id)


@pytest.mark.asyncio
async def test_t_confirm_always_resolves_on_lookup_failure(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()

    async def _broken_get_active(*a, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr("services.actions.pending_action.get_active_actions", _broken_get_active)
    result = await resolve_confirm(
        db, session_id=session_id, user_id="u1", project_id="p1",
        prompt="yes", user={"user_id": "u1"}, bin_ctx=None,
    )
    assert result is not None
    assert result["provider"] == PROVIDER_ERROR


def test_t_confirm_always_resolves_new_request_falls_through():
    intent, idx = classify_confirm_intent("update my opening hours")
    assert intent == "new_request"
    assert idx is None


# ── t_factual_claim_not_shown_unverified / t_done_only_post_verify /
#    t_confirm_bounded_to_verified — all three assert the same core
#    property from slightly different angles: "Done" framing and a
#    VERIFIED status only ever appear together with a real, checked
#    read-back; a failed read-back always yields an honest
#    APPLIED_FAILED, never a false "Done".
@pytest.mark.asyncio
async def test_t_done_only_post_verify_success_path(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()

    async def _fake_read(ctx, args):
        return {"ok": True, "content": "new-value-here"}  # both pre-check and post-write verify see the new value

    async def _fake_write(ctx, args):
        return {"ok": True, "sha": "s1", "html_url": "u1"}

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)

    try:
        await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="edit", raw_payload={"path": "f.jsx", "old_value": "new-value-here", "new_value": "final-value"},
            ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": BIN_CTX},
        )
        # override read AFTER propose so execute-time flow sees old-value
        # present exactly once, then the verify read sees the new value.
        calls = {"n": 0}

        async def _fake_read_seq(ctx, args):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": True, "content": "new-value-here"}  # pre-write check
            return {"ok": True, "content": "final-value"}  # post-write verify

        monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read_seq)

        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="yes", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        assert "Done" in result["content"]
        doc = await db.pending_actions.find_one({"session_id": session_id})
        assert doc["status"] == STATUS_VERIFIED
        assert doc["verification_result"]["verified"] is True
    finally:
        await _cleanup(db, session_id)


@pytest.mark.asyncio
async def test_t_factual_claim_not_shown_unverified_failure_path(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()

    calls = {"n": 0}

    async def _fake_read_seq(ctx, args):
        calls["n"] += 1
        # Every read (propose-validate, pre-write check, post-write
        # verify) sees the SAME unchanged content — simulates a write
        # that silently didn't land.
        return {"ok": True, "content": "old-value-here"}

    async def _fake_write(ctx, args):
        return {"ok": True, "sha": "s1", "html_url": "u1"}

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read_seq)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)

    try:
        await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="edit", raw_payload={"path": "f.jsx", "old_value": "old-value-here", "new_value": "new-value-here"},
            ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": BIN_CTX},
        )
        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="yes", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        assert "Done" not in result["content"]
        assert "couldn't confirm" in result["content"].lower()
        doc = await db.pending_actions.find_one({"session_id": session_id})
        assert doc["status"] == STATUS_APPLIED_FAILED
        assert doc["verification_result"]["verified"] is False
    finally:
        await _cleanup(db, session_id)


@pytest.mark.asyncio
async def test_t_confirm_bounded_to_verified_terminal_status(monkeypatch):
    """After a confirm resolves, the action's stored status is ALWAYS
    a terminal one (VERIFIED or APPLIED_FAILED) — never left EXECUTING
    or EXECUTED-but-unresolved."""
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()

    async def _fake_read(ctx, args):
        return {"ok": True, "content": "v1"}

    async def _fake_write(ctx, args):
        return {"ok": True, "sha": "s", "html_url": "u"}

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)

    try:
        await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="edit", raw_payload={"path": "f.jsx", "old_value": "v1", "new_value": "v2"},
            ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": BIN_CTX},
        )
        await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="yes", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        doc = await db.pending_actions.find_one({"session_id": session_id})
        assert doc["status"] in (STATUS_VERIFIED, STATUS_APPLIED_FAILED)
    finally:
        await _cleanup(db, session_id)


# ── t_turn_always_terminates ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_t_turn_always_terminates_on_write_exception(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()

    async def _fake_read(ctx, args):
        return {"ok": True, "content": "v1"}

    async def _boom_write(ctx, args):
        raise ConnectionError("upstream down")

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _boom_write)

    try:
        await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="edit", raw_payload={"path": "f.jsx", "old_value": "v1", "new_value": "v2"},
            ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": BIN_CTX},
        )
        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="yes", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        assert result is not None
        doc = await db.pending_actions.find_one({"session_id": session_id})
        assert doc["status"] == STATUS_APPLIED_FAILED
    finally:
        await _cleanup(db, session_id)


# ── t_pending_persists_across_turns ────────────────────────────────────
@pytest.mark.asyncio
async def test_t_pending_persists_across_turns(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()

    async def _fake_read(ctx, args):
        return {"ok": True, "content": file_state["content"]}

    async def _fake_write(ctx, args):
        file_state["content"] = args["content"]
        return {"ok": True, "sha": "s", "html_url": "u"}

    file_state = {"content": "v1"}
    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)

    try:
        # "Turn 1" — propose only, nothing else.
        await propose_from_turn(
            db, session_id=session_id, user_id="u1", project_id="p1",
            provider="aurem-agentic",
            assistant_reply="I'll change from 'v1' to 'v2' in `f.jsx`.\n```aurem-handoff\nupdate\n```",
            bin_ctx=BIN_CTX,
        )
        active = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert len(active) == 1

        # "Turn 2" — a completely separate call, no shared in-memory
        # state — proves persistence is real, server-side, not a
        # process-local cache.
        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="yes please update it", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        assert result["provider"] == PROVIDER_EXECUTOR
        assert "v2" in result["content"]
    finally:
        await _cleanup(db, session_id)


# ── t_confirm_idempotent ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_t_confirm_idempotent(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    write_calls = {"n": 0}

    async def _fake_read(ctx, args):
        return {"ok": True, "content": "v1"}

    async def _fake_write(ctx, args):
        write_calls["n"] += 1
        return {"ok": True, "sha": "s", "html_url": "u"}

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)

    try:
        await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="edit", raw_payload={"path": "f.jsx", "old_value": "v1", "new_value": "v2"},
            ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": BIN_CTX},
        )
        result1 = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="yes", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        result2 = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="yes", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        assert write_calls["n"] == 1, "a second confirm must NEVER re-execute the write"
        assert result2["content"] == result1["content"]
        assert result2["meta"].get("idempotent_echo") is True
    finally:
        await _cleanup(db, session_id)


# ── t_ttl_expiry_cancels ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_t_ttl_expiry_cancels():
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    try:
        action = await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="upgrade", raw_payload={"plan": "pro"},
        )
        assert action["status"] == STATUS_AWAITING_CONFIRM
        await db.pending_actions.update_one(
            {"id": action["id"]}, {"$set": {"expires_at": time.time() - 1}},
        )
        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="approve", user={"user_id": "u1"}, bin_ctx=None,
        )
        assert result["provider"] == PROVIDER_NO_PENDING
        doc = await db.pending_actions.find_one({"id": action["id"]})
        assert doc["status"] == STATUS_CANCELLED
        assert doc["cancel_reason"] == "ttl_expired"
    finally:
        await _cleanup(db, session_id)


# ── cancel intent sanity (supports CBR cancel path, not separately named) ─
@pytest.mark.asyncio
async def test_cancel_intent_cancels_active_action():
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    try:
        await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="upgrade", raw_payload={"plan": "pro"},
        )
        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="no, never mind", user={"user_id": "u1"}, bin_ctx=None,
        )
        assert result["provider"] == PROVIDER_CANCELLED
        active = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert active == []
    finally:
        await _cleanup(db, session_id)
