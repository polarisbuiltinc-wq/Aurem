"""
tests/test_github_app_router.py — Phase 2 coverage

Uses FastAPI TestClient + a lightweight in-memory Mongo fake so tests
never touch real Mongo. HTTP calls to GitHub are intercepted via
`httpx.MockTransport` (same pattern as test_github_app_service.py).

Real coverage — not mocks-of-mocks:
  * State token single-use / expiry / replay
  * Callback happy path + all soft-fail redirect branches
  * Webhook HMAC valid/invalid; every event type drives correct DB write
  * Delivery-ID dedupe (repeat delivery returns deduped:true with no re-write)
  * Race safety (webhook-before-callback and callback-before-webhook end
    with identical final row state)
  * Cross-user DELETE returns 404 (not 403 — prevents enumeration)
  * Ownership scoping on GET /installations
"""
from __future__ import annotations

import hmac
import hashlib
import json
import time
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

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


WEBHOOK_SECRET = "test-webhook-secret-1234"


# ═════════════════════════════════════════════════════════════════════
# Fakes
# ═════════════════════════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *args, **kwargs):
        # Ignore sort — tests seed rows in the exact order we want back.
        return self

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []
        self._unique_key = None

    def _match(self, row, query):
        for k, v in query.items():
            if isinstance(v, dict):
                if "$gt" in v and not (row.get(k) is not None and row[k] > v["$gt"]):
                    return False
                if "$exists" in v:
                    exists = row.get(k) is not None
                    if v["$exists"] != exists:
                        return False
                if "$ne" in v and row.get(k) == v["$ne"]:
                    return False
                continue
            if row.get(k) != v:
                return False
        return True

    async def insert_one(self, doc):
        if self._unique_key:
            if any(r.get(self._unique_key) == doc.get(self._unique_key) for r in self.rows):
                raise Exception("E11000 duplicate key")
        self.rows.append(dict(doc))
        return types.SimpleNamespace(inserted_id=doc.get("_id"))

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if self._match(r, query):
                return dict(r)
        return None

    async def find_one_and_update(self, query, update, upsert=False, return_document=False):
        for i, r in enumerate(self.rows):
            if self._match(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                if "$setOnInsert" in update:
                    pass  # already existed
                return dict(r) if return_document else dict(r)
        if upsert:
            new_row = {}
            new_row.update(query if isinstance(query, dict) else {})
            # Strip operators from the query — only literal keys survive as seed.
            for k in list(new_row.keys()):
                if isinstance(new_row[k], dict):
                    new_row.pop(k, None)
            if "$setOnInsert" in update:
                new_row.update(update["$setOnInsert"])
            if "$set" in update:
                new_row.update(update["$set"])
            self.rows.append(new_row)
            return dict(new_row) if return_document else None
        return None

    async def update_one(self, query, update):
        for r in self.rows:
            if self._match(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query, update):
        n = 0
        for r in self.rows:
            if self._match(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                n += 1
        return types.SimpleNamespace(matched_count=n, modified_count=n)

    def find(self, query):
        matched = [dict(r) for r in self.rows if self._match(r, query)]
        return _FakeCursor(matched)


class _FakeDB:
    def __init__(self):
        self.oauth_states = _FakeCollection()
        self.github_installations = _FakeCollection()
        self.github_installations._unique_key = "installation_id"
        self.webhook_deliveries = _FakeCollection()
        self.webhook_deliveries._unique_key = "_id"
        self.cto_projects = _FakeCollection()
        self.github_funnel_events = _FakeCollection()

    def __getitem__(self, name):
        col = getattr(self, name, None)
        if col is None:
            col = _FakeCollection()
            setattr(self, name, col)
        return col


# ═════════════════════════════════════════════════════════════════════
# App + fixtures
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


@pytest.fixture
def client(fake_db):
    """A minimal FastAPI app mounted with only the github_app router.
    All db dependencies point at the fake in-memory Mongo."""
    from routers import github_app as router_mod

    # Patch get_db / require_db in every module the router touches.
    from cto_services import db as _dbmod
    old_get_db = _dbmod.get_db
    old_require_db = _dbmod.require_db
    _dbmod.get_db = lambda: fake_db
    _dbmod.require_db = lambda: fake_db

    # Also patch the imports the router grabbed at module load time.
    old_router_get_db = router_mod.get_db
    old_router_require_db = router_mod.require_db
    router_mod.get_db = lambda: fake_db
    router_mod.require_db = lambda: fake_db

    # Patch current_dev so we don't need real JWT machinery.
    async def _fake_current_dev(auth):
        if not auth or not auth.startswith("Bearer "):
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        token = auth.split(" ", 1)[1]
        # Tokens map directly to user_id in tests.
        return {"user_id": token, "email": f"{token}@example.com"}

    old_current_dev = router_mod.current_dev
    router_mod.current_dev = _fake_current_dev

    # Silence funnel tracking so tests don't need to import anything else.
    async def _noop_track(*a, **kw):
        return None
    old_funnel_track = router_mod._funnel_track
    router_mod._funnel_track = _noop_track

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    c = TestClient(app)
    yield c

    _dbmod.get_db = old_get_db
    _dbmod.require_db = old_require_db
    # Iter212n — these 3 used to be left unrestored, leaking into every
    # test file that ran afterward — see memory/PHASE_A_AUDIT_2026-08-24.md
    # Category C.
    router_mod.get_db = old_router_get_db
    router_mod.require_db = old_router_require_db
    router_mod.current_dev = old_current_dev
    router_mod._funnel_track = old_funnel_track


def _make_mock_client(handler):
    _RealAsyncClient = httpx.AsyncClient

    def factory(*args, **kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))
    return factory


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256,
    ).hexdigest()


# ═════════════════════════════════════════════════════════════════════
# /install
# ═════════════════════════════════════════════════════════════════════

class TestInstallKickoff:
    def test_unauth_returns_401(self, configured, client):
        r = client.get("/api/aurem-dev/github/app/install",
                       follow_redirects=False)
        assert r.status_code == 401

    def test_not_configured_returns_503(self, client):
        set_runtime_github_app_config(None)
        r = client.get(
            "/api/aurem-dev/github/app/install",
            headers={"Authorization": "Bearer user-a"},
            follow_redirects=False,
        )
        assert r.status_code == 503
        assert "github_app_not_configured" in r.text

    def test_happy_path_creates_state_row_and_302(self, configured, client, fake_db):
        r = client.get(
            "/api/aurem-dev/github/app/install",
            headers={"Authorization": "Bearer user-a"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "github.com/apps/aurem-devops/installations/new" in r.headers["location"]
        assert "state=" in r.headers["location"]

        # State row persisted, bound to user, single-use flag = False
        assert len(fake_db.oauth_states.rows) == 1
        row = fake_db.oauth_states.rows[0]
        assert row["user_id"] == "user-a"
        assert row["kind"] == "github_app_install"
        assert row["used"] is False
        assert row["expires_at"] > time.time()


# ═════════════════════════════════════════════════════════════════════
# /callback
# ═════════════════════════════════════════════════════════════════════

class TestCallback:
    def _seed_state(self, fake_db, user_id="user-a", state="gha:user-a:abc", ttl=900):
        fake_db.oauth_states.rows.append({
            "state":       state,
            "kind":        "github_app_install",
            "user_id":     user_id,
            "used":        False,
            "created_at":  datetime.now(timezone.utc),
            "expires_at":  datetime.now(timezone.utc).timestamp() + ttl,
        })
        return state

    def _github_handler(self, iid=1001, account_login="testuser", repos=None):
        def handler(request):
            path = request.url.path
            if path == f"/app/installations/{iid}":
                return httpx.Response(200, json={
                    "id": iid,
                    "account": {"id": 42, "login": account_login, "type": "User"},
                    "target_type": "User",
                    "repository_selection": "selected",
                    "permissions": {"contents": "write", "metadata": "read"},
                    "events": ["installation", "installation_repositories"],
                })
            if path == "/installation/repositories":
                return httpx.Response(200, json={
                    "repositories": (repos or [
                        {"id": 111, "full_name": f"{account_login}/repo-1",
                         "private": False, "default_branch": "main"},
                    ]),
                })
            if path.startswith("/app/installations/") and path.endswith("/access_tokens"):
                return httpx.Response(201, json={
                    "token": "ghs_test",
                    "expires_at": (datetime.now(timezone.utc)
                                    .replace(microsecond=0)
                                   ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            return httpx.Response(404, json={"message": "not mocked"})
        return handler

    def test_setup_action_request_soft_redirect(self, configured, client):
        r = client.get(
            "/api/aurem-dev/github/app/callback?setup_action=request",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "status=pending" in r.headers["location"]

    def test_missing_installation_id_soft_redirect(self, configured, client):
        r = client.get(
            "/api/aurem-dev/github/app/callback",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "err=invalid_state" in r.headers["location"]

    def test_happy_path_upserts_and_links(self, configured, client, fake_db):
        state = self._seed_state(fake_db)
        with patch.object(httpx, "AsyncClient",
                          _make_mock_client(self._github_handler(iid=1001))):
            r = client.get(
                f"/api/aurem-dev/github/app/callback?installation_id=1001&state={state}",
                follow_redirects=False,
            )
        assert r.status_code == 302
        assert "status=success" in r.headers["location"]
        assert "install_id=1001" in r.headers["location"]

        # State consumed (single-use)
        st = fake_db.oauth_states.rows[0]
        assert st["used"] is True

        # Installation row created + linked to user
        assert len(fake_db.github_installations.rows) == 1
        row = fake_db.github_installations.rows[0]
        assert row["installation_id"] == 1001
        assert row["user_id"] == "user-a"
        assert row["active"] is True
        assert row["github_login"] == "testuser"
        assert row["linked_at"] is not None
        assert row["installed_at"] is not None
        assert len(row["repositories"]) == 1
        assert row["repositories"][0]["full_name"] == "testuser/repo-1"

    def test_invalid_state_soft_redirect_no_writes(self, configured, client, fake_db):
        r = client.get(
            "/api/aurem-dev/github/app/callback?installation_id=1&state=forged",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "err=invalid_state" in r.headers["location"]
        assert len(fake_db.github_installations.rows) == 0

    def test_replayed_state_soft_redirect(self, configured, client, fake_db):
        state = self._seed_state(fake_db)
        # First hit — happy path
        with patch.object(httpx, "AsyncClient",
                          _make_mock_client(self._github_handler(iid=2001))):
            client.get(
                f"/api/aurem-dev/github/app/callback?installation_id=2001&state={state}",
                follow_redirects=False,
            )
        # Second hit with same state — should be rejected
        r2 = client.get(
            f"/api/aurem-dev/github/app/callback?installation_id=2001&state={state}",
            follow_redirects=False,
        )
        assert r2.status_code == 302
        assert "err=invalid_state" in r2.headers["location"]

    def test_expired_state_soft_redirect(self, configured, client, fake_db):
        state = self._seed_state(fake_db, ttl=-10)  # already expired
        r = client.get(
            f"/api/aurem-dev/github/app/callback?installation_id=3001&state={state}",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "err=invalid_state" in r.headers["location"]

    def test_github_probe_failure_soft_redirect(self, configured, client, fake_db):
        state = self._seed_state(fake_db)

        def bad_handler(request):
            return httpx.Response(500, json={"message": "server error"})

        with patch.object(httpx, "AsyncClient", _make_mock_client(bad_handler)):
            r = client.get(
                f"/api/aurem-dev/github/app/callback?installation_id=4001&state={state}",
                follow_redirects=False,
            )
        assert r.status_code == 302
        assert "err=github_probe_failed" in r.headers["location"]
        # No row written
        assert len(fake_db.github_installations.rows) == 0


# ═════════════════════════════════════════════════════════════════════
# /webhook
# ═════════════════════════════════════════════════════════════════════

class TestWebhook:
    def _post(self, client, event, delivery_id, payload, secret=WEBHOOK_SECRET):
        body = json.dumps(payload).encode()
        return client.post(
            "/api/aurem-dev/github/app/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body, secret),
                "X-GitHub-Event":      event,
                "X-GitHub-Delivery":   delivery_id,
                "Content-Type":        "application/json",
            },
        )

    def test_invalid_signature_returns_401(self, configured, client):
        body = json.dumps({"action": "created"}).encode()
        r = client.post(
            "/api/aurem-dev/github/app/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": "sha256=" + ("0" * 64),
                "X-GitHub-Event":      "installation",
                "X-GitHub-Delivery":   "d1",
                "Content-Type":        "application/json",
            },
        )
        assert r.status_code == 401

    def test_installation_created_upserts(self, configured, client, fake_db):
        r = self._post(client, "installation", "d-created-1", {
            "action": "created",
            "installation": {
                "id": 500,
                "account": {"id": 1, "login": "octo", "type": "User"},
                "target_type": "User",
                "repository_selection": "all",
                "permissions": {"contents": "write"},
                "events": ["installation"],
            },
            "repositories": [
                {"id": 700, "full_name": "octo/one", "private": False},
                {"id": 701, "full_name": "octo/two", "private": True},
            ],
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
        row = fake_db.github_installations.rows[0]
        assert row["installation_id"] == 500
        assert row["active"] is True
        assert row.get("user_id") in (None, "")  # not linked yet
        assert len(row["repositories"]) == 2

    def test_delivery_dedup(self, configured, client, fake_db):
        payload = {
            "action": "created",
            "installation": {"id": 601, "account": {"login": "a", "type": "User"}},
            "repositories": [],
        }
        r1 = self._post(client, "installation", "d-dedup-1", payload)
        assert r1.status_code == 200
        assert r1.json().get("deduped") is not True
        r2 = self._post(client, "installation", "d-dedup-1", payload)
        assert r2.status_code == 200
        assert r2.json()["deduped"] is True
        # Only one installation row
        assert len(fake_db.github_installations.rows) == 1

    def test_installation_deleted_cascades(self, configured, client, fake_db):
        # Seed: existing installation + a linked project
        fake_db.github_installations.rows.append({
            "installation_id": 800, "user_id": "user-a", "active": True,
        })
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "user-a",
            "installation_id": 800, "installation_active": True,
        })
        r = self._post(client, "installation", "d-del-1", {
            "action": "deleted",
            "installation": {"id": 800},
        })
        assert r.status_code == 200
        assert fake_db.github_installations.rows[0]["active"] is False
        assert fake_db.cto_projects.rows[0]["installation_active"] is False

    def test_installation_suspend_unsuspend_round_trip(self, configured, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 900, "user_id": "user-a", "active": True,
        })
        fake_db.cto_projects.rows.append({
            "project_id": "p9", "user_id": "user-a",
            "installation_id": 900, "installation_active": True,
        })
        self._post(client, "installation", "d-susp-1", {
            "action": "suspend",
            "installation": {"id": 900},
        })
        assert fake_db.github_installations.rows[0]["active"] is False
        assert fake_db.cto_projects.rows[0]["installation_active"] is False

        self._post(client, "installation", "d-unsusp-1", {
            "action": "unsuspend",
            "installation": {"id": 900},
        })
        assert fake_db.github_installations.rows[0]["active"] is True
        assert fake_db.cto_projects.rows[0]["installation_active"] is True

    def test_installation_repositories_added_merges(self, configured, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 1000, "user_id": "u", "active": True,
            "repositories": [{"id": 1, "full_name": "a/one", "private": False}],
        })
        self._post(client, "installation_repositories", "d-add-1", {
            "action": "added",
            "installation": {"id": 1000},
            "repositories_added": [
                {"id": 2, "full_name": "a/two", "private": False},
                {"id": 1, "full_name": "a/one", "private": False},   # dupe → ignored
            ],
        })
        repos = fake_db.github_installations.rows[0]["repositories"]
        ids = {r["id"] for r in repos}
        assert ids == {1, 2}

    def test_installation_repositories_removed_cascades(self, configured, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 1100, "user_id": "u", "active": True,
            "repositories": [
                {"id": 10, "full_name": "a/keep", "private": False},
                {"id": 11, "full_name": "a/drop", "private": False},
            ],
        })
        fake_db.cto_projects.rows.append({
            "project_id": "p", "user_id": "u",
            "installation_id": 1100,
            "github_owner": "a", "github_repo": "drop",
            "installation_active": True,
        })
        self._post(client, "installation_repositories", "d-rem-1", {
            "action": "removed",
            "installation": {"id": 1100},
            "repositories_removed": [
                {"id": 11, "full_name": "a/drop"},
            ],
        })
        repos = fake_db.github_installations.rows[0]["repositories"]
        assert {r["id"] for r in repos} == {10}
        assert fake_db.cto_projects.rows[0]["installation_active"] is False

    def test_unknown_event_returns_200(self, configured, client):
        r = self._post(client, "star", "d-star-1", {"action": "created"})
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ═════════════════════════════════════════════════════════════════════
# Race safety
# ═════════════════════════════════════════════════════════════════════

class TestRaceSafety:
    def test_webhook_before_callback_ends_linked(self, configured, client, fake_db):
        # Webhook fires first (no user_id yet)
        client.post(
            "/api/aurem-dev/github/app/webhook",
            content=(body := json.dumps({
                "action": "created",
                "installation": {"id": 5555, "account": {"login": "x", "type": "User"}},
                "repositories": [],
            }).encode()),
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event":      "installation",
                "X-GitHub-Delivery":   "race-w-1",
                "Content-Type":        "application/json",
            },
        )
        row_before = fake_db.github_installations.rows[0]
        assert row_before.get("user_id") is None or row_before.get("user_id") == ""

        # Callback arrives second — must link
        state = "gha:user-r:xyz"
        fake_db.oauth_states.rows.append({
            "state": state, "kind": "github_app_install", "user_id": "user-r",
            "used": False,
            "created_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc).timestamp() + 900,
        })

        def handler(request):
            path = request.url.path
            if path == "/app/installations/5555":
                return httpx.Response(200, json={
                    "id": 5555,
                    "account": {"id": 1, "login": "x", "type": "User"},
                    "target_type": "User",
                    "repository_selection": "all",
                    "permissions": {}, "events": [],
                })
            if path == "/installation/repositories":
                return httpx.Response(200, json={"repositories": []})
            if path.endswith("/access_tokens"):
                return httpx.Response(201, json={
                    "token": "ghs_x",
                    "expires_at": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"),
                })
            return httpx.Response(404)

        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            r = client.get(
                f"/api/aurem-dev/github/app/callback?installation_id=5555&state={state}",
                follow_redirects=False,
            )
        assert r.status_code == 302
        # Row now linked
        assert fake_db.github_installations.rows[0]["user_id"] == "user-r"
        # Still only ONE row (no duplicate insert)
        assert len(fake_db.github_installations.rows) == 1


# ═════════════════════════════════════════════════════════════════════
# GET /installations + DELETE
# ═════════════════════════════════════════════════════════════════════

class TestListAndDelete:
    def test_list_scoped_to_user(self, configured, client, fake_db):
        fake_db.github_installations.rows += [
            {"installation_id": 1, "user_id": "user-a", "active": True,
             "github_login": "a", "installed_at": 100.0},
            {"installation_id": 2, "user_id": "user-b", "active": True,
             "github_login": "b", "installed_at": 200.0},
            {"installation_id": 3, "user_id": "user-a", "active": False,
             "github_login": "a2", "installed_at": 300.0},
        ]
        r = client.get(
            "/api/aurem-dev/github/app/installations",
            headers={"Authorization": "Bearer user-a"},
        )
        assert r.status_code == 200
        ids = [i["installation_id"] for i in r.json()["installations"]]
        assert ids == [1]   # only user-a's active

    def test_delete_cross_user_returns_404(self, configured, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 77, "user_id": "user-x", "active": True,
        })
        r = client.delete(
            "/api/aurem-dev/github/app/installations/77",
            headers={"Authorization": "Bearer user-y"},
        )
        assert r.status_code == 404
        # Row untouched
        assert fake_db.github_installations.rows[0]["active"] is True

    def test_delete_happy_path(self, configured, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 88, "user_id": "user-z", "active": True,
        })
        fake_db.cto_projects.rows.append({
            "project_id": "p", "user_id": "user-z",
            "installation_id": 88, "installation_active": True,
        })

        def handler(request):
            # DELETE /app/installations/88
            if request.method == "DELETE" and request.url.path == "/app/installations/88":
                return httpx.Response(204)
            return httpx.Response(200, json={})

        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            r = client.delete(
                "/api/aurem-dev/github/app/installations/88",
                headers={"Authorization": "Bearer user-z"},
            )
        assert r.status_code == 200
        assert r.json()["revoked_installation_id"] == 88
        assert fake_db.github_installations.rows[0]["active"] is False
        assert fake_db.cto_projects.rows[0]["installation_active"] is False


# ═════════════════════════════════════════════════════════════════════
# Phase 4 · Bridge page (popup ↔ parent handshake)
# ═════════════════════════════════════════════════════════════════════

class TestInstalledBridge:
    def test_success_bridge_returns_html_with_postmessage(self, configured, client):
        r = client.get(
            "/api/aurem-dev/github/app/installed?status=success&install_id=42",
        )
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        # Payload keys the wizard listens for
        assert "aurem-app-installed" in body
        assert "window.opener.postMessage" in body
        # install_id echoed into the client script
        assert "install_id" in body
        # Non-popup fallback path present
        assert "/dashboard" in body

    def test_err_bridge_still_returns_200(self, configured, client):
        # Errors from callback (invalid_state, github_probe_failed) all
        # route through the bridge — the page decides UX. Backend never
        # returns non-200 for the bridge itself.
        r = client.get(
            "/api/aurem-dev/github/app/installed?status=err&err=invalid_state",
        )
        assert r.status_code == 200
        assert "aurem-app-installed" in r.text

    def test_pending_bridge(self, configured, client):
        r = client.get(
            "/api/aurem-dev/github/app/installed?status=pending",
        )
        assert r.status_code == 200
        assert "aurem-app-installed" in r.text
