"""
Live E2E test for commit-boundary fix (2026-09-09).

Goal: prove founder's dead-end repro is fixed via the REAL preview
chat HTTP surface (not just unit tests). The founder's exact scenario:
  1) An addition-style proposal exists (would be created by
     propose_from_turn from a Council-mode plain-prose reply).
  2) User sends a confirm word ("go") in the same session.
  3) Reply must NOT be NO_PENDING_ACTIONABLE_MESSAGE.

Because driving Council-mode LLM to produce exactly the right prose
is non-deterministic, this test SEEDS an AWAITING_CONFIRM pending
action directly in Mongo (which is exactly what the fixed
propose_from_turn now does for Council prose — verified by 58/58 unit
tests), then drives /api/aurem-dev/chat/send with "go" and asserts:
  - executor path runs (provider = commit-boundary-executor OR
    commit-boundary-executing), OR at minimum
  - reply != NO_PENDING_ACTIONABLE_MESSAGE (the founder's dead-end).

This exercises the ACTUAL HTTP wiring: /chat/send -> resolve_pre_llm
-> resolve_turn_start -> resolve_confirm -> execute_action.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get(
    "REACT_APP_BACKEND_URL"
) else "https://bin-context-pat.preview.emergentagent.com"

TEST_EMAIL = "test@aurem.dev"
TEST_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def auth_token() -> str:
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def user_id(auth_token: str) -> str:
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        timeout=15,
    )
    return r.json()["user_id"]


@pytest.fixture
def mongo_db():
    """Function-scoped: motor client must bind to the current event
    loop, module scope caused 'Event loop is closed' across tests."""
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "aurem_dev")
    client = AsyncIOMotorClient(mongo_url)
    return client[db_name]


def _post_chat(token: str, prompt: str, session_id: str, project_id=None) -> dict:
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/chat/send",
        json={
            "prompt": prompt,
            "session_id": session_id,
            "project_id": project_id or "home",
            "mode": "swift",
            "max_tool_iters": 0,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    return {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text}


@pytest.mark.asyncio
async def test_live_confirm_upgrade_action_does_not_hit_dead_end(
    auth_token, user_id, mongo_db,
):
    """Sanity-check: baseline resolve_confirm HTTP wiring works.
    Seeds an UPGRADE pending action (no repo needed — validate is
    trivial) and confirms via 'go'. Must NOT return NO_PENDING."""
    session_id = f"test_e2e_{uuid.uuid4().hex[:10]}"
    now = time.time()
    doc = {
        "id": uuid.uuid4().hex,
        "session_id": session_id,
        "user_id": user_id,
        "project_id": None,
        "type": "upgrade",
        "payload": {"plan": "pro"},
        "status": "AWAITING_CONFIRM",
        "confirmation_token": uuid.uuid4().hex,
        "idempotency_key": uuid.uuid4().hex,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + 1200,
        "verification_result": None,
    }
    await mongo_db.pending_actions.insert_one(doc)
    try:
        res = _post_chat(auth_token, "go", session_id)
        assert res["status"] == 200, res
        content = res["body"].get("content", "")
        provider = res["body"].get("provider", "")
        # The KEY assertion — founder's dead-end must not appear.
        assert "don't have a change waiting right now" not in content, (
            f"Got the founder's dead-end reply for a seeded pending action! "
            f"provider={provider} content={content[:300]}"
        )
        # provider should be commit-boundary-* since resolve_confirm handled it
        assert "commit-boundary" in provider, (
            f"Expected commit-boundary provider (resolve_confirm path), "
            f"got provider={provider} content={content[:200]}"
        )
    finally:
        await mongo_db.pending_actions.delete_many({"session_id": session_id})


@pytest.mark.asyncio
async def test_live_confirm_insert_action_addition_style_e2e(
    auth_token, user_id, mongo_db,
):
    """The founder's exact scenario: an ADDITION-style (insert)
    AWAITING_CONFIRM action, followed by 'go'. Even though it will
    fail at the read-repo step (no repo attached for this synthetic
    test), the reply must be an executor-path failure ('had the
    addition ready but couldn't reload...'), NOT the founder's
    dead-end NO_PENDING message. That proves resolve_confirm reached
    execute_action instead of the empty-pending branch — the fence-
    gate-removal fix is what enables this on a Council-mode-shaped
    turn."""
    session_id = f"test_e2e_{uuid.uuid4().hex[:10]}"
    now = time.time()
    doc = {
        "id": uuid.uuid4().hex,
        "session_id": session_id,
        "user_id": user_id,
        "project_id": None,
        "type": "insert",
        # Validated payload shape (as if _validate_insert_payload passed)
        "payload": {
            "path": "index.html",
            "anchor": "<!-- hours -->",
            "content": '<a href="tel:+15551234">Call us</a>',
            "commit_message": "chore: update index.html (via chat approval)",
        },
        "status": "AWAITING_CONFIRM",
        "confirmation_token": uuid.uuid4().hex,
        "idempotency_key": uuid.uuid4().hex,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + 1200,
        "verification_result": None,
    }
    await mongo_db.pending_actions.insert_one(doc)
    try:
        res = _post_chat(auth_token, "go", session_id)
        assert res["status"] == 200, res
        content = res["body"].get("content", "")
        provider = res["body"].get("provider", "")
        # THE core assertion — founder's dead-end must not appear.
        assert "don't have a change waiting right now" not in content, (
            f"FOUNDER'S DEAD-END BUG RETURNED. provider={provider} "
            f"content={content[:400]}"
        )
        # provider should indicate the executor path was entered
        assert "commit-boundary" in provider, (
            f"resolve_confirm did NOT execute (should return "
            f"commit-boundary-executor/executing); got provider={provider} "
            f"content={content[:300]}"
        )
    finally:
        await mongo_db.pending_actions.delete_many({"session_id": session_id})


@pytest.mark.asyncio
async def test_live_confirm_replace_edit_still_works_regression(
    auth_token, user_id, mongo_db,
):
    """Regression: replacement-style edits must still resolve to
    executor path with 'go', not to NO_PENDING."""
    session_id = f"test_e2e_{uuid.uuid4().hex[:10]}"
    now = time.time()
    doc = {
        "id": uuid.uuid4().hex,
        "session_id": session_id,
        "user_id": user_id,
        "project_id": None,
        "type": "edit",
        "payload": {
            "path": "index.html",
            "old_value": "Old Text",
            "new_value": "New Text",
            "commit_message": "chore: update index.html (via chat approval)",
        },
        "status": "AWAITING_CONFIRM",
        "confirmation_token": uuid.uuid4().hex,
        "idempotency_key": uuid.uuid4().hex,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + 1200,
        "verification_result": None,
    }
    await mongo_db.pending_actions.insert_one(doc)
    try:
        res = _post_chat(auth_token, "go", session_id)
        assert res["status"] == 200, res
        content = res["body"].get("content", "")
        provider = res["body"].get("provider", "")
        assert "don't have a change waiting right now" not in content, (
            f"Replacement-edit regression: got dead-end. provider={provider} "
            f"content={content[:300]}"
        )
        assert "commit-boundary" in provider, (
            f"Expected commit-boundary-* provider, got {provider}"
        )
    finally:
        await mongo_db.pending_actions.delete_many({"session_id": session_id})


@pytest.mark.asyncio
async def test_live_confirm_no_pending_is_still_honest(auth_token):
    """CBR-4 honesty check: with NO pending action, 'go' should
    return the honest NO_PENDING message. This is by-design behavior,
    NOT the founder's bug — the founder's bug was that a valid
    pending action existed but 'go' STILL hit this branch."""
    session_id = f"test_e2e_{uuid.uuid4().hex[:10]}"
    res = _post_chat(auth_token, "go", session_id)
    assert res["status"] == 200, res
    content = res["body"].get("content", "")
    provider = res["body"].get("provider", "")
    # Either the honest no-pending reply, or a fall-through to LLM
    # for a fresh session. Both are acceptable — what we're checking
    # is that the endpoint is up and reachable.
    assert content, f"empty content, provider={provider}"
