"""Backend tests for System Maintenance / Outage Tracker (2026-08)."""
import os
import time
import subprocess
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
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


# -------- Boot-gap detection: real restart regression --------
# 2026-08-22 — fixed a race condition where the boot-gap-detection
# block (in main.py's lifespan startup) ran AFTER `_loop_housekeeping`
# was scheduled. That task's first tick (no initial sleep) also calls
# `write_heartbeat`, and any `await` in the boot-gap block let it run
# first and stamp a FRESH heartbeat — so the "gap since last beat"
# always read ~0s and outages were silently never logged, no matter
# how long the backend was actually down. This test backdates the
# heartbeat, forces a REAL supervisor restart, and asserts a new
# incident with a plausible duration appears — the only way to catch
# this class of race condition (unit tests on the functions alone
# can't see task-scheduling order).
class TestBootGapRealRestart:
    def test_real_restart_logs_outage_with_correct_duration(self, admin_headers):
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
        except ImportError:
            pytest.skip("motor not importable from this test runner")
        mongo_url = os.environ.get("MONGO_URL")
        if not mongo_url:
            pytest.skip("MONGO_URL not set in this test runner's env")

        import asyncio
        db_name = os.environ.get("DB_NAME", "aurem_dev")

        async def _prep():
            client = AsyncIOMotorClient(mongo_url)
            db = client[db_name]
            before_ids = {d["incident_id"] async for d in db.outage_incidents.find({}, {"incident_id": 1})}
            simulated_gap_s = 40
            backdated_ts = time.time() - simulated_gap_s
            await db.system_heartbeat.update_one(
                {"_id": "singleton"}, {"$set": {"last_beat_ts": backdated_ts}})
            return before_ids, simulated_gap_s

        before_ids, simulated_gap_s = asyncio.get_event_loop().run_until_complete(_prep())

        # Ensure threshold is well below the simulated gap so it must fire.
        r = requests.post(f"{BASE_URL}/api/aurem-dev/admin/maintenance/settings",
                          headers=admin_headers, json={"outage_threshold_s": 20}, timeout=10)
        assert r.status_code == 200

        subprocess.run(["sudo", "supervisorctl", "restart", "backend"],
                        check=True, capture_output=True, timeout=30)

        # Wait for the restarted backend to come back up.
        deadline = time.time() + 30
        up = False
        while time.time() < deadline:
            try:
                if requests.get(f"{BASE_URL}/api/health", timeout=3).status_code == 200:
                    up = True
                    break
            except requests.RequestException:
                pass
            time.sleep(1)
        assert up, "backend did not come back up after restart"

        r2 = requests.get(f"{BASE_URL}/api/aurem-dev/admin/maintenance/incidents",
                          headers=admin_headers, timeout=10)
        assert r2.status_code == 200
        new_incidents = [i for i in r2.json()["incidents"] if i["incident_id"] not in before_ids]
        assert len(new_incidents) == 1, \
            f"expected exactly 1 new outage incident after a real restart with a {simulated_gap_s}s backdated gap, got {new_incidents}"
        assert new_incidents[0]["duration_s"] >= simulated_gap_s - 2, \
            "logged duration should be at least the simulated gap"

        async def _cleanup(incident_id):
            client = AsyncIOMotorClient(mongo_url)
            db = client[db_name]
            await db.outage_incidents.delete_one({"incident_id": incident_id})

        asyncio.get_event_loop().run_until_complete(_cleanup(new_incidents[0]["incident_id"]))
