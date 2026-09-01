"""
Iter 212m-181 — Loop Mode gate regression tests.

Fix under test: routers/chat.py::/chat/stream previously used a
hardcoded "founder-only" check to gate execution_mode='loop'; it now
delegates to services/loop_beta.is_user_allowed() — the same function
POST /api/aurem-dev/loop/start already uses — so Pro/Team customers
are no longer silently downgraded to execution_mode='prompt'.

This file:
  1. Unit-tests loop_beta.is_user_allowed() across all tier cases
     (single source of truth for BOTH entry points).
  2. Verifies routers/chat.py has been rewired to call
     loop_beta.is_user_allowed() and no longer contains the old
     founder-only literal gate (drift regression).
  3. E2E: signs up a Pro-tier user + Team-tier user via /auth/signup,
     promotes them via Mongo, then hits POST /loop/start with a
     read-only-safe action verb prompt to confirm they are NOT rejected
     with 403 tier_locked (regression of the unmodified /loop/start
     path — should still work since it already used loop_beta).
  4. E2E: hits /chat/stream with execution_mode='loop' for Pro/Team
     users and asserts the response opens (2xx) — a smoke test that
     the code change didn't introduce a Python-level regression on
     the stream path.
  5. Explicit "finding" test: documents that
     loop_beta.is_user_allowed() intentionally does NOT consult the
     kill-switch — kill-switch enforcement remains ONLY at
     /loop/start (not in the chat.py contract-enrichment gate).
"""
import os
import sys
import time
import uuid
import pathlib
import pytest
import requests
from pymongo import MongoClient

# Make backend/ importable so we can unit-test the pure function
BACKEND_DIR = str(pathlib.Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services import loop_beta  # noqa: E402

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") \
    if os.environ.get("REACT_APP_BACKEND_URL") \
    else "https://bin-context-pat.preview.emergentagent.com"
API = f"{BASE_URL}/api/aurem-dev"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME",   "aurem_dev")


# ─── 1. is_user_allowed() unit tests ────────────────────────────────

class TestIsUserAllowedUnit:
    def test_none_user(self):
        allowed, reason = loop_beta.is_user_allowed(None)
        assert allowed is False
        assert reason == "no_user"

    def test_free_locked(self):
        allowed, reason = loop_beta.is_user_allowed({"tier": "free"})
        assert allowed is False
        assert reason == "tier_locked"

    def test_starter_locked(self):
        allowed, reason = loop_beta.is_user_allowed({"tier": "starter"})
        assert allowed is False
        assert reason == "tier_locked"

    def test_pro_allowed(self):
        allowed, reason = loop_beta.is_user_allowed({"tier": "pro"})
        assert allowed is True
        assert reason == ""

    def test_team_allowed(self):
        allowed, reason = loop_beta.is_user_allowed({"tier": "team"})
        assert allowed is True
        assert reason == ""

    def test_founder_tier_allowed(self):
        allowed, _ = loop_beta.is_user_allowed({"tier": "founder"})
        assert allowed is True

    def test_admin_flag_bypasses(self):
        allowed, _ = loop_beta.is_user_allowed(
            {"is_admin": True, "tier": "free"})
        assert allowed is True

    def test_unlimited_flag_bypasses(self):
        allowed, _ = loop_beta.is_user_allowed(
            {"is_unlimited": True, "tier": "free"})
        assert allowed is True

    def test_pro_case_insensitive(self):
        allowed, _ = loop_beta.is_user_allowed({"tier": "PRO"})
        assert allowed is True


# ─── 2. Code-level: routers/chat.py wired to loop_beta ──────────────

class TestChatRouterGateWired:
    def test_chat_router_uses_loop_beta_gate(self):
        p = pathlib.Path(BACKEND_DIR) / "routers" / "chat" / "stream.py"
        src = p.read_text()
        # The fix must import and call the shared gate.
        assert "loop_beta" in src, "routers/chat.py must reference loop_beta"
        assert "is_user_allowed(user)" in src, (
            "routers/chat.py must call is_user_allowed(user) so it "
            "shares a source of truth with /loop/start"
        )

    def test_chat_router_downgrade_still_happens_for_disallowed(self):
        """Confirm the disallowed → prompt downgrade branch still exists."""
        p = pathlib.Path(BACKEND_DIR) / "routers" / "chat" / "stream.py"
        src = p.read_text()
        # The fix explicitly sets execution_mode = "prompt" when not allowed.
        assert 'body.execution_mode = "prompt"' in src or \
               "body.execution_mode = 'prompt'" in src


# ─── 3. E2E fixtures — real signup + tier promotion via Mongo ──────

@pytest.fixture(scope="module")
def mongo():
    c = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    return c[DB_NAME]


def _signup(email: str, password: str) -> str:
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": password,
        "name":  "loop gate test", "form_age_ms": 5000,
    }, timeout=20)
    if r.status_code == 409:
        # already exists — just log in
        r = requests.post(f"{API}/auth/login", json={
            "email": email, "password": password,
        }, timeout=20)
    assert r.status_code == 200, (
        f"signup/login failed {r.status_code}: {r.text[:400]}")
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
    email = f"loop-gate-pro-{uuid.uuid4().hex[:6]}@aurem-test.dev"
    tok = _signup(email, "LoopGate2026!")
    _promote(mongo, email, "pro")
    yield tok, email
    # keep row for post-mortem; cleanup is fine either way
    try:
        mongo.dev_users.delete_one({"email": email})
    except Exception:
        pass


@pytest.fixture(scope="module")
def team_token(mongo):
    email = f"loop-gate-team-{uuid.uuid4().hex[:6]}@aurem-test.dev"
    tok = _signup(email, "LoopGate2026!")
    _promote(mongo, email, "team")
    yield tok, email
    try:
        mongo.dev_users.delete_one({"email": email})
    except Exception:
        pass


@pytest.fixture(scope="module")
def free_token(mongo):
    email = f"loop-gate-free-{uuid.uuid4().hex[:6]}@aurem-test.dev"
    tok = _signup(email, "LoopGate2026!")
    # signup already lands as free; assert to be safe
    _promote(mongo, email, "free")
    yield tok, email
    try:
        mongo.dev_users.delete_one({"email": email})
    except Exception:
        pass


@pytest.fixture(scope="module")
def founder_token():
    # Preview founder from /app/memory/test_credentials.md
    r = requests.post(f"{API}/auth/login", json={
        "email": "test@aurem.dev", "password": "AuremTest2026!",
    }, timeout=15)
    assert r.status_code == 200, r.text[:300]
    return r.json()["token"]


# ─── 4. /loop/start regression (unmodified endpoint) ────────────────

class TestLoopStartRegression:
    """POST /loop/start already used loop_beta.is_user_allowed pre-fix.
    This is the regression baseline — must still work for Pro/Team,
    still 403 for Free."""

    def _post_loop_start(self, token: str, msg: str):
        return requests.post(
            f"{API}/loop/start",
            headers={"Authorization": f"Bearer {token}"},
            json={"user_message": msg},
            timeout=25,
        )

    def test_pro_not_tier_locked(self, pro_token):
        tok, _ = pro_token
        r = self._post_loop_start(tok, "add a null check to server.py")
        # Must NOT be 403 tier_locked. Could be 200 (loop kicked) or
        # 403 kill-switch or 403 concurrency, or a redirect_to_chat 200.
        assert r.status_code != 403 or \
            "tier_locked" not in r.text and "loop_mode_locked" not in r.text, (
                f"Pro user got tier-locked from /loop/start: {r.text[:400]}"
            )

    def test_team_not_tier_locked(self, team_token):
        tok, _ = team_token
        r = self._post_loop_start(tok, "add a null check to server.py")
        assert r.status_code != 403 or \
            "tier_locked" not in r.text and "loop_mode_locked" not in r.text, (
                f"Team user got tier-locked from /loop/start: {r.text[:400]}"
            )

    def test_free_locked(self, free_token):
        tok, _ = free_token
        r = self._post_loop_start(tok, "add a null check to server.py")
        assert r.status_code == 403, r.text[:400]
        assert "loop_mode_locked" in r.text or "tier_locked" in r.text, \
            r.text[:400]

    def test_founder_allowed(self, founder_token):
        r = self._post_loop_start(founder_token,
                                  "add a null check to server.py")
        assert r.status_code != 403 or "loop_mode_locked" not in r.text, \
            r.text[:400]


# ─── 5. /chat/stream integration smoke (the actual fix path) ───────

class TestChatStreamLoopGate:
    """Hit /chat/stream with execution_mode='loop'. We can't observe
    the enriched prompt from outside, but we can verify:
      • the request returns 200 (not 4xx/5xx) — proves the code path
        with the new gate imports + executes cleanly (no syntax/
        import breakage from the fix).
      • founder still works (regression).
    """

    def _post_stream(self, token: str, prompt: str = "hello"):
        return requests.post(
            f"{API}/chat/stream",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
            },
            json={
                "prompt":         prompt,
                "execution_mode": "loop",
                "mode":           "swift",
                "project_id":     "home",
            },
            timeout=15,
            stream=True,
        )

    def test_pro_stream_opens(self, pro_token):
        tok, _ = pro_token
        try:
            r = self._post_stream(tok, "LOOP_PHASE:plan add a null check")
        except requests.exceptions.ReadTimeout:
            pytest.skip("upstream LLM slow — headers not received in 15s")
        assert r.status_code == 200, \
            f"Pro /chat/stream failed: {r.status_code} {r.text[:400]}"
        r.close()

    def test_team_stream_opens(self, team_token):
        tok, _ = team_token
        try:
            r = self._post_stream(tok, "LOOP_PHASE:plan add a null check")
        except requests.exceptions.ReadTimeout:
            pytest.skip("upstream LLM slow — headers not received in 15s")
        assert r.status_code == 200, \
            f"Team /chat/stream failed: {r.status_code} {r.text[:400]}"
        r.close()

    def test_free_stream_opens_but_downgraded(self, free_token):
        """Free tier should NOT be rejected — gate silently downgrades
        execution_mode to 'prompt' rather than returning an error.
        Regression check: response still 200 (not blocked)."""
        tok, _ = free_token
        try:
            r = self._post_stream(tok, "hello just a small message")
        except requests.exceptions.ReadTimeout:
            pytest.skip("upstream LLM slow — headers not received in 15s")
        assert r.status_code == 200, \
            f"Free /chat/stream (loop→prompt downgrade) failed: " \
            f"{r.status_code} {r.text[:400]}"
        r.close()

    def test_founder_stream_opens(self, founder_token):
        try:
            r = self._post_stream(founder_token,
                                  "LOOP_PHASE:plan hello")
        except requests.exceptions.ReadTimeout:
            pytest.skip("upstream LLM slow — headers not received in 15s")
        assert r.status_code == 200, \
            f"Founder /chat/stream failed: {r.status_code} {r.text[:400]}"
        r.close()


# ─── 6. Kill-switch scope finding (report, not fail) ────────────────

class TestKillSwitchScopeFinding:
    """FINDING (not a bug per review_request): loop_beta.is_user_allowed
    does NOT consult the kill-switch. The kill-switch is enforced ONLY
    at POST /loop/start (loop.py line ~98). The /chat/stream loop
    contract-enrichment gate — which just adds a system-prompt suffix —
    does NOT respect the DB-backed kill-switch even after this fix.

    Whether that's a real gap depends on threat model:
      • Users can't ACTUALLY run Loop end-to-end without /loop/start,
        because /chat/stream doesn't drive the loop state machine.
      • But if the kill-switch is meant to also stop LLMs from being
        prompted with the loop contract on continuation turns, it does
        not do that today.

    This test formalises current behaviour so a future accidental fix
    that couples the two doesn't sneak through unnoticed."""

    def test_kill_switch_not_consulted_by_is_user_allowed(self):
        # Passing a fake db doesn't matter — is_user_allowed's signature
        # does not accept one. That's the finding.
        import inspect
        sig = inspect.signature(loop_beta.is_user_allowed)
        params = list(sig.parameters.keys())
        assert params == ["user_doc"], (
            f"is_user_allowed signature changed to {params}; if it now "
            "accepts db, kill-switch coupling may have been added — "
            "re-verify /chat/stream gate calls it with the right args."
        )
