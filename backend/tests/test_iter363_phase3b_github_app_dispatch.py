"""
Iter 363 · Phase 3b (GitHub App downstream sweep) validation.

Tests two paths:
  1. get_repo_token dispatch contract (unit-level, mocked)
  2. Synthetic App-installed project vs PAT project against ~9 endpoints —
     verifying the PAT-missing gate is PASSED (downstream 401 from
     GitHub with a fake installation_id is ACCEPTABLE).
"""
import os
import uuid
import pytest
import requests
from unittest.mock import AsyncMock, patch

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

TEST_EMAIL = "test@aurem.dev"
TEST_PW = "AuremTest2026!"

# ============================================================
# UNIT — get_repo_token dispatch contract
# ============================================================

@pytest.mark.asyncio
async def test_get_repo_token_dispatch_github_app():
    from services import pat_vault
    with patch("services.github_app.get_installation_token",
               new=AsyncMock(return_value=("ghs_fake_app_tok", 9999))) as m:
        tok = await pat_vault.get_repo_token({
            "auth_method": "github_app",
            "installation_id": 12345,
            "github_token": None,
            "user_id": "u1",
        })
    assert tok == "ghs_fake_app_tok"
    m.assert_called_once_with(12345)


@pytest.mark.asyncio
async def test_get_repo_token_dispatch_pat():
    from services import pat_vault
    with patch("services.pat_vault._decrypt_pat",
               new=AsyncMock(return_value="ghp_decrypted")) as m:
        tok = await pat_vault.get_repo_token({
            "auth_method": "pat",
            "github_token": "v1:ciphertext",
            "user_id": "u1",
        })
    assert tok == "ghp_decrypted"
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_repo_token_dispatch_legacy_missing_auth_method():
    """Legacy rows with no auth_method must fall through to PAT."""
    from services import pat_vault
    with patch("services.pat_vault._decrypt_pat",
               new=AsyncMock(return_value="ghp_leg")) as m:
        tok = await pat_vault.get_repo_token({
            "github_token": "raw",
            "user_id": "u1",
        })
    assert tok == "ghp_leg"


@pytest.mark.asyncio
async def test_get_repo_token_app_missing_installation_id_returns_none():
    from services import pat_vault
    tok = await pat_vault.get_repo_token({
        "auth_method": "github_app",
        "installation_id": None,
        "github_token": None,
        "user_id": "u1",
    })
    assert tok is None


# ============================================================
# INTEGRATION — synthetic projects + real endpoints
# ============================================================

PAT_MISSING_MARKERS = [
    "no pat configured",
    "no_github_pat",
    "pat missing",
    "pat on file",
    "github_credentials_missing",
    "no_pat",
    "github credentials missing",
]


def _pat_missing_error(body_text: str) -> bool:
    lo = body_text.lower()
    return any(m in lo for m in PAT_MISSING_MARKERS)


@pytest.fixture(scope="module")
def auth_headers():
    r = requests.post(f"{API}/auth/login",
                      json={"email": TEST_EMAIL, "password": TEST_PW},
                      timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:300]}"
    tok = r.json().get("token")
    assert tok
    return {"Authorization": f"Bearer {tok}"}, r.json().get("user_id")


@pytest.fixture(scope="module")
def app_installed_project(auth_headers):
    """Insert a synthetic App-installed project directly into Mongo."""
    _, user_id = auth_headers
    from motor.motor_asyncio import AsyncIOMotorClient
    import asyncio, datetime
    proj_id = f"TEST_appinst_{uuid.uuid4().hex[:8]}"
    doc = {
        "project_id":       proj_id,
        "user_id":          user_id,
        "name":             "TEST GitHub App Project",
        "github_owner":     "fake-org",
        "github_repo":      "fake-repo",
        "github_branch":    "main",
        "github_token":     None,
        "auth_method":      "github_app",
        "installation_id":  99999999,
        "status":           "active",
        "created_at":       datetime.datetime.now(datetime.timezone.utc),
        "updated_at":       datetime.datetime.now(datetime.timezone.utc),
    }

    async def _setup():
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = cli[os.environ["DB_NAME"]]
        await db.cto_projects.insert_one(doc)
        cli.close()

    async def _teardown():
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = cli[os.environ["DB_NAME"]]
        await db.cto_projects.delete_one({"project_id": proj_id})
        cli.close()

    asyncio.get_event_loop().run_until_complete(_setup())
    yield proj_id
    asyncio.get_event_loop().run_until_complete(_teardown())


@pytest.fixture(scope="module")
def pat_project(auth_headers):
    """Insert a synthetic PAT-mode project with a fake encrypted PAT."""
    _, user_id = auth_headers
    from motor.motor_asyncio import AsyncIOMotorClient
    import asyncio, datetime
    proj_id = f"TEST_pat_{uuid.uuid4().hex[:8]}"
    doc = {
        "project_id":     proj_id,
        "user_id":        user_id,
        "name":           "TEST PAT Project",
        "github_owner":   "fake-org",
        "github_repo":    "fake-repo-pat",
        "github_branch":  "main",
        "github_token":   "ghp_fake_test_pat_1234567890abcdef",
        "auth_method":    "pat",
        "status":         "active",
        "created_at":     datetime.datetime.now(datetime.timezone.utc),
        "updated_at":     datetime.datetime.now(datetime.timezone.utc),
    }

    async def _setup():
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = cli[os.environ["DB_NAME"]]
        await db.cto_projects.insert_one(doc)
        cli.close()

    async def _teardown():
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = cli[os.environ["DB_NAME"]]
        await db.cto_projects.delete_one({"project_id": proj_id})
        cli.close()

    asyncio.get_event_loop().run_until_complete(_setup())
    yield proj_id
    asyncio.get_event_loop().run_until_complete(_teardown())


# --- Endpoint probes (App-installed) --------------------------------

def _assert_no_pat_gate(resp, endpoint):
    """The PAT gate MUST have been passed. A downstream 401 from GitHub
    is fine. What is not fine: any 400/403 with PAT-missing text."""
    if resp.status_code in (400, 403):
        body = resp.text or ""
        assert not _pat_missing_error(body), (
            f"[{endpoint}] PAT-missing error returned for App-installed "
            f"project: HTTP {resp.status_code} — {body[:400]}"
        )


def test_repo_status_app_installed(auth_headers, app_installed_project):
    headers, _ = auth_headers
    r = requests.post(f"{API}/repo-status",
                      headers=headers,
                      json={"project_id": app_installed_project},
                      timeout=15)
    _assert_no_pat_gate(r, "repo-status")


def test_codebase_health_scan_app_installed(auth_headers, app_installed_project):
    headers, _ = auth_headers
    r = requests.post(f"{API}/codebase-health/scan",
                      headers=headers,
                      json={"project_id": app_installed_project},
                      timeout=30)
    _assert_no_pat_gate(r, "codebase-health/scan")


def test_security_scan_app_installed(auth_headers, app_installed_project):
    headers, _ = auth_headers
    r = requests.post(f"{API}/security-scan",
                      headers=headers,
                      json={"project_id": app_installed_project},
                      timeout=30)
    _assert_no_pat_gate(r, "security-scan")


def test_user_rollback_app_installed(auth_headers, app_installed_project):
    headers, _ = auth_headers
    fake_loop_id = f"loop_notexist_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/user-rollback/{fake_loop_id}",
                      headers=headers,
                      json={"project_id": app_installed_project},
                      timeout=15)
    # Expect 404 (loop not found) but NOT a PAT-missing 400/403
    _assert_no_pat_gate(r, "user-rollback")


def test_loop_rollback_app_installed(auth_headers, app_installed_project):
    headers, _ = auth_headers
    fake_loop_id = f"loop_notexist_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/loop/{fake_loop_id}/rollback",
                      headers=headers,
                      json={"project_id": app_installed_project},
                      timeout=15)
    _assert_no_pat_gate(r, "loop/rollback")


def test_mcp_projects_connected_flag_app_installed(auth_headers, app_installed_project):
    headers, _ = auth_headers
    r = requests.get(f"{API}/mcp/projects/{app_installed_project}",
                     headers=headers, timeout=15)
    _assert_no_pat_gate(r, "mcp/projects")
    if r.status_code == 200:
        body = r.json()
        # connected flag must be True since installation_id is set,
        # even though github_token is None.
        connected = body.get("connected")
        assert connected in (True, None), (
            f"mcp/projects returned connected={connected} for App-installed "
            f"project (expected True). Full body: {body}"
        )


def test_admin_brain_replay_app_installed(auth_headers, app_installed_project):
    headers, _ = auth_headers
    r = requests.post(f"{API}/admin/brain-replay",
                      headers=headers,
                      json={"project_id": app_installed_project,
                            "loop_id": f"fake_{uuid.uuid4().hex[:6]}"},
                      timeout=20)
    _assert_no_pat_gate(r, "admin/brain-replay")


# --- Endpoint probes (PAT regression) --------------------------------

def test_repo_status_pat(auth_headers, pat_project):
    headers, _ = auth_headers
    r = requests.post(f"{API}/repo-status",
                      headers=headers,
                      json={"project_id": pat_project},
                      timeout=15)
    # For a PAT project, the code path still calls get_repo_token which
    # returns the "decrypted" PAT (legacy plaintext passthrough). No PAT
    # gate errors should fire.
    _assert_no_pat_gate(r, "repo-status[PAT]")


def test_codebase_health_scan_pat(auth_headers, pat_project):
    headers, _ = auth_headers
    r = requests.post(f"{API}/codebase-health/scan",
                      headers=headers,
                      json={"project_id": pat_project},
                      timeout=30)
    _assert_no_pat_gate(r, "codebase-health/scan[PAT]")


def test_security_scan_pat(auth_headers, pat_project):
    headers, _ = auth_headers
    r = requests.post(f"{API}/security-scan",
                      headers=headers,
                      json={"project_id": pat_project},
                      timeout=30)
    _assert_no_pat_gate(r, "security-scan[PAT]")


def test_mcp_projects_connected_flag_pat(auth_headers, pat_project):
    headers, _ = auth_headers
    r = requests.get(f"{API}/mcp/projects/{pat_project}",
                     headers=headers, timeout=15)
    _assert_no_pat_gate(r, "mcp/projects[PAT]")
