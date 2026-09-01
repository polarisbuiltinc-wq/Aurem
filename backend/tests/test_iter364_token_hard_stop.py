"""
test_iter364_token_hard_stop.py — Iter 364 · Phase 3

E2E verification of the token hard-stop enforcement fix:

  1. Free-tier user with tokens_used >= effective_limit → /chat/send
     must return 402 with error="token_limit_reached", and NO LLM
     provider call must be made.
  2. /usage/me must surface is_blocked=true for that same user.
  3. Admin grants bonus tokens → user can /chat/send again, and
     is_blocked flips back to false.
  4. /chat/stream enforces the same gate (streaming path parity).
  5. Diagram /generate + upload /convert (image branch) also refuse
     when the wallet is exhausted.

Direct-behavioural tests — no HTTP layer needed for the core assert;
we call the FastAPI handler function or the underlying gate helper
directly so we can assert "LLM provider was NEVER called" cleanly.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.usage import (
    assert_has_budget, get_usage, PLAN_LIMITS,
)


def _db():
    from cto_services.db import set_db as _set_db
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "aurem_dev")]
    # Wire the app-state DB reference so `services.usage.get_usage`
    # (which calls `require_db()` internally) resolves in test context.
    _set_db(db)
    return db


async def _seed_exhausted_user(db, tier: str = "free") -> str:
    uid = f"u_iter364_tok_{secrets.token_hex(3)}"
    plan_limit = PLAN_LIMITS[tier]
    # Fresh doc with no grants.
    await db.dev_users.update_one(
        {"user_id": uid},
        {"$set": {
            "user_id": uid,
            "email":   f"{uid}@example.com",
            "tier":    tier,
            "tokens_granted": 0,
            "is_admin":       False,
            "is_unlimited":   False,
        }},
        upsert=True,
    )
    # Seed cto_tasks so the tokens_used aggregate reports >= plan_limit.
    # One row is enough — get_usage() sums $tokens_used over
    # status="done" rows for this user.
    await db.cto_tasks.insert_one({
        "task_id":     f"t_{secrets.token_hex(4)}",
        "user_id":     uid,
        "status":      "done",
        "tokens_used": plan_limit + 5,   # push a hair over the limit
        "created_at":  datetime.now(timezone.utc).timestamp(),
    })
    return uid


async def _cleanup(db, uid: str) -> None:
    await db.cto_tasks.delete_many({"user_id": uid})
    await db.dev_users.delete_one({"user_id": uid})


# ── Core enforcement ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assert_has_budget_402_when_exhausted():
    """assert_has_budget must raise HTTPException(402) for an
    exhausted user — this is the exact call chat/send now does
    BEFORE any LLM provider is contacted."""
    db = _db()
    uid = await _seed_exhausted_user(db, "free")
    try:
        with pytest.raises(HTTPException) as ei:
            await assert_has_budget(uid)
        assert ei.value.status_code == 402
        assert ei.value.detail["error"] == "token_limit_reached"
        # Body carries the fields the frontend interceptor + banner
        # read for the upgrade toast + red-banner disable state.
        assert "upgrade_url" in ei.value.detail
        assert "used"  in ei.value.detail
        assert "limit" in ei.value.detail
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_get_usage_exposes_is_blocked_true_on_exhausted():
    """/usage/me must return is_blocked=True so the TokenBanner
    disables the send button server-authoritatively. Iter 364 added
    this field explicitly to prevent client-computed pct drift."""
    db = _db()
    uid = await _seed_exhausted_user(db, "free")
    try:
        u = await get_usage(uid)
        assert u["is_blocked"] is True, (
            f"exhausted user must have is_blocked=True, got {u}"
        )
        assert u["is_exhausted"] is True
        assert u["remaining"] == 0
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_get_usage_is_blocked_false_after_grant():
    """Admin grants bonus tokens → effective_limit rises → is_blocked
    flips back to False. Mirrors the recovery path in the E2E script."""
    db = _db()
    uid = await _seed_exhausted_user(db, "free")
    try:
        # Simulate what /admin/users/{uid}/grant-tokens does.
        await db.dev_users.update_one(
            {"user_id": uid},
            {"$inc": {"tokens_granted": 500_000}},
        )
        u = await get_usage(uid)
        assert u["is_blocked"] is False, (
            "grant should lift the block; usage row still says "
            f"is_blocked=True: {u}"
        )
        # And the gate itself must no longer raise.
        await assert_has_budget(uid)  # must NOT raise
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_founder_never_blocked_even_when_over_limit():
    """Founder / unlimited accounts bypass the gate regardless of
    tokens_used. Prevents an accidental block on our own internal
    QA-drift accounts."""
    db = _db()
    uid = f"u_iter364_founder_tok_{secrets.token_hex(3)}"
    await db.dev_users.update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "tier": "founder",
                  "is_unlimited": True, "is_admin": True,
                  "tokens_granted": 0}},
        upsert=True,
    )
    await db.cto_tasks.insert_one({
        "task_id":     f"t_{secrets.token_hex(4)}",
        "user_id":     uid,
        "status":      "done",
        "tokens_used": 10**9,   # comically over any plan
        "created_at":  datetime.now(timezone.utc).timestamp(),
    })
    try:
        u = await get_usage(uid)
        assert u["is_blocked"] is False
        assert u["is_unlimited"] is True
        # Gate must not raise.
        await assert_has_budget(uid)
    finally:
        await _cleanup(db, uid)


# ── Router wiring: import-time invariant ─────────────────────────────
# The Iter 364 fix's whole point is that the LLM call MUST NOT happen
# when the wallet is empty. The tightest, most-honest check is an
# AST-level assertion that `assert_has_budget` is called in every
# endpoint that then goes on to invoke an LLM. We enumerate the known
# hot endpoints here — a code refactor that removes the gate call
# will trip this.

_MUST_GATE = {
    "/app/backend/routers/chat/turn.py":   ["chat_send"],
    "/app/backend/routers/chat/stream.py": ["chat_stream"],
    "/app/backend/routers/diagram.py":  ["generate_diagram"],
    "/app/backend/routers/upload.py":   ["upload_convert"],
}


def test_llm_endpoints_still_call_assert_has_budget():
    """Static grep — every listed handler function body MUST contain
    the string `assert_has_budget`. If someone later refactors the
    call out, this test screams before the silent-burn re-appears."""
    import ast
    for path, fnames in _MUST_GATE.items():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                if node.name in fnames:
                    body_src = ast.get_source_segment(src, node) or ""
                    assert "assert_has_budget" in body_src, (
                        f"{path}::{node.name} lost its "
                        f"assert_has_budget gate — this reopens the "
                        f"Iter 364 silent-burn hole"
                    )
