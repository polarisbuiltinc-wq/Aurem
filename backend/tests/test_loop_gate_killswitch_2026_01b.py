"""
Iter 212m-181 (follow-up delta) — Loop Mode kill-switch coupling on
/chat/stream.

This is a small delta on top of test_loop_gate_chat_stream_2026_01.py.
The previous run flagged that routers/chat.py's Loop Mode gate consulted
tier eligibility only, and skipped the DB-backed kill-switch that
routers/loop.py::/start already respected. Main agent then added
`if _loop_allowed and await _lb_gate.is_kill_switch_on_async(get_db()):
    _loop_allowed = False` inside chat_stream().

This file verifies ONLY that delta:
  1. Source-level: the new kill-switch line exists next to is_user_allowed
     in routers/chat.py (Iter 212m-181 block).
  2. With kill switch OFF, Pro /chat/stream loop request still opens 200
     (baseline, unchanged).
  3. With kill switch ON:
     • Pro /chat/stream loop still returns 200 (does NOT 403 —
       execution_mode is silently downgraded to 'prompt').
     • Pro /chat/stream prompt (non-loop) is unaffected — 200.
     • /loop/start still 403s with 'loop_mode_kill_switch' (regression).
  4. Explicit teardown that removes the flag.
"""
from __future__ import annotations

import os
import sys
import uuid
import pathlib
import asyncio
import pytest
import requests
from pymongo import MongoClient

BACKEND_DIR = str(pathlib.Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://bin-context-pat.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")

FLAG_KEY = "loop_mode_kill_switch"


# ─── Mongo helpers (kill switch is DB-backed) ────────────────────────

@pytest.fixture(scope="module")
def mongo():
    c = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    return c[DB_NAME]


def _flip_kill_switch(mongo, value: bool):
    mongo.system_flags.update_one(
        {"key": FLAG_KEY},
        {"$set": {"key": FLAG_KEY, "value": value, "reason": "test-delta"}},
        upsert=True,
    )


def _clear_kill_switch(mongo):
    mongo.system_flags.delete_one({"key": FLAG_KEY})


@pytest.fixture(scope="module", autouse=True)
def ensure_clean_state(mongo):
    """Guarantee flag is OFF before AND after this module runs."""
    _clear_kill_switch(mongo)
    yield
    _clear_kill_switch(mongo)


# ─── Signup + Pro promotion (idempotent-ish per-run) ─────────────────

def _signup(email: str, password: str) -> str:
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": password,
        "name": "loop kill-switch delta test", "form_age_ms": 5000,
    }, timeout=20)
    if r.status_code == 409:
        r = requests.post(f"{API}/auth/login", json={
            "email": email, "password": password,
        }, timeout=20)
    assert r.status_code == 200, f"signup/login failed {r.status_code}: {r.text[:400]}"
    return r.json()["token"]


def _promote(mongo, email: str, tier: str):
    res = mongo.dev_users.update_one(
        {"email": email},
        {"$set": {
            "tier": tier,
            "is_admin": False,
            "is_unlimited": False,
            "email_verified": True,
        }},
    )
    assert res.matched_count == 1, f"no dev_users row for {email}"


@pytest.fixture(scope="module")
def pro_token(mongo):
    email = f"loop-ks-pro-{uuid.uuid4().hex[:6]}@aurem-test.dev"
    tok = _signup(email, "LoopKS2026!")
    _promote(mongo, email, "pro")
    yield tok, email
    try:
        mongo.dev_users.delete_one({"email": email})
    except Exception:
        pass


# ─── 1. Source-level: the new line exists in chat.py ─────────────────

class TestChatRouterKillSwitchWired:
    def test_chat_stream_calls_kill_switch(self):
        p = pathlib.Path(BACKEND_DIR) / "routers" / "chat.py"
        src = p.read_text()
        # The new delta.
        assert "is_kill_switch_on_async" in src, (
            "routers/chat.py must call is_kill_switch_on_async in the "
            "/chat/stream loop gate (Iter 212m-181 delta)")
        # And it must be gated so it only runs if the tier check passed
        # (otherwise we'd flap execution_mode redundantly).
        assert "_lb_gate.is_kill_switch_on_async" in src, (
            "delta must live inside the /chat/stream loop block, aliased "
            "as _lb_gate.is_kill_switch_on_async")

    def test_kill_switch_downgrades_not_403s(self):
        """The kill-switch on /chat/stream must ONLY silently downgrade
        execution_mode to 'prompt'. It MUST NOT raise HTTPException(403)
        — that would break non-loop chat when the switch is flipped."""
        p = pathlib.Path(BACKEND_DIR) / "routers" / "chat.py"
        src = p.read_text()
        # Grab the loop-gate block and confirm no 403 inside it.
        idx = src.find("Iter 212m-181")
        assert idx > -1
        # Look at the next ~1500 chars after the marker (the gate block).
        block = src[idx: idx + 1500]
        assert "HTTPException(403" not in block, (
            "the kill-switch delta must not 403 inside /chat/stream — "
            "it must only downgrade execution_mode to 'prompt'")
        assert "loop_mode_kill_switch" not in block or "execution_mode" in block


# ─── 2. Unit: is_kill_switch_on_async reads system_flags correctly ──

class TestKillSwitchAsyncUnit:
    def test_reads_true_when_flag_true(self, mongo):
        from motor.motor_asyncio import AsyncIOMotorClient
        from services import loop_beta

        _flip_kill_switch(mongo, True)
        try:
            async def _run():
                cli = AsyncIOMotorClient(MONGO_URL)
                db = cli[DB_NAME]
                try:
                    return await loop_beta.is_kill_switch_on_async(db)
                finally:
                    cli.close()
            assert asyncio.run(_run()) is True
        finally:
            _clear_kill_switch(mongo)

    def test_reads_false_when_flag_absent(self, mongo):
        from motor.motor_asyncio import AsyncIOMotorClient
        from services import loop_beta
        _clear_kill_switch(mongo)

        async def _run():
            cli = AsyncIOMotorClient(MONGO_URL)
            db = cli[DB_NAME]
            try:
                return await loop_beta.is_kill_switch_on_async(db)
            finally:
                cli.close()
        assert asyncio.run(_run()) is False


# ─── 3. E2E: Behaviour of both endpoints under the flag ──────────────

class TestKillSwitchE2E:
    def _post_stream(self, token: str, prompt: str, mode: str = "loop"):
        return requests.post(
            f"{API}/chat/stream",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
            },
            json={
                "prompt": prompt,
                "execution_mode": mode,
                "mode": "swift",
                "project_id": "home",
            },
            timeout=15,
            stream=True,
        )

    def _post_loop_start(self, token: str, msg: str):
        return requests.post(
            f"{API}/loop/start",
            headers={"Authorization": f"Bearer {token}"},
            json={"user_message": msg},
            timeout=20,
        )

    # ── Baseline (flag OFF) ──────────────────────────────────────────
    def test_pro_loop_stream_ok_when_flag_off(self, pro_token, mongo):
        _clear_kill_switch(mongo)
        tok, _ = pro_token
        try:
            r = self._post_stream(tok, "LOOP_PHASE:plan add a null check")
        except requests.exceptions.ReadTimeout:
            pytest.skip("upstream LLM slow")
        assert r.status_code == 200, r.text[:400]
        r.close()

    # ── Flag ON: /chat/stream must NOT 4xx/5xx, just downgrade ───────
    def test_pro_loop_stream_still_200_when_flag_on(self, pro_token, mongo):
        _flip_kill_switch(mongo, True)
        try:
            tok, _ = pro_token
            try:
                r = self._post_stream(tok, "LOOP_PHASE:plan hello")
            except requests.exceptions.ReadTimeout:
                pytest.skip("upstream LLM slow")
            # Must be 200 — silent downgrade, not a 403.
            assert r.status_code == 200, (
                f"Kill switch caused /chat/stream to fail (should silently "
                f"downgrade to prompt): {r.status_code} {r.text[:400]}")
            r.close()
        finally:
            _clear_kill_switch(mongo)

    def test_pro_prompt_stream_unaffected_when_flag_on(self, pro_token, mongo):
        """Non-loop chat must be totally unaffected by the kill switch."""
        _flip_kill_switch(mongo, True)
        try:
            tok, _ = pro_token
            try:
                r = self._post_stream(tok, "hello", mode="prompt")
            except requests.exceptions.ReadTimeout:
                pytest.skip("upstream LLM slow")
            assert r.status_code == 200, (
                f"Non-loop /chat/stream broke under kill switch: "
                f"{r.status_code} {r.text[:400]}")
            r.close()
        finally:
            _clear_kill_switch(mongo)

    # ── Flag ON: /loop/start must 403 with loop_mode_kill_switch ─────
    def test_pro_loop_start_403_kill_switch_when_flag_on(self,
                                                        pro_token, mongo):
        _flip_kill_switch(mongo, True)
        try:
            tok, _ = pro_token
            r = self._post_loop_start(tok, "add a null check to server.py")
            assert r.status_code == 403, r.text[:400]
            assert "loop_mode_kill_switch" in r.text, r.text[:400]
        finally:
            _clear_kill_switch(mongo)

    def test_founder_loop_start_403_kill_switch_when_flag_on(self, mongo):
        """Even founder must be blocked from /loop/start when flag is on."""
        _flip_kill_switch(mongo, True)
        try:
            r = requests.post(f"{API}/auth/login", json={
                "email": "test@aurem.dev", "password": "AuremTest2026!",
            }, timeout=15)
            assert r.status_code == 200, r.text[:300]
            founder_tok = r.json()["token"]
            r = self._post_loop_start(founder_tok,
                                      "add a null check to server.py")
            assert r.status_code == 403, r.text[:400]
            assert "loop_mode_kill_switch" in r.text, r.text[:400]
        finally:
            _clear_kill_switch(mongo)


# ─── 4. Explicit final teardown check ────────────────────────────────

class TestFlagCleanup:
    def test_flag_removed_at_end(self, mongo):
        _clear_kill_switch(mongo)
        row = mongo.system_flags.find_one({"key": FLAG_KEY})
        assert row is None or row.get("value") is False, (
            "kill switch left ON after test module — this would break "
            "Loop Mode for real users")
