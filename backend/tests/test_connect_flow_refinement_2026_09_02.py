"""
tests/test_connect_flow_refinement_2026_09_02.py

Connect-flow refinement (on top of the shipped Michael/Mike repo-picker
fix) — three design decisions, each with its own test:

  D1 — t_repo_picker_project_isolation: a project created via the
       repo-picker path (`/cto/projects/add` with `installation_id`)
       is only visible to the user who created it — proves each
       picked repo becomes its own ISOLATED project, never merged and
       never cross-readable.

  D2 — t_already_added_shows_open_existing_project: picking a repo
       that's already one of the user's projects returns 409
       `already_connected` with the existing project_id/name instead
       of creating a duplicate or a generic dead-end error (this is
       the fix for the Michael-loop — "I already connected, why is it
       saying connect again?").

  D3 — t_no_12s_blocking_interstitial: the success-bridge popup no
       longer holds a 12s close delay (replaced by a short standard
       "Connected" acknowledgement beat, ~1-1.5s) — the false-denied
       race fix (installation_active in useGitHubConnectStatus.js)
       already covers the real race; this popup no longer needs to
       block.

Reuses the same in-memory Mongo fake + TestClient pattern as
tests/test_github_app_project_add.py (Phase 3a coverage) — no mocks
inside production code, every branch exercises the real router.
"""
from __future__ import annotations

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

from services.github_app_config import set_runtime_github_app_config
from services import github_app as _ga

WEBHOOK_SECRET = "test-webhook-secret-1234"


# ═════════════════════════════════════════════════════════════════════
# Fake Mongo (same pattern as test_github_app_project_add.py)
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

    def _match(self, row, query):
        for k, v in query.items():
            if isinstance(v, dict) and "$regex" in v:
                import re as _re
                if not _re.match(v["$regex"], row.get(k) or "", _re.IGNORECASE):
                    return False
                continue
            if isinstance(v, dict):
                continue
            if row.get(k) != v:
                return False
        return True

    async def insert_one(self, doc):
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
        self.dev_users = _FakeCollection()


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
    from routers import cto_projects as router_mod
    from cto_services import db as _dbmod

    old_dbmod_get_db      = _dbmod.get_db
    old_dbmod_require_db  = _dbmod.require_db
    old_get_db            = router_mod.get_db
    old_require_db        = router_mod.require_db
    old_current_dev       = router_mod.current_dev
    old_run_project_indexing = router_mod._run_project_indexing

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

    async def _noop_indexing(**kw):
        return None
    router_mod._run_project_indexing = _noop_indexing

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    yield TestClient(app)

    _dbmod.get_db = old_dbmod_get_db
    _dbmod.require_db = old_dbmod_require_db
    router_mod.get_db = old_get_db
    router_mod.require_db = old_require_db
    router_mod.current_dev = old_current_dev
    router_mod._run_project_indexing = old_run_project_indexing


def _make_mock_client(handler):
    _RealAsyncClient = httpx.AsyncClient

    def factory(*a, **k):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))
    return factory


def _install_ok_handler(owner, repo):
    def handler(request):
        path = request.url.path
        if path.startswith("/app/installations/") and path.endswith("/access_tokens"):
            return httpx.Response(201, json={
                "token": "ghs_x",
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=60)
                               ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
        if path == f"/repos/{owner}/{repo}":
            return httpx.Response(200, json={
                "id": 1, "full_name": f"{owner}/{repo}", "default_branch": "main",
            })
        return httpx.Response(404, json={"message": "not mocked"})
    return handler


# ═════════════════════════════════════════════════════════════════════
# D2 — t_already_added_shows_open_existing_project
# ═════════════════════════════════════════════════════════════════════

def test_t_already_added_shows_open_existing_project(client, fake_db):
    fake_db.github_installations.rows.append({
        "installation_id": 157944565, "user_id": "michael",
        "active": True, "github_login": "mpelletier0691-byte",
    })
    # Michael already has a project for this exact repo.
    fake_db.cto_projects.rows.append({
        "project_id": "p_existing1", "user_id": "michael",
        "name": "forseti", "github_owner": "mpelletier0691-byte",
        "github_repo": "forseti",
    })

    with patch.object(httpx, "AsyncClient",
                       _make_mock_client(_install_ok_handler("mpelletier0691-byte", "forseti"))):
        r = client.post(
            "/api/aurem-dev/cto/projects/add",
            headers={"Authorization": "Bearer michael"},
            json={
                "name": "forseti",
                "github_url": "https://github.com/mpelletier0691-byte/forseti",
                "installation_id": 157944565,
            },
        )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "already_connected"
    assert detail["project_id"] == "p_existing1"
    assert detail["project_name"] == "forseti"
    assert "already your project" in detail["message"]
    # No duplicate project was created.
    assert len(fake_db.cto_projects.rows) == 1


def test_already_added_check_is_case_insensitive(client, fake_db):
    fake_db.github_installations.rows.append({
        "installation_id": 1, "user_id": "u1", "active": True, "github_login": "octo",
    })
    fake_db.cto_projects.rows.append({
        "project_id": "p1", "user_id": "u1",
        "name": "Mine", "github_owner": "Octo", "github_repo": "Mine",
    })
    with patch.object(httpx, "AsyncClient", _make_mock_client(_install_ok_handler("octo", "mine"))):
        r = client.post(
            "/api/aurem-dev/cto/projects/add",
            headers={"Authorization": "Bearer u1"},
            json={
                "name": "mine",
                "github_url": "https://github.com/octo/mine",
                "installation_id": 1,
            },
        )
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "already_connected"


def test_different_repo_not_flagged_as_already_connected(configured, client, fake_db):
    """Regression guard — an unrelated existing project must NOT
    false-positive the duplicate check."""
    fake_db.github_installations.rows.append({
        "installation_id": 2, "user_id": "u2", "active": True, "github_login": "octo",
    })
    fake_db.cto_projects.rows.append({
        "project_id": "p2", "user_id": "u2",
        "name": "other", "github_owner": "octo", "github_repo": "other-repo",
    })
    with patch.object(httpx, "AsyncClient", _make_mock_client(_install_ok_handler("octo", "new-repo"))):
        r = client.post(
            "/api/aurem-dev/cto/projects/add",
            headers={"Authorization": "Bearer u2"},
            json={
                "name": "new-repo",
                "github_url": "https://github.com/octo/new-repo",
                "installation_id": 2,
            },
        )
    assert r.status_code == 200, r.text
    assert len(fake_db.cto_projects.rows) == 2


# ═════════════════════════════════════════════════════════════════════
# D1 — t_repo_picker_project_isolation
# ═════════════════════════════════════════════════════════════════════

def test_t_repo_picker_project_isolation(configured, client, fake_db):
    """Each repo picked from the picker becomes its OWN isolated
    project — Michael-class users must never see or merge into
    another user's project."""
    fake_db.github_installations.rows.append({
        "installation_id": 157944565, "user_id": "michael",
        "active": True, "github_login": "mpelletier0691-byte",
    })

    with patch.object(httpx, "AsyncClient",
                       _make_mock_client(_install_ok_handler("mpelletier0691-byte", "forseti"))):
        r = client.post(
            "/api/aurem-dev/cto/projects/add",
            headers={"Authorization": "Bearer michael"},
            json={
                "name": "forseti",
                "github_url": "https://github.com/mpelletier0691-byte/forseti",
                "installation_id": 157944565,
            },
        )
    assert r.status_code == 200, r.text
    project_id = r.json()["project_id"]

    # Owner sees it.
    mine = client.get("/api/aurem-dev/cto/projects/list",
                       headers={"Authorization": "Bearer michael"})
    assert mine.status_code == 200
    assert any(p["project_id"] == project_id for p in mine.json()["projects"])

    # A DIFFERENT user (Mike, unrelated) must never see it.
    others = client.get("/api/aurem-dev/cto/projects/list",
                         headers={"Authorization": "Bearer mike"})
    assert others.status_code == 200
    assert not any(p["project_id"] == project_id for p in others.json()["projects"])


def test_second_repo_creates_a_separate_project_not_merged(configured, client, fake_db):
    """Picking a 2nd repo from the same installation creates a 2nd,
    SEPARATE project — never merged into the first."""
    fake_db.github_installations.rows.append({
        "installation_id": 5, "user_id": "michael",
        "active": True, "github_login": "mpelletier0691-byte",
    })
    with patch.object(httpx, "AsyncClient",
                       _make_mock_client(_install_ok_handler("mpelletier0691-byte", "forseti"))):
        r1 = client.post(
            "/api/aurem-dev/cto/projects/add",
            headers={"Authorization": "Bearer michael"},
            json={"name": "forseti",
                  "github_url": "https://github.com/mpelletier0691-byte/forseti",
                  "installation_id": 5},
        )
    with patch.object(httpx, "AsyncClient",
                       _make_mock_client(_install_ok_handler("mpelletier0691-byte", "BrokkrForge-"))):
        r2 = client.post(
            "/api/aurem-dev/cto/projects/add",
            headers={"Authorization": "Bearer michael"},
            json={"name": "BrokkrForge-",
                  "github_url": "https://github.com/mpelletier0691-byte/BrokkrForge-",
                  "installation_id": 5},
        )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["project_id"] != r2.json()["project_id"]
    assert len(fake_db.cto_projects.rows) == 2


# ═════════════════════════════════════════════════════════════════════
# D3 — t_no_12s_blocking_interstitial
# ═════════════════════════════════════════════════════════════════════

def test_t_no_12s_blocking_interstitial():
    """The success-bridge popup no longer blocks for 12s before
    closing — the false-denied race is now covered by
    `installation_active` in useGitHubConnectStatus.js, so this popup
    just needs a short, standard acknowledgement beat (GitHub/Linear/
    Notion/Slack pattern), not a multi-second hold."""
    from routers.github_app import _BRIDGE_HTML
    assert "window.close()" in _BRIDGE_HTML
    marker = "try { window.close(); } catch (e) {} }, "
    idx = _BRIDGE_HTML.index(marker) + len(marker)
    delay_str = _BRIDGE_HTML[idx:idx + 10].split(")")[0]
    delay_ms = int(delay_str)
    assert delay_ms < 2_000, (
        f"bridge still blocks the success popup for {delay_ms}ms — "
        f"should be a short standard beat, not a multi-second hold"
    )
    assert delay_ms >= 500, "beat should still be perceptible, not an instant vanish"
