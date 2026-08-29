"""Phase 2c — real end-to-end smoke tests for routers/codebase_health.py
against the ACTUAL preview server + a real GitHub-App-connected repo.

Quarantined under tests/live_env_quarantine.txt: these hit a live
external server (REACT_APP_BACKEND_URL), so they contribute ZERO to
the pytest-cov coverage.json measured by ci_check_coverage_ratchet.py
(that code executes in the separate supervisor-managed backend
process, not inside this pytest process). Real coverage for this file
comes from test_phase2c_codebase_health_router.py's in-process
TestClient suite. This file exists purely for genuine end-to-end
confidence against a real repo — kept deliberately small.

Reuses the pre-seeded testbed project (test_credentials.md):
  test@aurem.dev owns project p_6d0be78cdd -> polarisbuiltinc-wq/
  aurem-rollback-testbed via GitHub App installation 152797252.
"""
import os

import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE_URL:
    try:
        for _ln in open("/app/frontend/.env"):
            if _ln.strip().startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = _ln.split("=", 1)[1].strip().rstrip("/")
                break
    except FileNotFoundError:
        pass
API = f"{BASE_URL}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"
TESTBED_PROJECT_ID = "p_6d0be78cdd"  # funnel-repro -> aurem-rollback-testbed


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login",
                      json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    if data.get("mfa_required"):
        pytest.skip("MFA required — cannot proceed without TOTP")
    return data["token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.flaky(
    reason="Live scan against a real GitHub-App-connected repo — "
           "intermittent 499 'client disconnected or upstream error' seen "
           "in full-suite batch runs, passes reliably standalone. "
           "Confirmed 2026-08-28 P0-4 audit (RECON-LEDGER.md).",
    owner="e1-agent",
    fix_by="next-live-network-hardening-pass",
)
def test_scan_success_full_categories_real_repo(headers):
    r = requests.post(
        f"{API}/codebase-health/scan", headers=headers,
        json={"project_id": TESTBED_PROJECT_ID,
              "categories": ["security", "performance", "code_quality",
                              "dependencies", "database"]},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert 0 <= body["score"] <= 100
    assert body["scanned_files"] >= 1
    assert set(body["breakdown"].keys()) == {
        "security", "performance", "code_quality", "dependencies", "database",
    }
    assert "X-Scan-Remaining" in r.headers


@pytest.mark.flaky(
    reason="Live scan against a real GitHub-App-connected repo — "
           "intermittent 499 'client disconnected or upstream error' seen "
           "in full-suite batch runs, passes reliably standalone. "
           "Confirmed 2026-08-28 P0-4 audit (RECON-LEDGER.md).",
    owner="e1-agent",
    fix_by="next-live-network-hardening-pass",
)
def test_scan_called_twice_in_a_row_both_succeed(headers):
    r1 = requests.post(f"{API}/codebase-health/scan", headers=headers,
                       json={"project_id": TESTBED_PROJECT_ID,
                             "categories": ["security"]}, timeout=45)
    r2 = requests.post(f"{API}/codebase-health/scan", headers=headers,
                       json={"project_id": TESTBED_PROJECT_ID,
                             "categories": ["security"]}, timeout=45)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["ok"] is True and r2.json()["ok"] is True


def test_last_scan_returns_persisted_result_after_a_real_scan(headers):
    requests.post(f"{API}/codebase-health/scan", headers=headers,
                 json={"project_id": TESTBED_PROJECT_ID,
                       "categories": ["security"]}, timeout=45)
    r = requests.get(f"{API}/codebase-health/last", headers=headers,
                     params={"project_id": TESTBED_PROJECT_ID}, timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["score"] is not None
    assert "breakdown" in body
    assert body["created_at"] is not None
