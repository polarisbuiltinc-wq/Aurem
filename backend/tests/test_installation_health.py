"""
Tests for the new GitHub App installation-health + connection-status
suspended/deleted branching (2026-08-20).

Covers:
  - GET /github/app/installations/health (new endpoint)
  - GET /github/app/installations (regression — MUST still be active-only)
  - GET /cto/projects/connection-status (installation_suspended/deleted
    short-circuit for github_app auth projects)
"""
import os
import time
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"
USER_ID = "test_admin_001"

# Unique fixture IDs to avoid collisions with founder's prior test
FIX_IID_SUSPENDED = 999999042
FIX_IID_DELETED = 999999043
FIX_PROJ_SUSPENDED = "test_suspended_proj_042"
FIX_PROJ_DELETED = "test_deleted_proj_043"


@pytest.fixture(scope="module")
def db():
    c = MongoClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest.fixture(scope="module")
def token():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def fixtures(db):
    """Seed suspended + deleted installation + matching cto_projects."""
    now = time.time()
    db.github_installations.delete_many(
        {"installation_id": {"$in": [FIX_IID_SUSPENDED, FIX_IID_DELETED]}}
    )
    db.cto_projects.delete_many(
        {"project_id": {"$in": [FIX_PROJ_SUSPENDED, FIX_PROJ_DELETED]}}
    )
    db.github_installations.insert_many([
        {
            "installation_id": FIX_IID_SUSPENDED,
            "user_id": USER_ID,
            "github_login": "fake-suspended-org-042",
            "active": False,
            "suspended_at": now,
            "deleted_at": None,
            "updated_at": now,
            "installed_at": now,
            "repositories": [{"id": 1, "full_name": "fake-suspended-org-042/demo-repo"}],
        },
        {
            "installation_id": FIX_IID_DELETED,
            "user_id": USER_ID,
            "github_login": "fake-deleted-org-043",
            "active": False,
            "suspended_at": None,
            "deleted_at": now,
            "updated_at": now,
            "installed_at": now,
            "repositories": [],
        },
    ])
    db.cto_projects.insert_many([
        {
            "project_id": FIX_PROJ_SUSPENDED,
            "user_id": USER_ID,
            "name": "Suspended Test Project",
            "github_owner": "fake-suspended-org-042",
            "github_repo": "demo-repo",
            "branch": "main",
            "auth_method": "github_app",
            "installation_id": FIX_IID_SUSPENDED,
            "created_at": now,
        },
        {
            "project_id": FIX_PROJ_DELETED,
            "user_id": USER_ID,
            "name": "Deleted Test Project",
            "github_owner": "fake-deleted-org-043",
            "github_repo": "demo-repo",
            "branch": "main",
            "auth_method": "github_app",
            "installation_id": FIX_IID_DELETED,
            "created_at": now,
        },
    ])
    yield
    # Cleanup
    db.github_installations.delete_many(
        {"installation_id": {"$in": [FIX_IID_SUSPENDED, FIX_IID_DELETED]}}
    )
    db.cto_projects.delete_many(
        {"project_id": {"$in": [FIX_PROJ_SUSPENDED, FIX_PROJ_DELETED]}}
    )


# ─────────── New /installations/health endpoint ────────────────────────

def test_health_endpoint_returns_all_installations(headers, fixtures):
    r = requests.get(f"{API}/github/app/installations/health", headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "installations" in data
    by_iid = {i["installation_id"]: i for i in data["installations"]}
    assert FIX_IID_SUSPENDED in by_iid, f"suspended install not present: {data}"
    assert FIX_IID_DELETED in by_iid, f"deleted install not present: {data}"

    s = by_iid[FIX_IID_SUSPENDED]
    assert s["status"] == "suspended"
    assert s["suspended_at"] is not None
    assert s["deleted_at"] is None
    assert s["repo_count"] == 1
    assert s["github_login"] == "fake-suspended-org-042"

    d = by_iid[FIX_IID_DELETED]
    assert d["status"] == "deleted"
    assert d["deleted_at"] is not None
    assert d["repo_count"] == 0


def test_health_endpoint_requires_auth():
    r = requests.get(f"{API}/github/app/installations/health", timeout=15)
    assert r.status_code in (401, 403)


# ─────────── Regression: /installations MUST stay active-only ──────────

def test_list_installations_is_still_active_only(headers, fixtures):
    r = requests.get(f"{API}/github/app/installations", headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    iids = {i["installation_id"] for i in r.json().get("installations", [])}
    assert FIX_IID_SUSPENDED not in iids, "regression: suspended install leaked into active list"
    assert FIX_IID_DELETED not in iids, "regression: deleted install leaked into active list"


# ─────────── connection-status short-circuit ───────────────────────────

def test_connection_status_marks_suspended_and_deleted(headers, fixtures):
    r = requests.get(f"{API}/cto/projects/connection-status", headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    by_pid = {s["project_id"]: s for s in data.get("statuses", [])}

    s = by_pid.get(FIX_PROJ_SUSPENDED)
    assert s is not None, f"suspended project missing from statuses: {data}"
    assert s["status"] == "disconnected"
    assert s["error"] == "installation_suspended"
    assert s.get("installation_id") == FIX_IID_SUSPENDED

    d = by_pid.get(FIX_PROJ_DELETED)
    assert d is not None
    assert d["status"] == "disconnected"
    assert d["error"] == "installation_deleted"
    assert d.get("installation_id") == FIX_IID_DELETED
