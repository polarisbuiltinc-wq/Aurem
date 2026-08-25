"""Live integration verify for Priority-1 restore drill fix.

Tests against REACT_APP_BACKEND_URL:
- Login as admin
- POST /admin/backups/drill-now 4x in quick succession (no crashes/502)
- GET /admin/backups/drill-history checks checked_at monotonicity
- POST /admin/backups/run (regression)
- POST /admin/backups/test-restore (regression, single-scratch consolidation)
"""
import os
import time
import requests
import pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_headers():
    # Try common login endpoints
    for path in ("/auth/login", "/admin/login", "/login"):
        try:
            r = requests.post(f"{API}{path}", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
            if r.status_code == 200:
                data = r.json()
                token = data.get("access_token") or data.get("token") or data.get("jwt")
                if token:
                    return {"Authorization": f"Bearer {token}"}
        except Exception:
            pass
    # Try cookie-based
    s = requests.Session()
    for path in ("/auth/login", "/admin/login"):
        try:
            r = s.post(f"{API}{path}", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
            if r.status_code == 200:
                # return session cookies as a header proxy
                cookies = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
                if cookies:
                    return {"Cookie": cookies}
        except Exception:
            pass
    pytest.skip("Could not authenticate as admin")


def test_drill_now_repeated_no_crash(admin_headers):
    """Call drill-now 4x back-to-back — must all return 200, no 502/499."""
    results = []
    for i in range(4):
        r = requests.post(f"{API}/admin/backups/drill-now", headers=admin_headers, timeout=180)
        results.append((i, r.status_code, r.text[:200] if r.status_code != 200 else ""))
        assert r.status_code == 200, f"iteration {i}: got {r.status_code}: {r.text[:400]}"
        data = r.json()
        for k in ("r2_key", "ok", "duration_ms", "checked_at", "collection_coverage", "fallback_attempts"):
            assert k in data, f"iteration {i}: missing key {k} in {list(data.keys())}"
        assert isinstance(data["fallback_attempts"], list)
    print("drill-now results:", results)


def test_drill_history_shape_and_monotonic(admin_headers):
    r1 = requests.get(f"{API}/admin/backups/drill-history", headers=admin_headers, timeout=30)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1.get("ok") is True
    for k in ("last_ok_at", "last_fail_at", "last_result"):
        assert k in d1, f"missing {k}"
    hist1 = d1.get("history", [])
    assert isinstance(hist1, list)
    newest1 = hist1[0]["checked_at"] if hist1 else None

    # trigger another drill
    r_drill = requests.post(f"{API}/admin/backups/drill-now", headers=admin_headers, timeout=180)
    assert r_drill.status_code == 200

    r2 = requests.get(f"{API}/admin/backups/drill-history", headers=admin_headers, timeout=30)
    assert r2.status_code == 200
    d2 = r2.json()
    hist2 = d2.get("history", [])
    newest2 = hist2[0]["checked_at"] if hist2 else None
    assert newest2 is not None
    if newest1 is not None:
        assert newest2 >= newest1, f"newest checked_at did not advance: {newest1} -> {newest2}"


def test_backup_run_regression(admin_headers):
    r = requests.post(f"{API}/admin/backups/run", headers=admin_headers, timeout=180)
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    assert data.get("ok") is True
    for k in ("r2_key", "size_bytes", "duration_ms", "total_docs", "total_collections"):
        assert k in data, f"missing {k}"


def test_test_restore_regression(admin_headers):
    r = requests.post(f"{API}/admin/backups/test-restore", headers=admin_headers, timeout=240)
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    assert data.get("ok") is True
    assert "source_counts" in data
    assert "restored_counts" in data
    assert "mismatches" in data
    assert "counts_match" in data
    assert isinstance(data["mismatches"], list)
    # collection_counts one entry per source collection
    src = data["source_counts"]
    rest = data["restored_counts"]
    assert isinstance(src, dict) and isinstance(rest, dict)
    # every source coll should appear in restored (possibly with mismatch)
    missing = [c for c in src.keys() if c not in rest]
    assert not missing, f"restored_counts missing collections: {missing[:5]}"
