"""
Backend tests for the Revoked-Repo Banner + Auto-Reconnect feature (2026-08-20).

Covers:
  1) PATCH /cto/projects/{id} with installation_id → sets auth_method=github_app
     and installation_id on the project doc (verified via GET).
  2) Regression: PATCH other fields (branch, tech_stack, preview_url) still work.
  3) GET /cto/projects/connection-status returns expected structure and the
     demo disconnected projects surface with status=disconnected.
"""
import os
import pytest
import requests

_RAW_BASE = os.environ.get("REACT_APP_BACKEND_URL")
if not _RAW_BASE:
    # live_env test (see tests/live_env_quarantine.txt) — skip cleanly
    # at collection time instead of a bare KeyError, which used to
    # abort the ENTIRE pytest session (Interrupted: 1 error during
    # collection) when this file ran alongside others in one process.
    pytest.skip("REACT_APP_BACKEND_URL not set — live_env test",
                allow_module_level=True)
BASE = _RAW_BASE.rstrip("/") + "/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/auth/login",
                      json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def projects(auth_headers):
    r = requests.get(f"{BASE}/cto/projects/list",
                     headers=auth_headers, timeout=30)
    assert r.status_code == 200, r.text
    return r.json().get("projects", [])


# -----------------------------------------------------------------------------
# connection-status response shape + demo disconnected projects surface
# -----------------------------------------------------------------------------
def test_connection_status_shape_and_disconnected(auth_headers):
    r = requests.get(f"{BASE}/cto/projects/connection-status",
                     headers=auth_headers, timeout=60)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("ok") is True
    statuses = j.get("statuses")
    assert isinstance(statuses, list)
    # every entry should have expected fields
    for s in statuses:
        assert "project_id" in s
        assert "status" in s
    # at least one disconnected entry should exist for this account (demo data)
    disc = [s for s in statuses if s.get("status") != "connected"]
    assert len(disc) >= 1, f"expected at least 1 disconnected project, got {statuses}"


# -----------------------------------------------------------------------------
# PATCH installation_id sets auth_method='github_app' and persists id
# -----------------------------------------------------------------------------
def test_patch_installation_id_sets_auth_method(auth_headers, projects):
    if not projects:
        pytest.skip("no projects on account")
    target = projects[0]
    pid = target["project_id"]
    original_auth = target.get("auth_method")
    original_iid = target.get("installation_id")
    test_iid = 987654321  # bogus but valid int
    try:
        r = requests.patch(f"{BASE}/cto/projects/{pid}",
                           headers=auth_headers,
                           json={"installation_id": test_iid}, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert "installation_id" in body.get("updated_fields", [])
        assert "auth_method" in body.get("updated_fields", [])

        # Verify persistence via list
        r2 = requests.get(f"{BASE}/cto/projects/list",
                          headers=auth_headers, timeout=30)
        assert r2.status_code == 200
        found = next((p for p in r2.json().get("projects", [])
                      if p["project_id"] == pid), None)
        assert found is not None
        assert found.get("auth_method") == "github_app"
        assert int(found.get("installation_id")) == test_iid
    finally:
        # Restore original values so we don't leave the account polluted.
        restore = {}
        if original_iid is not None:
            restore["installation_id"] = int(original_iid)
        # Can't null out auth_method via PATCH (it strips None), but at least
        # restore installation_id.  Doc other fix if needed.
        if restore:
            requests.patch(f"{BASE}/cto/projects/{pid}",
                           headers=auth_headers, json=restore, timeout=30)


# -----------------------------------------------------------------------------
# Regression: existing PATCH fields (branch / tech_stack / preview_url)
# -----------------------------------------------------------------------------
def test_patch_branch_and_tech_stack_and_preview(auth_headers, projects):
    if not projects:
        pytest.skip("no projects on account")
    target = projects[0]
    pid = target["project_id"]
    orig_branch = target.get("branch") or "main"
    orig_tech = target.get("tech_stack") or ""
    orig_prev = target.get("preview_url") or ""
    try:
        payload = {
            "branch": "TEST_branch_2026_08_20",
            "tech_stack": "TEST_stack",
            "preview_url": "https://example.com/test-preview",
        }
        r = requests.patch(f"{BASE}/cto/projects/{pid}",
                           headers=auth_headers, json=payload, timeout=30)
        assert r.status_code == 200, r.text
        updated = set(r.json().get("updated_fields", []))
        assert {"branch", "tech_stack", "preview_url"}.issubset(updated)

        r2 = requests.get(f"{BASE}/cto/projects/list",
                          headers=auth_headers, timeout=30)
        found = next((p for p in r2.json().get("projects", [])
                      if p["project_id"] == pid), None)
        assert found is not None
        assert found.get("branch") == payload["branch"]
        assert found.get("tech_stack") == payload["tech_stack"]
        assert found.get("preview_url") == payload["preview_url"]
    finally:
        # Restore
        restore = {}
        if orig_branch:
            restore["branch"] = orig_branch
        if orig_tech:
            restore["tech_stack"] = orig_tech
        if orig_prev:
            restore["preview_url"] = orig_prev
        if restore:
            requests.patch(f"{BASE}/cto/projects/{pid}",
                           headers=auth_headers, json=restore, timeout=30)


def test_patch_empty_returns_400(auth_headers, projects):
    if not projects:
        pytest.skip("no projects on account")
    pid = projects[0]["project_id"]
    r = requests.patch(f"{BASE}/cto/projects/{pid}",
                       headers=auth_headers, json={}, timeout=30)
    assert r.status_code == 400


def test_patch_bad_project_404(auth_headers):
    r = requests.patch(f"{BASE}/cto/projects/nonexistent_pid_xyz",
                       headers=auth_headers,
                       json={"branch": "main"}, timeout=30)
    assert r.status_code == 404
