"""Iter 369 — restore-drill automation + ad-click attribution join.

Covers:
  1) POST /admin/backups/drill-now — real restore against latest R2 backup row
  2) GET  /admin/backups/drill-history — history rows written
  3) POST /admin/backups/run + /admin/backups/test-restore regression
  4) POST /ads/attribute-click — first-touch idempotent
  5) GET  /admin/users/{id} — includes ad_attribution
  6) GET  /admin/insights/activation-funnel/stage-users?stage=signed_up — ad_source label
  7) GET  /admin/insights/activation-funnel bottleneck fields present
"""
import os
import time
import uuid
import pytest
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASS  = "AuremTest2026!"
API = f"{BASE}/api/aurem-dev"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
                      timeout=30)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("access_token") or r.json().get("token")
    assert tok, f"no token in response: {r.json()}"
    return tok


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ─── Backup / restore-drill ─────────────────────────────────────────
class TestRestoreDrill:
    def test_drill_history_shape(self, admin_h):
        r = requests.get(f"{API}/admin/backups/drill-history", headers=admin_h, timeout=30)
        assert r.status_code == 200, r.text[:200]
        j = r.json()
        assert j.get("ok") is True
        assert isinstance(j.get("history"), list)
        # Keys always present (may be None if never run)
        for k in ("last_ok_at", "last_fail_at", "last_result"):
            assert k in j, f"missing key {k}"

    def test_drill_now_full_restore(self, admin_h):
        r = requests.post(f"{API}/admin/backups/drill-now",
                          headers=admin_h, timeout=180)
        assert r.status_code == 200, r.text[:400]
        j = r.json()
        # Contract fields must always exist
        for k in ("r2_key", "ok", "duration_ms", "checked_at"):
            assert k in j, f"missing contract field {k}: {j}"
        # If ok, all doc/collection counts must be real ints
        if j.get("ok") is True:
            assert isinstance(j.get("source_total_docs"), int)
            assert isinstance(j.get("restored_total_docs"), int)
            assert isinstance(j.get("collection_coverage"), (int, float))
            assert j["source_total_docs"] > 0
            assert j["restored_total_docs"] > 0
        else:
            # ok=False must still surface restore_error for observability
            assert j.get("restore_error"), f"ok=False but no restore_error: {j}"

    def test_drill_now_writes_history_row(self, admin_h):
        before = requests.get(f"{API}/admin/backups/drill-history",
                              headers=admin_h, timeout=30).json()
        before_ct = len(before.get("history") or [])
        rr = requests.post(f"{API}/admin/backups/drill-now",
                           headers=admin_h, timeout=180)
        assert rr.status_code == 200
        after = requests.get(f"{API}/admin/backups/drill-history",
                             headers=admin_h, timeout=30).json()
        after_ct = len(after.get("history") or [])
        assert after_ct >= before_ct + 1, f"drill-now did not append a history row ({before_ct} -> {after_ct})"

    def test_backup_run_regression(self, admin_h):
        # Existing manual backup trigger must still return 200
        r = requests.post(f"{API}/admin/backups/run", headers=admin_h, timeout=180)
        assert r.status_code in (200, 202), r.text[:200]
        assert isinstance(r.json(), dict)

    def test_backup_test_restore_regression(self, admin_h):
        r = requests.post(f"{API}/admin/backups/test-restore",
                          headers=admin_h, timeout=180)
        assert r.status_code == 200, r.text[:200]
        assert isinstance(r.json(), dict)


# ─── Ad-click attribution end-to-end ────────────────────────────────
@pytest.fixture(scope="module")
def new_user():
    """Create a fresh signup for ad-attribution test."""
    email = f"test+adattr{uuid.uuid4().hex[:8]}@aurem.dev"
    password = "TestPass123!"
    r = requests.post(f"{API}/auth/signup",
                      json={"email": email, "password": password},
                      timeout=30)
    assert r.status_code in (200, 201), f"signup failed: {r.status_code} {r.text[:300]}"
    j = r.json()
    tok = j.get("access_token") or j.get("token")
    uid = j.get("user_id") or (j.get("user") or {}).get("user_id")
    assert tok and uid, f"signup missing token/user_id: {j}"
    return {"email": email, "token": tok, "user_id": uid}


class TestAdAttribution:
    def test_attribute_click_first_touch(self, new_user):
        h = {"Authorization": f"Bearer {new_user['token']}"}
        body = {"gclid": "test123", "utm_source": "google",
                "utm_campaign": "launch", "landing_path": "/"}
        r = requests.post(f"{API}/ads/attribute-click",
                          headers=h, json=body, timeout=30)
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        assert j.get("ok") is True, f"expected ok=True, got {j}"
        attr = j.get("ad_attribution") or {}
        assert attr.get("gclid") == "test123"
        assert attr.get("utm_source") == "google"
        assert attr.get("utm_campaign") == "launch"
        assert attr.get("landing_path") == "/"
        assert "captured_at" in attr

    def test_attribute_click_idempotent(self, new_user):
        h = {"Authorization": f"Bearer {new_user['token']}"}
        body = {"gclid": "different456", "utm_source": "meta"}
        r = requests.post(f"{API}/ads/attribute-click",
                          headers=h, json=body, timeout=30)
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        assert j.get("ok") is False
        assert j.get("reason") == "already attributed"

    def test_admin_user_detail_shows_ad_attribution(self, admin_h, new_user):
        r = requests.get(f"{API}/admin/users/{new_user['user_id']}",
                         headers=admin_h, timeout=30)
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        # user detail structure may nest under "user"
        user_obj = j.get("user") or j
        attr = user_obj.get("ad_attribution")
        assert attr, f"ad_attribution missing on admin user detail: keys={list(user_obj.keys())[:20]}"
        assert attr.get("gclid") == "test123"
        assert attr.get("utm_source") == "google"

    def test_funnel_stage_users_shows_ad_source_google_ads(self, admin_h, new_user):
        r = requests.get(f"{API}/admin/insights/activation-funnel/stage-users",
                         headers=admin_h, params={"stage": "signed_up"}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        users = j.get("users") or j.get("rows") or []
        assert isinstance(users, list) and users, "no users returned for signed_up stage"
        found = next((u for u in users if u.get("user_id") == new_user["user_id"]
                      or u.get("email") == new_user["email"]), None)
        assert found, f"newly-signed-up test user not in signed_up stage list"
        assert found.get("ad_source") == "Google Ads", \
            f"ad_source should be 'Google Ads' (gclid set), got {found.get('ad_source')!r}"


# ─── Activation Funnel bottleneck regression (iter 368) ─────────────
class TestActivationFunnelRegression:
    def test_bottleneck_summary_fields(self, admin_h):
        r = requests.get(f"{API}/admin/insights/activation-funnel",
                         headers=admin_h, timeout=30)
        assert r.status_code == 200, r.text[:200]
        j = r.json()
        assert "bottleneck_summary" in j
        assert "stuck_counts" in j
        assert "biggest_bottleneck_stage" in j


# ─── Cleanup ────────────────────────────────────────────────────────
def test_zzz_cleanup_new_user(admin_h, new_user):
    """Best-effort cleanup — remove test user so DB doesn't drift."""
    # Try admin delete if available; otherwise just log.
    r = requests.delete(f"{API}/admin/users/{new_user['user_id']}",
                        headers=admin_h, timeout=30)
    # accept 200/204/404/405 — cleanup best-effort only
    assert r.status_code in (200, 202, 204, 404, 405), r.text[:200]
