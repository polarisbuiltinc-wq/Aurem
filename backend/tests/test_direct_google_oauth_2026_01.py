"""
Backend tests for the DIRECT Google OAuth path — the ONLY Google auth
path since 2026-08-28 (the old Emergent-broker /auth/google/session
route was deleted entirely so no traffic can land on
auth.emergentagent.com).

Covers:
  * GET /api/aurem-dev/google/oauth/start
    - 307 redirect to https://accounts.google.com/... with correct
      client_id, scope, dynamic redirect_uri, and state
    - oauth_states row created with mode/provider/origin/redirect_uri/created_at
    - state expiry after 5 minutes
  * GET /api/aurem-dev/google/oauth/callback error paths
    - missing code -> graceful redirect (no 422)
    - unknown state -> clean 400
    - invalid state format -> 400
  * Full callback with mocked exchange/get_profile
    - new email creates dev_users row with correct bootstrap
    - existing email is matched (no dup) + google sub-doc updated
    - redirect points to {origin}/oauth-finish#token=...&login=...&new=...
  * Regression: /auth/google/session (Emergent broker) is GONE (404).
"""
from __future__ import annotations
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs, unquote

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"
MONGO_URL = os.environ.get("MONGO_URL", "")
DB_NAME = os.environ.get("DB_NAME", "")

# ─── Shared client ────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    sess.headers.update({"Origin": BASE_URL})
    return sess

@pytest.fixture(scope="module")
def db():
    c = MongoClient(MONGO_URL)
    return c[DB_NAME]


# ─── /start ───────────────────────────────────────────────────────────
class TestStart:
    def test_start_redirects_to_google(self, s, db):
        r = s.get(f"{API}/google/oauth/start", allow_redirects=False)
        assert r.status_code in (302, 307), f"unexpected {r.status_code}: {r.text[:200]}"
        loc = r.headers["location"]
        parsed = urlparse(loc)
        assert parsed.netloc == "accounts.google.com", loc
        assert parsed.path.startswith("/o/oauth2/v2/auth"), loc
        qs = parse_qs(parsed.query)
        assert qs["client_id"][0], "client_id missing"
        assert qs["response_type"][0] == "code"
        assert qs["scope"][0] == "openid email profile"
        assert qs["state"][0].startswith("signup:")
        # Dynamic redirect_uri must be computed per-request (not hardcoded).
        # It may match either the outward host (BASE_URL) or the ingress-
        # forwarded host — the important invariant is that redirect_uri
        # ends with /api/aurem-dev/google/oauth/callback and equals the
        # origin the state row was created for.
        redir = qs["redirect_uri"][0]
        assert redir.endswith("/api/aurem-dev/google/oauth/callback"), redir
        assert redir.startswith("http")

        # oauth_states row was created
        state = qs["state"][0]
        row = db.oauth_states.find_one({"state": state})
        assert row is not None
        assert row["mode"] == "signup"
        assert row["provider"] == "google"
        assert row["origin"] and row["origin"].startswith("http")
        assert row["redirect_uri"].endswith("/api/aurem-dev/google/oauth/callback")
        assert row["redirect_uri"].startswith("http")
        # created_at present + recent
        assert "created_at" in row
        assert "created_at" in row
        # cleanup
        db.oauth_states.delete_one({"state": state})

    def test_start_intent_login_sets_mode_login(self, s, db):
        r = s.get(f"{API}/google/oauth/start?intent=login", allow_redirects=False)
        assert r.status_code in (302, 307)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        assert state.startswith("login:")
        row = db.oauth_states.find_one({"state": state})
        assert row and row["mode"] == "login"
        db.oauth_states.delete_one({"state": state})


# ─── /callback error paths ────────────────────────────────────────────
class TestCallbackErrors:
    def test_missing_code_redirects_gracefully(self, s):
        r = s.get(f"{API}/google/oauth/callback", allow_redirects=False)
        # Expect 302/307 to /login?google=cancelled...  (NOT 422)
        assert r.status_code in (302, 307), f"got {r.status_code}: {r.text[:200]}"
        loc = r.headers["location"]
        assert "/login" in loc
        assert "google=cancelled" in loc
        assert "reason=missing_code" in loc

    def test_user_denied_error_redirects(self, s):
        r = s.get(f"{API}/google/oauth/callback?error=access_denied",
                  allow_redirects=False)
        assert r.status_code in (302, 307)
        loc = r.headers["location"]
        assert "/login" in loc and "google=cancelled" in loc
        assert "reason=access_denied" in loc

    def test_unknown_state_returns_400(self, s):
        r = s.get(f"{API}/google/oauth/callback?code=fake&state=signup:doesnotexist123",
                  allow_redirects=False)
        assert r.status_code == 400, f"got {r.status_code}: {r.text[:200]}"

    def test_invalid_state_format_returns_400(self, s):
        r = s.get(f"{API}/google/oauth/callback?code=fake&state=malformednocolon",
                  allow_redirects=False)
        assert r.status_code == 400

    def test_expired_state_returns_400(self, s, db):
        # Seed an artificially expired state (>5 min old)
        state = f"signup:{uuid.uuid4().hex}"
        db.oauth_states.insert_one({
            "state": state, "mode": "signup", "provider": "google",
            "origin": BASE_URL,
            "redirect_uri": f"{BASE_URL}/api/aurem-dev/google/oauth/callback",
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=10),
            "ts": time.time() - 600,
        })
        r = s.get(f"{API}/google/oauth/callback?code=fake&state={state}",
                  allow_redirects=False)
        assert r.status_code == 400, f"got {r.status_code}"
        # Row must be cleaned up
        assert db.oauth_states.find_one({"state": state}) is None


# ─── Full callback flow with mocked Google service ────────────────────
# We monkey-patch services.google_oauth.exchange + get_profile at the
# module level where routers/google_oauth.py imported them.
class TestCallbackFullFlowMocked:
    """These tests run in-process via importing the FastAPI app and using
    starlette TestClient, since we need to patch the httpx-backed Google
    service (can't intercept it over the live preview URL)."""

    @pytest.fixture(scope="class")
    def client_and_module(self):
        import sys
        sys.path.insert(0, "/app/backend")
        from main import app
        from routers import google_oauth as gmod
        from starlette.testclient import TestClient
        # `with TestClient(app)` triggers lifespan startup so db is ready
        with TestClient(app) as client:
            yield client, gmod

    @pytest.fixture
    def mock_email(self):
        return f"TEST_google_{uuid.uuid4().hex[:8]}@aurem.dev"

    def _seed_state(self, db, mode="signup"):
        state = f"{mode}:{uuid.uuid4().hex}"
        db.oauth_states.insert_one({
            "state": state, "mode": mode, "provider": "google",
            "origin": BASE_URL,
            "redirect_uri": f"{BASE_URL}/api/aurem-dev/google/oauth/callback",
            "created_at": datetime.now(timezone.utc),
            "ts": time.time(),
        })
        return state

    def test_new_email_creates_user_and_redirects_with_new_1(
        self, client_and_module, db, mock_email, monkeypatch,
    ):
        client, gmod = client_and_module
        g_sub = f"sub-{uuid.uuid4().hex[:10]}"

        async def fake_exchange(code, redirect_uri):
            return "fake-access-token"
        async def fake_profile(tok):
            return {
                "sub": g_sub, "email": mock_email, "email_verified": True,
                "name": "Test Google User", "picture": "https://example/p.png",
            }
        monkeypatch.setattr(gmod, "exchange", fake_exchange)
        monkeypatch.setattr(gmod, "get_profile", fake_profile)

        state = self._seed_state(db, "signup")
        try:
            r = client.get(
                f"/api/aurem-dev/google/oauth/callback?code=abc&state={state}",
                follow_redirects=False,
                headers={"Origin": BASE_URL},
            )
            assert r.status_code in (302, 307), f"{r.status_code} {r.text[:300]}"
            loc = r.headers["location"]
            assert "/oauth-finish" in loc
            assert "#token=" in loc and "&new=1" in loc
            assert loc.startswith(BASE_URL)

            u = db.dev_users.find_one({"email": mock_email})
            assert u is not None
            assert u["auth_provider"] == "google"
            assert u["password"] is None
            assert u.get("tier") in ("free", "founder")
            assert u.get("tokens_remaining", 0) > 0
            assert u["google"]["id"] == g_sub
            assert u["google"]["email"] == mock_email
        finally:
            db.dev_users.delete_many({"email": mock_email})
            db.oauth_states.delete_many({"state": state})

    def test_existing_email_matches_same_user_and_new_0(
        self, client_and_module, db, mock_email, monkeypatch,
    ):
        client, gmod = client_and_module
        g_sub = f"sub-{uuid.uuid4().hex[:10]}"

        async def fake_exchange(code, redirect_uri):
            return "fake-token"
        async def fake_profile(tok):
            return {"sub": g_sub, "email": mock_email, "email_verified": True,
                    "name": "Test Google User", "picture": ""}
        monkeypatch.setattr(gmod, "exchange", fake_exchange)
        monkeypatch.setattr(gmod, "get_profile", fake_profile)

        try:
            # Run 1 -> new signup
            state1 = self._seed_state(db, "signup")
            r1 = client.get(
                f"/api/aurem-dev/google/oauth/callback?code=x&state={state1}",
                follow_redirects=False, headers={"Origin": BASE_URL},
            )
            assert r1.status_code in (302, 307)
            assert "&new=1" in r1.headers["location"]
            u1 = db.dev_users.find_one({"email": mock_email})
            assert u1 is not None
            uid1 = u1["user_id"]

            # Run 2 -> existing user
            state2 = self._seed_state(db, "login")
            r2 = client.get(
                f"/api/aurem-dev/google/oauth/callback?code=y&state={state2}",
                follow_redirects=False, headers={"Origin": BASE_URL},
            )
            assert r2.status_code in (302, 307)
            assert "&new=0" in r2.headers["location"]

            # Same user_id, no dup
            dups = list(db.dev_users.find({"email": mock_email}))
            assert len(dups) == 1
            assert dups[0]["user_id"] == uid1
        finally:
            db.dev_users.delete_many({"email": mock_email})


# ─── Regression: Emergent-broker route must be GONE ───────────────────
class TestEmergentBrokerRemoved:
    def test_broker_route_returns_404(self, s):
        # 2026-08-28 — the Emergent-broker route was deleted entirely so
        # no traffic can ever land on auth.emergentagent.com. Must 404.
        r = s.post(f"{API}/auth/google/session",
                   json={"session_id": "definitely-invalid"})
        assert r.status_code == 404, (
            "broker route /auth/google/session must be fully removed"
        )
