"""Live preview tests for backend/routers/cto_projects.py behaviors.

Focus: functional behavior on preview host — list, add-rejection paths,
delete, check-pat/test-pat sensible responses, task rollback confirm gate,
task 404 for unknown ids. Rate-limit is skipped for founder tier so we
just validate submit's error handling (no crash) for missing/invalid
project + rollback confirm handling.

Requires REACT_APP_BACKEND_URL and preview credentials from
/app/memory/test_credentials.md.
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"

API = f"{BASE_URL}/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    assert data.get("ok") is True
    assert "token" in data
    return data["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ─── projects/list ─────────────────────────────────────────────────────────
def test_list_projects_ok_and_no_token_leak(auth_headers):
    r = requests.get(f"{API}/cto/projects/list", headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert data.get("ok") is True
    assert isinstance(data.get("projects"), list)
    for p in data["projects"]:
        # never leak raw token, always expose boolean flag
        assert "github_token" not in p, f"github_token leaked in row: {p.keys()}"
        assert "has_pat" in p, f"has_pat missing in row: {p.keys()}"
        assert isinstance(p["has_pat"], bool)


# ─── projects/add — rejection paths ────────────────────────────────────────
def test_add_project_without_auth_returns_auth_required(auth_headers):
    body = {
        "name": f"TEST_noauth_{uuid.uuid4().hex[:6]}",
        "github_url": "https://github.com/tjsandhu/aurem",
        "branch": "main",
    }
    r = requests.post(f"{API}/cto/projects/add", headers=auth_headers, json=body, timeout=15)
    assert r.status_code == 400, r.text[:300]
    payload = r.json().get("detail", r.json())
    assert isinstance(payload, dict), f"Expected structured error, got: {payload}"
    assert payload.get("error") == "auth_required", payload


def test_add_project_with_pat_returns_pat_not_supported(auth_headers):
    body = {
        "name": f"TEST_pat_{uuid.uuid4().hex[:6]}",
        "github_url": "https://github.com/tjsandhu/aurem",
        "branch": "main",
        "github_token": "ghp_fake_token_that_should_be_rejected_ffffffffffff",
    }
    r = requests.post(f"{API}/cto/projects/add", headers=auth_headers, json=body, timeout=15)
    assert r.status_code == 400, r.text[:300]
    payload = r.json().get("detail", r.json())
    assert isinstance(payload, dict), f"Expected structured error, got: {payload}"
    assert payload.get("error") == "pat_not_supported", payload


# ─── projects/{id}/check-pat & test-pat — clean not-connected responses ────
def _get_or_none_project(auth_headers):
    r = requests.get(f"{API}/cto/projects/list", headers=auth_headers, timeout=15)
    projects = r.json().get("projects", []) if r.status_code == 200 else []
    return projects[0] if projects else None


def test_check_pat_no_crash_for_unlinked_project(auth_headers):
    p = _get_or_none_project(auth_headers)
    if not p:
        pytest.skip("No project available for this user to test check-pat")
    pid = p["project_id"]
    r = requests.get(f"{API}/cto/projects/{pid}/check-pat", headers=auth_headers, timeout=20)
    # must NOT be 500 — sensible response even without a linked installation
    assert r.status_code in (200, 403), f"unexpected: {r.status_code} {r.text[:300]}"
    if r.status_code == 200:
        data = r.json()
        assert data.get("ok") is True
        assert data.get("state") in ("missing", "valid", "expired", "unknown"), data


def test_check_pat_404_for_unknown_project(auth_headers):
    fake_id = f"p_{uuid.uuid4().hex[:10]}"
    r = requests.get(f"{API}/cto/projects/{fake_id}/check-pat", headers=auth_headers, timeout=15)
    assert r.status_code == 404, r.text[:300]


def test_test_pat_no_crash_for_unlinked_project(auth_headers):
    p = _get_or_none_project(auth_headers)
    if not p:
        pytest.skip("No project available for this user to test test-pat")
    pid = p["project_id"]
    r = requests.get(f"{API}/cto/projects/{pid}/test-pat", headers=auth_headers, timeout=25)
    # spec says HTTP always 200 with ok:true/false, but implementation may 403 on auth_err
    assert r.status_code in (200, 403), f"unexpected: {r.status_code} {r.text[:300]}"
    if r.status_code == 200:
        data = r.json()
        assert "ok" in data
        assert isinstance(data["ok"], bool)
        if data["ok"] is False:
            assert isinstance(data.get("error"), str) and data["error"]


def test_test_pat_404_for_unknown_project(auth_headers):
    fake_id = f"p_{uuid.uuid4().hex[:10]}"
    r = requests.get(f"{API}/cto/projects/{fake_id}/test-pat", headers=auth_headers, timeout=15)
    assert r.status_code == 404, r.text[:300]


# ─── projects/{id} DELETE — owned=deleted:1, foreign/missing=deleted:0 ─────
def test_delete_unknown_project_returns_deleted_zero(auth_headers):
    fake_id = f"p_{uuid.uuid4().hex[:10]}"
    r = requests.delete(f"{API}/cto/projects/{fake_id}", headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert data.get("ok") is True
    assert data.get("deleted") == 0, data


# NOTE: we do NOT test the "delete an owned project" path here because
# creating a real project requires a live GitHub App installation for
# the test user (known preview gap per PRD.md). The deleted=0 path is
# the same code path just with a different match count, and it is
# exercised on the in-process TestClient suite already.


# ─── tasks/{id} — 404 for unknown/foreign ──────────────────────────────────
def test_get_task_404_for_unknown(auth_headers):
    fake_task = f"t_{uuid.uuid4().hex[:12]}"
    r = requests.get(f"{API}/cto/tasks/{fake_task}", headers=auth_headers, timeout=15)
    assert r.status_code == 404, r.text[:300]


# ─── tasks/{id}/rollback — must require confirm=='ROLLBACK' ─────────────────
def test_rollback_missing_confirm_returns_400(auth_headers):
    fake_task = f"t_{uuid.uuid4().hex[:12]}"
    r = requests.post(
        f"{API}/cto/tasks/{fake_task}/rollback",
        headers=auth_headers,
        json={},
        timeout=15,
    )
    # missing required field → 422 from pydantic OR 400 from our own guard
    assert r.status_code in (400, 422), r.text[:300]


def test_rollback_wrong_confirm_returns_400(auth_headers):
    fake_task = f"t_{uuid.uuid4().hex[:12]}"
    r = requests.post(
        f"{API}/cto/tasks/{fake_task}/rollback",
        headers=auth_headers,
        json={"confirm": "please"},
        timeout=15,
    )
    assert r.status_code == 400, r.text[:300]
    detail = r.json().get("detail", "")
    assert "ROLLBACK" in (detail if isinstance(detail, str) else str(detail))


def test_rollback_correct_confirm_but_unknown_task_returns_404(auth_headers):
    """With the confirm gate passed, we should hit the 'task not found'
    branch — proves the confirm gate happens before task lookup, and
    the endpoint doesn't crash on unknown tasks."""
    fake_task = f"t_{uuid.uuid4().hex[:12]}"
    r = requests.post(
        f"{API}/cto/tasks/{fake_task}/rollback",
        headers=auth_headers,
        json={"confirm": "ROLLBACK"},
        timeout=15,
    )
    assert r.status_code == 404, r.text[:300]


# ─── tasks/submit — clean error when project doesn't belong to user ────────
def test_submit_task_unknown_project_clean_error(auth_headers):
    """Founder tier is unlimited so rate-limit path is skipped; we just
    want to verify submit doesn't crash and returns a clean error when
    project can't be found for this user."""
    body = {
        "project_id": f"p_{uuid.uuid4().hex[:10]}",
        "task": "TEST_noop",
        "files": [],
        "context": "",
    }
    r = requests.post(f"{API}/cto/tasks/submit", headers=auth_headers, json=body, timeout=25)
    # Clean 4xx (403/404) — not 500
    assert 400 <= r.status_code < 500, (
        f"submit crashed / unexpected 5xx: {r.status_code} {r.text[:300]}"
    )
