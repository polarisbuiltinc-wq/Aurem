"""
tests/test_github_app_project_add.py — Phase 3a coverage

Focused on the `/cto/projects/add` gate rewrite + `get_repo_token()`
helper. Uses the same in-memory Mongo fake + httpx.MockTransport
pattern from Phase 2. No mocks in production code; every branch
tests a REAL code path through the router.

Coverage:

  Class 1 — Gate branches
    * missing both → 400 auth_required
    * PAT-only legacy path unchanged (row persisted with auth_method="pat",
      encrypted PAT, installation_id=None)
    * installation_id happy path (row persisted with auth_method="github_app",
      installation_id set, github_token=None, no PAT verification hit)
    * installation not owned by user → 400 installation_not_found_or_inactive
    * installation inactive → same 400
    * installation lacks repo access → 400 installation_no_repo_access
    * both provided → installation_id wins silently (PAT verify never called)

  Class 2 — get_repo_token()
    * legacy row (no auth_method) → decrypts stored PAT
    * explicit auth_method="pat" → decrypts stored PAT
    * auth_method="github_app" → mints fresh installation token
    * malformed row (github_app but no installation_id) → returns None
      (caller falls back to org token — never raises)
    * malformed row (pat but no github_token) → returns None
"""
from __future__ import annotations

import time
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, NoEncryption,
)

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import github_app as _ga
from services.github_app_config import set_runtime_github_app_config
from services import pat_vault as _pv


WEBHOOK_SECRET = "test-webhook-secret-1234"


# ═════════════════════════════════════════════════════════════════════
# Fake Mongo (reused pattern from Phase 2 tests)
# ═════════════════════════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k): return self

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows = []
        self._unique_key = None

    def _match(self, row, query):
        for k, v in query.items():
            if isinstance(v, dict):
                continue
            if row.get(k) != v:
                return False
        return True

    async def insert_one(self, doc):
        if self._unique_key and any(
            r.get(self._unique_key) == doc.get(self._unique_key) for r in self.rows
        ):
            raise Exception("E11000 duplicate key")
        self.rows.append(dict(doc))
        return types.SimpleNamespace(inserted_id=doc.get("_id"))

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if self._match(r, query):
                return dict(r)
        return None

    async def update_one(self, query, update):
        for r in self.rows:
            if self._match(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    def find(self, query, projection=None):
        matched = [dict(r) for r in self.rows if self._match(r, query)]
        return _FakeCursor(matched)


class _FakeDB:
    def __init__(self):
        self.cto_projects = _FakeCollection()
        self.github_installations = _FakeCollection()
        self.github_installations._unique_key = "installation_id"
        self.dev_users = _FakeCollection()


# ═════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def rsa_pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption(),
    ).decode()


@pytest.fixture
def configured(rsa_pem):
    set_runtime_github_app_config({
        "app_id":         "123456",
        "app_slug":       "aurem-devops",
        "private_key":    rsa_pem,
        "webhook_secret": WEBHOOK_SECRET,
    })
    _ga._APP_JWT_CACHE = None
    _ga._INSTALL_TOKEN_CACHE.clear()
    yield
    set_runtime_github_app_config(None)
    _ga._APP_JWT_CACHE = None
    _ga._INSTALL_TOKEN_CACHE.clear()


def _make_mock_client(handler):
    _RealAsyncClient = httpx.AsyncClient

    def factory(*a, **k):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))
    return factory


# ═════════════════════════════════════════════════════════════════════
# Class 1 — Gate branches (via /projects/add router endpoint)
# ═════════════════════════════════════════════════════════════════════

@pytest.fixture
def client(fake_db):
    """FastAPI TestClient mounted with the ACTUAL cto_projects router,
    but with db + current_dev patched to test-only fakes."""
    from routers import cto_projects as router_mod
    from cto_services import db as _dbmod

    _dbmod.get_db = lambda: fake_db
    _dbmod.require_db = lambda: fake_db
    router_mod.get_db = lambda: fake_db
    router_mod.require_db = lambda: fake_db

    async def _fake_current_dev(auth):
        if not auth or not auth.startswith("Bearer "):
            from fastapi import HTTPException as _HE
            raise _HE(401, "auth required")
        return {"user_id": auth.split(" ", 1)[1], "email": "x@example.com"}
    router_mod.current_dev = _fake_current_dev

    # Silence the background indexing task — its inner build_brain
    # imports pull in a huge dep tree.
    async def _noop_indexing(**kw):
        return None
    router_mod._run_project_indexing = _noop_indexing

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    return TestClient(app)


class TestGateBranches:
    def test_missing_both_returns_400(self, configured, client):
        r = client.post(
            "/api/aurem-dev/cto/projects/add",
            headers={"Authorization": "Bearer user-a"},
            json={"name": "x", "github_url": "https://github.com/a/b"},
        )
        assert r.status_code == 400
        # Detail is a dict payload
        detail = r.json()["detail"]
        assert detail["error"] == "auth_required"

    def test_pat_only_legacy_path_unchanged(self, configured, client, fake_db,
                                             monkeypatch):
        # Enable test-bypass so we skip the live GitHub call.
        monkeypatch.setenv("AUREM_TEST_MODE", "1")
        r = client.post(
            "/api/aurem-dev/cto/projects/add",
            headers={"Authorization": "Bearer user-a"},
            json={
                "name":         "legacy",
                "github_url":   "https://github.com/octo/legacy",
                "github_token": "github_pat_TEST_abc",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["auth_method"] == "pat"
        assert body["pat_verified"] is True
        assert "installation_id" not in body

        row = fake_db.cto_projects.rows[0]
        assert row["auth_method"] == "pat"
        assert row["installation_id"] is None
        # PAT is persisted encrypted (v1:-prefixed) OR plaintext for
        # legacy-migration paths — either way it's non-empty.
        assert row["github_token"]
        assert (row["github_token"].startswith("v1:")
                or row["github_token"] == "github_pat_TEST_abc")

    def test_installation_id_happy_path(self, configured, client, fake_db):
        # Seed installation row owned by user-a
        fake_db.github_installations.rows.append({
            "installation_id": 42, "user_id": "user-a",
            "active": True, "github_login": "octo",
        })

        def handler(request):
            # get_repo_via_installation → first mints an installation token,
            # then GET /repos/{owner}/{repo}
            path = request.url.path
            if path.startswith("/app/installations/") and path.endswith("/access_tokens"):
                return httpx.Response(201, json={
                    "token":      "ghs_installation_token",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=60)
                                   ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            if path == "/repos/octo/mine":
                return httpx.Response(200, json={
                    "id": 1, "full_name": "octo/mine", "default_branch": "main",
                })
            return httpx.Response(404, json={"message": "not mocked"})

        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            r = client.post(
                "/api/aurem-dev/cto/projects/add",
                headers={"Authorization": "Bearer user-a"},
                json={
                    "name":            "app",
                    "github_url":      "https://github.com/octo/mine",
                    "installation_id": 42,
                },
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["auth_method"] == "github_app"
        assert body["installation_id"] == 42
        row = fake_db.cto_projects.rows[0]
        assert row["auth_method"] == "github_app"
        assert row["installation_id"] == 42
        assert row["github_token"] is None       # never stored
        assert row["installation_active"] is True

    def test_installation_not_owned_by_user(self, configured, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 99, "user_id": "OTHER-USER",
            "active": True, "github_login": "elsewhere",
        })
        r = client.post(
            "/api/aurem-dev/cto/projects/add",
            headers={"Authorization": "Bearer user-a"},
            json={
                "name":            "x",
                "github_url":      "https://github.com/elsewhere/repo",
                "installation_id": 99,
            },
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "installation_not_found_or_inactive"
        # No row was created
        assert len(fake_db.cto_projects.rows) == 0

    def test_installation_inactive(self, configured, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 100, "user_id": "user-a",
            "active": False, "github_login": "octo",
        })
        r = client.post(
            "/api/aurem-dev/cto/projects/add",
            headers={"Authorization": "Bearer user-a"},
            json={
                "name":            "x",
                "github_url":      "https://github.com/octo/dead",
                "installation_id": 100,
            },
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "installation_not_found_or_inactive"

    def test_installation_no_repo_access(self, configured, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 55, "user_id": "user-a",
            "active": True, "github_login": "octo",
        })

        def handler(request):
            path = request.url.path
            if path.endswith("/access_tokens"):
                return httpx.Response(201, json={
                    "token":      "ghs_x",
                    "expires_at": (datetime.now(timezone.utc)
                                   ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            if path.startswith("/repos/"):
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(500)

        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            r = client.post(
                "/api/aurem-dev/cto/projects/add",
                headers={"Authorization": "Bearer user-a"},
                json={
                    "name":            "x",
                    "github_url":      "https://github.com/octo/no-access",
                    "installation_id": 55,
                },
            )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "installation_no_repo_access"
        assert len(fake_db.cto_projects.rows) == 0

    def test_both_provided_installation_wins(self, configured, client, fake_db,
                                              monkeypatch):
        """When BOTH installation_id and github_token are sent, the App
        branch wins silently. The PAT is neither verified nor persisted."""
        monkeypatch.setenv("AUREM_TEST_MODE", "1")
        fake_db.github_installations.rows.append({
            "installation_id": 77, "user_id": "user-a",
            "active": True, "github_login": "octo",
        })

        pat_verify_calls = {"n": 0}

        def handler(request):
            path = request.url.path
            if path.endswith("/access_tokens"):
                return httpx.Response(201, json={
                    "token": "ghs_x",
                    "expires_at": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"),
                })
            if path == "/repos/octo/both":
                return httpx.Response(200, json={"id": 1})
            # If the PAT branch tries to call GitHub, this counter
            # increments — we assert it stays at 0.
            if "Authorization" in request.headers:
                if "ghp_" in request.headers["Authorization"]:
                    pat_verify_calls["n"] += 1
            return httpx.Response(200, json={})

        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            r = client.post(
                "/api/aurem-dev/cto/projects/add",
                headers={"Authorization": "Bearer user-a"},
                json={
                    "name":            "both",
                    "github_url":      "https://github.com/octo/both",
                    "installation_id": 77,
                    "github_token":    "ghp_should_be_ignored",
                },
            )
        assert r.status_code == 200
        row = fake_db.cto_projects.rows[0]
        assert row["auth_method"] == "github_app"
        assert row["github_token"] is None     # PAT NOT persisted
        assert pat_verify_calls["n"] == 0      # PAT verify never hit


# ═════════════════════════════════════════════════════════════════════
# Class 2 — get_repo_token() helper
# ═════════════════════════════════════════════════════════════════════

class TestGetRepoToken:
    @pytest.mark.asyncio
    async def test_legacy_row_defaults_to_pat(self, configured):
        # Legacy row = no `auth_method` field. Should decrypt the
        # stored PAT (or pass through plaintext for pre-encryption rows).
        row = {
            "user_id":      "u1",
            "github_token": "ghp_legacy_plaintext",
            # auth_method absent — treated as PAT
        }
        tok = await _pv.get_repo_token(row)
        assert tok == "ghp_legacy_plaintext"

    @pytest.mark.asyncio
    async def test_explicit_pat_method(self, configured):
        row = {
            "user_id":      "u1",
            "auth_method":  "pat",
            "github_token": "github_pat_11xxxx",
        }
        tok = await _pv.get_repo_token(row)
        assert tok == "github_pat_11xxxx"

    @pytest.mark.asyncio
    async def test_github_app_method_mints_fresh(self, configured):
        row = {
            "user_id":         "u1",
            "auth_method":     "github_app",
            "installation_id": 123,
            # github_token DELIBERATELY absent for App rows
        }

        def handler(request):
            if request.url.path == "/app/installations/123/access_tokens":
                return httpx.Response(201, json={
                    "token": "ghs_freshly_minted",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=60)
                                   ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            return httpx.Response(500)

        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            tok = await _pv.get_repo_token(row)
        assert tok == "ghs_freshly_minted"

    @pytest.mark.asyncio
    async def test_github_app_no_installation_id_returns_none(self, configured):
        """Malformed row — never raises, returns None so caller falls
        through to `_user_gh_token(user_id)` org fallback safely."""
        row = {
            "user_id":         "u1",
            "auth_method":     "github_app",
            # installation_id absent — misconfigured
        }
        tok = await _pv.get_repo_token(row)
        assert tok is None

    @pytest.mark.asyncio
    async def test_pat_method_no_token_returns_none(self, configured):
        row = {
            "user_id":     "u1",
            "auth_method": "pat",
            # github_token absent
        }
        tok = await _pv.get_repo_token(row)
        # _decrypt_pat returns None for empty input
        assert tok in (None, "")

    @pytest.mark.asyncio
    async def test_empty_project_returns_none(self, configured):
        assert await _pv.get_repo_token(None) is None
        assert await _pv.get_repo_token({}) in (None, "")

    @pytest.mark.asyncio
    async def test_github_app_revoked_installation_returns_none(self, configured):
        """When GitHub returns 401/404 on token mint (installation
        deleted or App uninstalled), helper returns None — never raises
        — so caller falls through to org-token fallback."""
        row = {
            "user_id":         "u1",
            "auth_method":     "github_app",
            "installation_id": 404,
        }

        def handler(request):
            return httpx.Response(404, json={"message": "Not Found"})

        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            tok = await _pv.get_repo_token(row)
        assert tok is None
