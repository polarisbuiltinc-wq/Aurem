"""Phase 3a — GET /admin/duplication and /admin/churn-risk backend tests."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

TEST_EMAIL = "test@aurem.dev"
TEST_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login",
                      json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
                      timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    tok = data.get("token") or data.get("access_token") or data.get("jwt")
    assert tok, f"no token in response: {list(data.keys())}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------- /admin/duplication ----------------
class TestDuplication:
    def test_requires_auth(self):
        r = requests.get(f"{API}/admin/duplication", timeout=10)
        assert r.status_code in (401, 403), f"expected auth-gated, got {r.status_code}"

    def test_returns_200_with_schema(self, auth_headers):
        started = time.time()
        r = requests.get(f"{API}/admin/duplication", headers=auth_headers, timeout=90)
        elapsed = time.time() - started
        assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
        data = r.json()
        # Should be ok:true from real scan
        assert data.get("ok") is True, f"scan not ok: {data}"
        for k in ("duplication_pct", "duplicated_lines", "total_lines",
                  "clone_count", "files_scanned", "top_clusters"):
            assert k in data, f"missing key {k}"
        assert isinstance(data["duplication_pct"], (int, float))
        assert isinstance(data["total_lines"], int)
        assert data["total_lines"] > 0
        assert isinstance(data["top_clusters"], list)
        # Reasonable perf
        print(f"duplication scan took {elapsed:.2f}s, {data['files_scanned']} files, {data['duplication_pct']}%")

    def test_top_cluster_shape(self, auth_headers):
        r = requests.get(f"{API}/admin/duplication", headers=auth_headers, timeout=90)
        data = r.json()
        if data.get("ok") and data.get("top_clusters"):
            c = data["top_clusters"][0]
            for k in ("file_a", "file_b", "lines", "format"):
                assert k in c, f"cluster missing {k}: {c}"


# ---------------- /admin/churn-risk ----------------
class TestChurnRisk:
    def test_requires_auth(self):
        r = requests.get(f"{API}/admin/churn-risk", timeout=10)
        assert r.status_code in (401, 403)

    def test_returns_200_with_schema(self, auth_headers):
        started = time.time()
        r = requests.get(f"{API}/admin/churn-risk", headers=auth_headers, timeout=60)
        elapsed = time.time() - started
        assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
        data = r.json()
        assert "ok" in data
        if data.get("ok"):
            for k in ("window_days", "total_files_considered", "flagged_files", "rows"):
                assert k in data
            assert data["window_days"] == 90
            assert isinstance(data["rows"], list)
            # sorted by risk_score desc
            scores = [r["risk_score"] for r in data["rows"]]
            assert scores == sorted(scores, reverse=True), "rows must be sorted by risk_score desc"
            if data["rows"]:
                row = data["rows"][0]
                for k in ("file", "bloated", "has_complex_function", "risk_score"):
                    assert k in row
                # commits_last_90d dynamic key
                assert any(k.startswith("commits_last_") for k in row.keys())
        print(f"churn-risk took {elapsed:.2f}s, ok={data.get('ok')}, reason={data.get('reason')}")

    def test_custom_window(self, auth_headers):
        r = requests.get(f"{API}/admin/churn-risk?days=30", headers=auth_headers, timeout=60)
        assert r.status_code == 200
        data = r.json()
        if data.get("ok"):
            assert data["window_days"] == 30
