"""
tests/test_confirm_execution_2026_09_04.py

Confirm-execution round (2026-09-04) — server-side pending action
state + real execution on confirmation, reversing Iter 212m-26's
"no auto-ship-via-chat-text" restriction (founder's explicit call,
after a live 3x repro showed the alternative -- a reworded proposal
with a DIFFERENT CSS class name, then "yes please" -> "nothing
pending" -- is worse).

t_confirmation_intent_recognized -- broadened confirm-intent matcher
t_pending_persists_across_turns -- pending_action survives across
  the persist -> lookup round trip via the real chat_sessions doc
t_confirm_executes_code_still_works -- code-fence confirm still
  executes the pending edit (Root 1 regression, post-DRY)
t_confirm_executes_upgrade -- upgrade-offer confirm starts checkout,
  not "nothing pending"
t_confirm_executor_handles_both_types -- ONE helper dispatches on
  action type, not hardcoded per type
t_no_duplicate_guard_wiring -- confirm-execution + output-guard logic
  each live in exactly one place, called from exactly the 2 real
  endpoints (chat_send, chat_stream) -- no extra copies
"""
from __future__ import annotations

import inspect
import os
import time
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from cto_services.db import set_db
from services.confirm_execution import (
    is_confirm_execute_intent,
    get_pending_action,
    register_code_fence_pending,
    register_upgrade_pending,
    clear_pending_action,
    maybe_execute_pending,
    extract_deterministic_edit,
)

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")


def _ensure_db():
    if not MONGO_URL:
        return None
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    set_db(db)
    return db


# ── t_confirmation_intent_recognized ────────────────────────────────
@pytest.mark.parametrize("msg", [
    "yes", "yes please", "yes please update it", "approve", "approved",
    "go", "go ahead", "do it", "yes go", "sure", "ok do it", "ship it",
    "sounds good", "confirm", "proceed",
])
def test_t_confirmation_intent_recognized_positive(msg):
    assert is_confirm_execute_intent(msg) is True, f"expected confirm intent: {msg!r}"


@pytest.mark.parametrize("msg", [
    "update my opening hours", "yes but also change the color",
    "what is my current plan?", "hi", "no, don't do that",
    "yes update the hours to include weekends too",
])
def test_t_confirmation_intent_recognized_negative(msg):
    assert is_confirm_execute_intent(msg) is False, f"unexpected confirm intent match: {msg!r}"


# ── t_pending_persists_across_turns ─────────────────────────────────
@pytest.mark.asyncio
async def test_t_pending_persists_across_turns_code_fence():
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = f"confirm-exec-test-{uuid.uuid4()}"
    try:
        await register_code_fence_pending(
            db, session_id=session_id, project_id="home",
            proposal_text="I'll change the opening hours from '9am-5pm' to "
                          "'9am-6pm' in `src/components/Hours.jsx`.",
            brief="update hours",
        )
        action = await get_pending_action(db, session_id)
        assert action is not None
        assert action["type"] == "code_fence"
        assert "9am-6pm" in action["proposal_text"]
    finally:
        await db.chat_sessions.delete_one({"session_id": session_id})


@pytest.mark.asyncio
async def test_pending_action_expires_past_ttl():
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = f"confirm-exec-ttl-test-{uuid.uuid4()}"
    try:
        await db.chat_sessions.update_one(
            {"session_id": session_id},
            {"$set": {"pending_action": {
                "type": "upgrade", "created_at": time.time() - 999999, "plan": "pro",
            }}},
            upsert=True,
        )
        assert await get_pending_action(db, session_id) is None
    finally:
        await db.chat_sessions.delete_one({"session_id": session_id})


# ── extraction helper ────────────────────────────────────────────────
def test_extract_deterministic_edit_from_prose():
    text = "I'll change the opening hours from '9am-5pm' to '9am-6pm' in `src/components/Hours.jsx`."
    edit = extract_deterministic_edit(text)
    assert edit == {"path": "src/components/Hours.jsx", "old_value": "9am-5pm", "new_value": "9am-6pm"}


def test_extract_deterministic_edit_returns_none_when_unclear():
    assert extract_deterministic_edit("I'll update your hours soon.") is None


# ── t_confirm_executes_code_still_works (Root 1 regression, post-DRY) ─
@pytest.mark.asyncio
async def test_t_confirm_executes_code_still_works(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = f"confirm-exec-code-{uuid.uuid4()}"
    original_file = '<p className="hours-badge">9am-5pm</p>'
    written = {}

    async def _fake_read(ctx, args):
        return {"ok": True, "content": original_file}

    async def _fake_write(ctx, args):
        written["path"] = args["path"]
        written["content"] = args["content"]
        return {"ok": True, "sha": "abc123", "html_url": "https://github.com/x/y/commit/abc123"}

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)

    try:
        await register_code_fence_pending(
            db, session_id=session_id, project_id="p1",
            proposal_text="I'll change the opening hours from '9am-5pm' to "
                          "'9am-6pm' in `src/Hours.jsx`. Ready when you approve?",
            brief="update hours",
        )
        result = await maybe_execute_pending(
            db, user={"user_id": "u1"}, session_id=session_id, project_id="p1",
            prompt="yes please update it", bin_ctx=None,
        )
        assert result is not None
        assert result["provider"] == "confirm-executor"
        assert result["_skip_output_guards"] is True
        assert "9am-6pm" in written["content"]
        # THE SMOKING-GUN ASSERTION: the CSS class name from the
        # ORIGINAL proposal's target file survives verbatim -- proves
        # this is a literal substitution, not a regenerated proposal.
        assert 'className="hours-badge"' in written["content"]
        assert "9am-5pm" not in written["content"]
        # No pending action left armed after execution.
        assert await get_pending_action(db, session_id) is None
    finally:
        await db.chat_sessions.delete_one({"session_id": session_id})


@pytest.mark.asyncio
async def test_confirm_with_no_pending_action_returns_none():
    """No pending action -> maybe_execute_pending defers (returns
    None) so the caller's existing NO_PENDING_FIX_MESSAGE path
    handles it -- the rare, honest fallback."""
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = f"confirm-exec-empty-{uuid.uuid4()}"
    result = await maybe_execute_pending(
        db, user={"user_id": "u1"}, session_id=session_id, project_id="p1",
        prompt="yes please", bin_ctx=None,
    )
    assert result is None


# ── t_confirm_executes_upgrade ───────────────────────────────────────
@pytest.mark.asyncio
async def test_t_confirm_executes_upgrade(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = f"confirm-exec-upgrade-{uuid.uuid4()}"

    async def _fake_checkout(user, plan, origin_url=""):
        return {"url": "https://checkout.stripe.com/fake", "checkout_url": "https://checkout.stripe.com/fake", "session_id": "cs_test_123"}

    monkeypatch.setattr("routers.payments.create_checkout_session", _fake_checkout)

    try:
        await register_upgrade_pending(db, session_id=session_id, plan="pro")
        result = await maybe_execute_pending(
            db, user={"user_id": "u2", "email": "free@x.com"}, session_id=session_id,
            project_id="home", prompt="yes please", bin_ctx=None,
        )
        assert result is not None
        assert result["provider"] == "confirm-executor"
        assert "nothing pending" not in result["content"].lower()
        assert "checkout.stripe.com" in result["content"]
        assert result["meta"]["executed"] is True
        assert await get_pending_action(db, session_id) is None
    finally:
        await db.chat_sessions.delete_one({"session_id": session_id})


# ── t_confirm_executor_handles_both_types ────────────────────────────
@pytest.mark.asyncio
async def test_t_confirm_executor_handles_both_types(monkeypatch):
    """ONE function dispatches on `action["type"]` -- not two
    hardcoded, separately-wired call paths."""
    src = inspect.getsource(maybe_execute_pending)
    assert 'action.get("type") == "upgrade"' in src
    assert 'action.get("type") == "code_fence"' in src

    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")

    async def _fake_checkout(user, plan, origin_url=""):
        return {"url": "u", "checkout_url": "u", "session_id": "s"}
    monkeypatch.setattr("routers.payments.create_checkout_session", _fake_checkout)

    for action_type, register in [
        ("upgrade", lambda sid: register_upgrade_pending(db, session_id=sid, plan="pro")),
    ]:
        session_id = f"confirm-exec-dispatch-{uuid.uuid4()}"
        try:
            await register(session_id)
            result = await maybe_execute_pending(
                db, user={"user_id": "u3"}, session_id=session_id, project_id="home",
                prompt="approve", bin_ctx=None,
            )
            assert result is not None
            assert result["meta"]["action_type"] == action_type
        finally:
            await db.chat_sessions.delete_one({"session_id": session_id})


# ── t_no_duplicate_guard_wiring ───────────────────────────────────────
def test_t_no_duplicate_guard_wiring():
    """The confirm-execution entry point and the output-guard chain
    each live in exactly one function, called from exactly the 2 real
    chat endpoints (chat_send, chat_stream) -- no 3rd/4th/5th ad-hoc
    copy anywhere else in the router.

    2026-09-05 Commit-Boundary round: routers/chat.py now calls
    `services.commit_boundary.resolve_turn_start` (which itself
    dispatches to THIS module's `maybe_execute_pending` only when
    COMMIT_BOUNDARY_ENABLED=false) -- so chat.py itself no longer
    references `maybe_execute_pending` directly.

    2026-09-06 Phase 1 chat.py refactor (the "load-bearing dedup"):
    the confirm-boundary check is now ONE OF FIVE pre-LLM checks
    (confirm-boundary / intent-classify / upgrade-offer / self-bug /
    casual-direct-reply) collapsed into ONE shared function,
    `routers.chat_pre_llm.resolve_pre_llm`, that both chat_send and
    chat_stream call identically -- so `resolve_turn_start(` itself
    no longer appears directly in routers/chat.py at all (it now
    lives inside `resolve_pre_llm`); the call-site-count invariant
    moves to `resolve_pre_llm(`."""
    import routers.chat as chat_mod
    import routers.chat_pre_llm as pre_llm_mod
    src = inspect.getsource(chat_mod)
    pre_llm_src = inspect.getsource(pre_llm_mod)

    assert src.count("resolve_pre_llm(") == 2, (
        "expected exactly 2 call sites (chat_send + chat_stream), "
        f"found {src.count('resolve_pre_llm(')}"
    )
    assert src.count("resolve_turn_start(") == 0, (
        "the confirm-boundary check should no longer be called "
        "directly from routers/chat.py -- it now lives inside the "
        "single shared routers.chat_pre_llm.resolve_pre_llm()"
    )
    assert pre_llm_src.count("resolve_turn_start(") == 1, (
        "resolve_pre_llm should call the confirm-boundary exactly once"
    )
    assert src.count("maybe_execute_pending(") == 0, (
        "chat.py should no longer call the legacy confirm_execution "
        "entry point directly -- it goes through commit_boundary.py now"
    )
    assert src.count("apply_output_guards(") == 2, (
        "expected exactly 2 call sites (chat_send + chat_stream), "
        f"found {src.count('apply_output_guards(')}"
    )
    # The individual guards are no longer called ad-hoc in chat.py
    # outside the shared helper. The 2 EARLY casual-branch
    # applications of apply_no_false_success_guard (pre-dating this
    # round, applying to an intermediate draft before it becomes
    # `result`) moved into resolve_pre_llm() along with the rest of
    # the casual-direct-reply branch in the 2026-09-06 refactor.
    assert src.count("apply_no_edit_deadend_guard(") == 0
    assert src.count("apply_no_orphan_confirm_guard(") == 0
    assert src.count("apply_fabricated_content_guard(") == 0
    assert src.count("apply_no_false_success_guard(") == 0
    assert pre_llm_src.count("apply_no_false_success_guard(") == 1, (
        "expected exactly 1 EARLY casual-branch application inside "
        "the single shared resolve_pre_llm()"
    )

    import services.chat_helpers as ch_mod
    ch_src = inspect.getsource(ch_mod)
    assert ch_src.count("def apply_output_guards(") == 1
    import services.confirm_execution as ce_mod
    assert inspect.getsource(ce_mod).count("def maybe_execute_pending(") == 1
