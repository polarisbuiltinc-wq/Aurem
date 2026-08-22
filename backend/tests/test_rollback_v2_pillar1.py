"""Pillar 1 Rollback v2 — adversarial verification (iter 2026-01-24)."""
import os
import time
import uuid

import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE:
    # fall back to reading frontend .env
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE = line.split("=", 1)[1].strip().rstrip("/")

API = f"{BASE}/api/aurem-dev"
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PW = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PW},
                      timeout=30)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def nonadmin_token():
    # Try existing account first
    email = "free-gate-test-0822@aurem.dev"
    pw = "FreeGateTest2026!"
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=30)
    if r.status_code == 200 and r.json().get("token"):
        return r.json()["token"]
    # else create a fresh signup
    email = f"na-{uuid.uuid4().hex[:10]}@aurem.dev"
    pw = "NonAdminTest2026!"
    r = requests.post(f"{API}/auth/signup",
                      json={"email": email, "password": pw, "name": "NA Test"},
                      timeout=30)
    if r.status_code in (200, 201):
        tok = r.json().get("token")
        if tok:
            return tok
    # try login of the freshly created one
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=30)
    if r.status_code == 200:
        return r.json().get("token")
    pytest.skip("could not obtain non-admin token")


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


# ------------------------------------------------------------------
# 1. Auth gating
# ------------------------------------------------------------------
GATED_ENDPOINTS = [
    ("POST", "/admin/rollback2/snapshot", {"owner": "x", "repo": "y", "file_paths": ["a"]}),
    ("GET", "/admin/rollback2/snapshots", None),
    ("POST", "/admin/rollback2/preview", {"snapshot_id": "x"}),
    ("POST", "/admin/rollback2/execute",
     {"snapshot_id": "x", "preview_token": "x", "confirm": False}),
    ("GET", "/admin/rollback2/attempts", None),
    ("POST", "/admin/rollback2/drill", None),
    ("GET", "/admin/rollback2/drills", None),
]


@pytest.mark.parametrize("method,path,body", GATED_ENDPOINTS)
def test_gated_unauthenticated(method, path, body):
    url = f"{API}{path}"
    if method == "GET":
        r = requests.get(url, timeout=15)
    else:
        r = requests.post(url, json=body or {}, timeout=15)
    assert r.status_code in (401, 403), \
        f"unauthenticated {method} {path} → {r.status_code} (expected 401/403)"


@pytest.mark.parametrize("method,path,body", GATED_ENDPOINTS)
def test_gated_nonadmin(method, path, body, nonadmin_token):
    url = f"{API}{path}"
    hdrs = _h(nonadmin_token)
    if method == "GET":
        r = requests.get(url, headers=hdrs, timeout=15)
    else:
        r = requests.post(url, json=body or {}, headers=hdrs, timeout=15)
    assert r.status_code in (401, 403), \
        f"non-admin {method} {path} → {r.status_code} (expected 401/403)"


# ------------------------------------------------------------------
# 2. Snapshot creation
# ------------------------------------------------------------------
@pytest.fixture(scope="module")
def snapshot(admin_token):
    body = {
        "owner": "polarisbuiltinc-wq",
        "repo": "auremdev",
        "branch": "main",
        "file_paths": ["backend/main.py", "nonexistent/file.py"],
    }
    r = requests.post(f"{API}/admin/rollback2/snapshot",
                      json=body, headers=_h(admin_token), timeout=60)
    assert r.status_code == 200, f"snapshot create → {r.status_code} {r.text[:400]}"
    data = r.json()
    assert data.get("ok") is True
    snap = data["snapshot"]
    assert snap.get("snapshot_id", "").startswith("snap_")
    assert snap.get("r2_key")
    assert isinstance(snap.get("base_commit_sha"), str) and len(snap["base_commit_sha"]) > 0
    manifest = {m["path"]: m for m in snap["file_manifest"]}
    assert manifest["backend/main.py"]["present"] is True
    assert manifest["backend/main.py"]["sha256"]
    assert manifest["nonexistent/file.py"]["present"] is False
    return snap


def test_snapshot_ok(snapshot):
    assert snapshot["snapshot_id"]


# ------------------------------------------------------------------
# 3. List snapshots — JSON-serializable, no _id
# ------------------------------------------------------------------
def test_list_snapshots_contains_created(admin_token, snapshot):
    r = requests.get(f"{API}/admin/rollback2/snapshots",
                     headers=_h(admin_token), timeout=15)
    assert r.status_code == 200
    body = r.json()
    ids = [s["snapshot_id"] for s in body["snapshots"]]
    assert snapshot["snapshot_id"] in ids
    for s in body["snapshots"]:
        assert "_id" not in s, "MongoDB _id leaked in response"


# ------------------------------------------------------------------
# 4. Preview → issues token, backend/main.py unchanged
# ------------------------------------------------------------------
@pytest.fixture(scope="module")
def preview(admin_token, snapshot):
    r = requests.post(f"{API}/admin/rollback2/preview",
                      json={"snapshot_id": snapshot["snapshot_id"]},
                      headers=_h(admin_token), timeout=60)
    assert r.status_code == 200, f"preview → {r.status_code} {r.text[:300]}"
    data = r.json()
    assert data.get("ok") is True
    assert data.get("preview_token")
    assert data.get("expires_in_min") == 15
    files = {f["path"]: f for f in data["files"]}
    # backend/main.py should be unchanged (repo has not drifted)
    assert files["backend/main.py"]["status"] == "unchanged"
    return data


def test_preview_ok(preview):
    assert preview["preview_token"]


# ------------------------------------------------------------------
# 5. Execute with confirm=false — must refuse
# ------------------------------------------------------------------
def test_execute_confirm_required(admin_token, snapshot, preview):
    r = requests.post(f"{API}/admin/rollback2/execute",
                      json={"snapshot_id": snapshot["snapshot_id"],
                            "preview_token": preview["preview_token"],
                            "confirm": False},
                      headers=_h(admin_token), timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is False
    assert data.get("reason") == "confirm_required"


# ------------------------------------------------------------------
# 6. Execute with bogus token — must refuse
# ------------------------------------------------------------------
def test_execute_bogus_token(admin_token, snapshot):
    r = requests.post(f"{API}/admin/rollback2/execute",
                      json={"snapshot_id": snapshot["snapshot_id"],
                            "preview_token": "not-a-real-token-" + uuid.uuid4().hex,
                            "confirm": True},
                      headers=_h(admin_token), timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is False
    assert data.get("reason") == "preview_token_invalid"


# ------------------------------------------------------------------
# 7. Execute with real token, confirm=true → EXPECTED fail-closed 403
# ------------------------------------------------------------------
@pytest.fixture(scope="module")
def execute_result(admin_token, snapshot, preview):
    r = requests.post(f"{API}/admin/rollback2/execute",
                      json={"snapshot_id": snapshot["snapshot_id"],
                            "preview_token": preview["preview_token"],
                            "confirm": True},
                      headers=_h(admin_token), timeout=60)
    assert r.status_code == 200
    return r.json()


def test_execute_fails_closed(execute_result):
    data = execute_result
    assert data.get("ok") is False, f"execute unexpectedly succeeded: {data}"
    assert data.get("reason") == "restore_commit_failed"
    assert data.get("attempt_id", "").startswith("rba_")
    detail = data.get("detail", "")
    assert "403" in detail, f"expected 403 in detail, got: {detail}"
    # secret non-leakage: token env variable should not appear in detail
    tok_env = (os.environ.get("AUREM_DRILL_TOKEN", "")
               or os.environ.get("GITHUB_ACTIONS_TOKEN", ""))
    if tok_env:
        assert tok_env not in detail, "token leaked in error detail"
    # generic ghp_/github_pat_ patterns shouldn't leak
    assert "ghp_" not in detail
    assert "github_pat_" not in detail


# ------------------------------------------------------------------
# 8. Attempts ledger contains the failure
# ------------------------------------------------------------------
def test_attempts_ledger(admin_token, execute_result):
    time.sleep(1)
    r = requests.get(f"{API}/admin/rollback2/attempts",
                     headers=_h(admin_token), timeout=15)
    assert r.status_code == 200
    attempts = r.json()["attempts"]
    match = [a for a in attempts if a.get("attempt_id") == execute_result["attempt_id"]]
    assert match, f"attempt_id {execute_result['attempt_id']} not in ledger"
    a = match[0]
    assert a["mechanism"] == "snapshot_restore"
    assert a["result"] == "failed"
    assert a.get("failure_reason")
    assert a.get("timestamp")
    for x in attempts:
        assert "_id" not in x


# ------------------------------------------------------------------
# 9. Preview token single-use enforcement
# ------------------------------------------------------------------
def test_preview_token_single_use(admin_token, snapshot, preview, execute_result):
    r = requests.post(f"{API}/admin/rollback2/execute",
                      json={"snapshot_id": snapshot["snapshot_id"],
                            "preview_token": preview["preview_token"],
                            "confirm": True},
                      headers=_h(admin_token), timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is False
    assert data.get("reason") == "preview_token_already_used", \
        f"expected single-use enforcement, got {data}"


# ------------------------------------------------------------------
# 10. Drill blocked (AUREM_DRILL_REPO unset in preview)
# ------------------------------------------------------------------
def test_drill_blocked(admin_token):
    r = requests.post(f"{API}/admin/rollback2/drill",
                      headers=_h(admin_token), timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert data.get("result") == "blocked"
    steps = data.get("steps", [])
    assert steps and steps[0]["step"] == "config"
    assert steps[0]["status"] == "blocked"
    drill_id = data.get("drill_id")

    # list drills
    r = requests.get(f"{API}/admin/rollback2/drills",
                     headers=_h(admin_token), timeout=15)
    assert r.status_code == 200
    drills = r.json()["drills"]
    ids = [d["drill_id"] for d in drills]
    assert drill_id in ids
    for d in drills:
        assert "_id" not in d


# ------------------------------------------------------------------
# 11. Regression — core endpoints still healthy
# ------------------------------------------------------------------
def test_regression_login_ok():
    r = requests.post(f"{API}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PW},
                      timeout=30)
    assert r.status_code == 200
    assert r.json().get("token")


def test_regression_heartbeat(admin_token):
    r = requests.get(f"{API}/admin/synthetic-checks/heartbeat",
                     headers=_h(admin_token), timeout=15)
    assert r.status_code == 200
