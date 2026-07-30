"""
test_iter365_signup_guards_and_funnel.py — Iter 365

Behavioural tests for:
  - Signup abuse protection (disposable email, honeypot, timing, per-IP)
  - Funnel event helper
  - AST invariant: /auth/signup keeps calling enforce_signup_guards
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services import signup_guards as sg


def _db():
    from cto_services.db import set_db as _set_db
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "aurem_dev")]
    _set_db(db)
    return db


# ── Disposable domain block ─────────────────────────────────────────

@pytest.mark.parametrize("email,is_bad", [
    ("real@gmail.com",         False),
    ("bad@mailinator.com",     True),
    ("test@10minutemail.com",  True),
    ("test@guerrillamail.com", True),
    ("dev@company.io",         False),
    ("no-at-sign",             False),
    ("",                       False),
])
def test_disposable_detection(email, is_bad):
    assert sg._disposable_hit(email) is is_bad


# ── Honeypot ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_honeypot_rejects():
    db = _db()
    with pytest.raises(HTTPException) as ei:
        await sg.enforce_signup_guards(
            db, email="human@example.com", ip="1.2.3.4",
            honeypot="I am a bot",
        )
    assert ei.value.status_code == 400
    assert ei.value.detail["error"] == "signup_rejected"


@pytest.mark.asyncio
async def test_honeypot_blank_passes():
    """Empty / None honeypot must NOT reject a real signup."""
    db = _db()
    # Use unique IP so rate-limit doesn't trip; disposable off; no timing.
    await sg.enforce_signup_guards(
        db, email="real@example.com", ip=f"9.9.{secrets.randbelow(255)}.1",
        honeypot="",
    )


# ── Timing check ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_form_submitted_too_fast_rejects():
    db = _db()
    with pytest.raises(HTTPException) as ei:
        await sg.enforce_signup_guards(
            db, email="human@example.com",
            ip=f"5.5.{secrets.randbelow(255)}.1",
            form_age_ms=500,
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_form_reasonable_timing_passes():
    db = _db()
    await sg.enforce_signup_guards(
        db, email="human2@example.com",
        ip=f"5.5.{secrets.randbelow(255)}.2",
        form_age_ms=6000,
    )


# ── Per-IP rate limit ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_per_ip_rate_limit_triggers():
    """Seed >SIGNUP_RATE_LIMIT_PER_IP dev_users rows for one IP in the
    last 24h and confirm the guard 429s."""
    db = _db()
    ip = f"10.11.12.{secrets.randbelow(200)}"
    now = datetime.now(timezone.utc).timestamp()
    seeds = []
    try:
        for _ in range(sg.SIGNUP_RATE_LIMIT_PER_IP):
            uid = f"u_iter365_ip_{secrets.token_hex(3)}"
            await db.dev_users.insert_one({
                "user_id":    uid,
                "email":      f"{uid}@example.com",
                "signup_ip":  ip,
                "created_at": now,
                "tier":       "free",
            })
            seeds.append(uid)
        with pytest.raises(HTTPException) as ei:
            await sg.enforce_signup_guards(
                db, email=f"one_more@example.com", ip=ip,
            )
        assert ei.value.status_code == 429
        assert ei.value.detail["error"] == "signup_rate_limit"
    finally:
        await db.dev_users.delete_many({"user_id": {"$in": seeds}})


@pytest.mark.asyncio
async def test_founder_bypasses_all_guards(monkeypatch):
    """Even a founder email with a fake bot signature must pass."""
    monkeypatch.setenv("FOUNDER_EMAILS", "iter365_founder@aurem.live")
    db = _db()
    # Try every abuse signal simultaneously — founder MUST still pass.
    await sg.enforce_signup_guards(
        db,
        email="iter365_founder@aurem.live",
        ip="1.1.1.1",
        honeypot="botsig",
        form_age_ms=100,
    )


# ── Funnel event helper ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_funnel_event_persists_row():
    db = _db()
    uid = f"u_iter365_fn_{secrets.token_hex(3)}"
    try:
        await sg.emit_funnel_event(
            db, user_id=uid, event_type="signup_completed",
            metadata={"tier": "free"},
        )
        row = await db.funnel_events.find_one({"user_id": uid})
        assert row is not None
        assert row["event_type"] == "signup_completed"
        assert row["metadata"]["tier"] == "free"
        assert "created_at" in row
    finally:
        await db.funnel_events.delete_many({"user_id": uid})


# ── AST invariant — signup handler must still call the guard ────────

def test_signup_handler_still_calls_enforce_guards():
    import ast
    with open("/app/backend/routers/auth.py", encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "signup":
            body_src = ast.get_source_segment(src, node) or ""
            assert "enforce_signup_guards" in body_src, (
                "routers/auth.py::signup lost its signup-abuse guard "
                "— this reopens the Iter 365 abuse hole"
            )
            return
    pytest.fail("signup handler not found in routers/auth.py")
