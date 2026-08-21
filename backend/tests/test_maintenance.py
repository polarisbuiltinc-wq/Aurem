"""Backend tests for System Maintenance / Outage Tracker (2026-08)."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://launch-pad-237.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PW = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_token():
    # Try common login endpoints
    for path in ["/api/aurem-dev/auth/login", "/api/auth/login", "/api/login"]:
        r = requests.post(f"{BASE_URL}{path}",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PW}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            tok = data.get("token") or data.get("access_token") or (data.get("user") or {}).get("token")
            if tok:
                return tok
    pytest.skip("Could not obtain admin token — login endpoints unknown")


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# -------- Public status endpoint --------
class TestPublicStatus:
    def test_public_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/aurem-dev/maintenance/status", timeout=10)
        assert r.status_code == 200
        d = r.json()
        for k in ("manual_enabled", "message", "window", "updated_at"):
            assert k in d, f"missing key: {k}"
        assert isinstance(d["manual_enabled"], bool)


# -------- Admin gating --------
class TestAdminAuthGating:
    def test_get_settings_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/maintenance", timeout=10)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"

    def test_incidents_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/maintenance/incidents", timeout=10)
        assert r.status_code in (401, 403)

    def test_post_settings_no_auth(self):
        r = requests.post(f"{BASE_URL}/api/aurem-dev/admin/maintenance/settings",
                          json={"manual_enabled": False}, timeout=10)
        assert r.status_code in (401, 403)


# -------- Admin CRUD + partial update --------
class TestAdminSettings:
    def test_get_settings_with_auth(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/maintenance",
                         headers=admin_headers, timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "manual_enabled" in d
        assert "outage_threshold_s" in d

    def test_partial_update_preserves_other_fields(self, admin_headers):
        # First set message + window
        r1 = requests.post(f"{BASE_URL}/api/aurem-dev/admin/maintenance/settings",
                           headers=admin_headers,
                           json={"message": "TEST_pre_message", "window": "TEST_pre_window",
                                 "outage_threshold_s": 45, "manual_enabled": False},
                           timeout=10)
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert d1["message"] == "TEST_pre_message"
        assert d1["window"] == "TEST_pre_window"
        assert d1["outage_threshold_s"] == 45

        # Now partial update — only threshold. Message/window MUST remain.
        r2 = requests.post(f"{BASE_URL}/api/aurem-dev/admin/maintenance/settings",
                           headers=admin_headers,
                           json={"outage_threshold_s": 60}, timeout=10)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["outage_threshold_s"] == 60
        assert d2["message"] == "TEST_pre_message", "partial update wiped message!"
        assert d2["window"] == "TEST_pre_window", "partial update wiped window!"

    def test_outage_threshold_clamped_low(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/aurem-dev/admin/maintenance/settings",
                          headers=admin_headers, json={"outage_threshold_s": 1}, timeout=10)
        assert r.status_code == 200
        assert r.json()["outage_threshold_s"] == 5, "expected clamp to min=5"

    def test_outage_threshold_clamped_high(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/aurem-dev/admin/maintenance/settings",
                          headers=admin_headers, json={"outage_threshold_s": 9999}, timeout=10)
        assert r.status_code == 200
        assert r.json()["outage_threshold_s"] == 600, "expected clamp to max=600"

    def test_manual_toggle_reflects_publicly(self, admin_headers):
        # Turn ON
        r = requests.post(f"{BASE_URL}/api/aurem-dev/admin/maintenance/settings",
                          headers=admin_headers,
                          json={"manual_enabled": True, "message": "TEST_maint_msg",
                                "window": "TEST_win_5m"}, timeout=10)
        assert r.status_code == 200
        time.sleep(0.5)
        pub = requests.get(f"{BASE_URL}/api/aurem-dev/maintenance/status", timeout=10).json()
        assert pub["manual_enabled"] is True
        assert pub["message"] == "TEST_maint_msg"
        assert pub["window"] == "TEST_win_5m"

        # Turn OFF (cleanup — critical!)
        r2 = requests.post(f"{BASE_URL}/api/aurem-dev/admin/maintenance/settings",
                           headers=admin_headers,
                           json={"manual_enabled": False, "message": "", "window": ""},
                           timeout=10)
        assert r2.status_code == 200
        pub2 = requests.get(f"{BASE_URL}/api/aurem-dev/maintenance/status", timeout=10).json()
        assert pub2["manual_enabled"] is False


# -------- Incidents endpoint --------
class TestIncidents:
    def test_incidents_shape(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/maintenance/incidents",
                         headers=admin_headers, timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "incidents" in d and isinstance(d["incidents"], list)
        assert "stats" in d
        s = d["stats"]
        for k in ("count_all", "count_30d", "total_downtime_s_30d", "avg_duration_s_30d"):
            assert k in s, f"missing stats key {k}"


# -------- Final safety: ensure manual is OFF at end --------
def test_zzz_final_ensure_manual_off(admin_headers):
    requests.post(f"{BASE_URL}/api/aurem-dev/admin/maintenance/settings",
                  headers=admin_headers, json={"manual_enabled": False}, timeout=10)
    pub = requests.get(f"{BASE_URL}/api/aurem-dev/maintenance/status", timeout=10).json()
    assert pub["manual_enabled"] is False
