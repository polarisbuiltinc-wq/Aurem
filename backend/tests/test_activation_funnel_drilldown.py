"""Iteration 368 - Tests for the Activation-Funnel drill-down feature:
- funnel endpoint now includes bottleneck_summary/stuck_counts/biggest_bottleneck_stage
- new /insights/activation-funnel/stage-users?stage=<key> endpoint
- admin user detail includes emails_sent list
"""
import os
import pytest
import requests

def _load_frontend_env():
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        return None
    return None


BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _load_frontend_env() or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL is not set"
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"
STAGE_KEYS = ["signed_up", "connected_github", "added_project", "sent_message", "shipped_code"]


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    tok = r.json().get("token") or r.json().get("access_token")
    if not tok:
        pytest.skip("No token in login response")
    return tok


@pytest.fixture(scope="module")
def headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# --- Activation funnel core endpoint (regression + new fields) --------------
class TestActivationFunnelCore:
    def test_returns_200_with_existing_and_new_fields(self, headers):
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/activation-funnel",
            headers=headers, timeout=20,
        )
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        # existing fields preserved
        for k in ("funnel_steps", "funnel", "conversion_rates", "totals", "recent_signups"):
            assert k in data, f"missing existing field {k}"
        # new fields present
        for k in ("bottleneck_summary", "stuck_counts", "biggest_bottleneck_stage"):
            assert k in data, f"missing new field {k}"
        assert isinstance(data["bottleneck_summary"], str)
        assert isinstance(data["stuck_counts"], dict)
        # stuck_counts must have exactly the 5 stage keys
        assert set(data["stuck_counts"].keys()) == set(STAGE_KEYS), data["stuck_counts"]
        # each value is int
        for k, v in data["stuck_counts"].items():
            assert isinstance(v, int), f"{k}={v!r}"

    def test_stuck_counts_reconcile_with_signed_up_total(self, headers):
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/activation-funnel",
            headers=headers, timeout=20,
        )
        data = r.json()
        signed_up_total = data["funnel"]["signed_up"]
        stuck_total = sum(data["stuck_counts"].values())
        assert stuck_total == signed_up_total, (
            f"sum(stuck_counts)={stuck_total} != funnel.signed_up={signed_up_total}"
        )

    def test_biggest_bottleneck_matches_max_bucket(self, headers):
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/activation-funnel",
            headers=headers, timeout=20,
        )
        data = r.json()
        stuck = data["stuck_counts"]
        biggest = data["biggest_bottleneck_stage"]
        if sum(stuck.values()) == 0:
            assert biggest is None
        else:
            assert biggest in STAGE_KEYS
            assert stuck[biggest] == max(stuck.values())


# --- Stage users drill-down endpoint ----------------------------------------
class TestStageUsersEndpoint:
    @pytest.mark.parametrize("stage", STAGE_KEYS)
    def test_valid_stage_returns_200(self, headers, stage):
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/activation-funnel/stage-users",
            params={"stage": stage}, headers=headers, timeout=20,
        )
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert data["ok"] is True
        assert data["stage"] == stage
        assert isinstance(data["label"], str) and data["label"]
        assert isinstance(data["count"], int)
        assert isinstance(data["users"], list)
        assert data["count"] == len(data["users"])
        for u in data["users"]:
            assert "user_id" in u and "email" in u
            assert "stage_reached_at" in u
            assert "stuck_hours" in u

    def test_users_sorted_longest_stuck_first(self, headers):
        # find a stage with >=2 users to verify sort
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/activation-funnel",
            headers=headers, timeout=20,
        )
        stuck = r.json()["stuck_counts"]
        stage = max(stuck, key=lambda k: stuck[k])
        if stuck[stage] < 2:
            pytest.skip("no stage with >=2 users to verify sort")
        r2 = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/activation-funnel/stage-users",
            params={"stage": stage}, headers=headers, timeout=20,
        )
        users = r2.json()["users"]
        hours = [u.get("stuck_hours") or 0 for u in users]
        assert hours == sorted(hours, reverse=True), hours

    def test_invalid_stage_returns_400(self, headers):
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/activation-funnel/stage-users",
            params={"stage": "bogus"}, headers=headers, timeout=15,
        )
        assert r.status_code == 400, r.text[:200]

    def test_missing_auth_rejected(self):
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/activation-funnel/stage-users",
            params={"stage": "signed_up"}, timeout=15,
        )
        assert r.status_code in (401, 403), r.status_code


# --- Admin user detail (emails_sent regression) -----------------------------
class TestAdminUserDetailEmailsSent:
    def test_user_detail_includes_emails_sent(self, headers):
        # pick any user from the signed_up bucket (guaranteed real user)
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/activation-funnel/stage-users",
            params={"stage": "signed_up"}, headers=headers, timeout=20,
        )
        users = r.json().get("users", [])
        if not users:
            pytest.skip("no real users in signed_up stage to inspect")
        uid = users[0]["user_id"]
        r2 = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/users/{uid}",
            headers=headers, timeout=20,
        )
        assert r2.status_code == 200, r2.text[:300]
        data = r2.json()
        # existing fields preserved
        for k in ("activity_timeline", "projects", "usage", "token_grants", "offers"):
            assert k in data, f"missing existing field {k}"
        # new field
        assert "emails_sent" in data, "missing emails_sent"
        assert isinstance(data["emails_sent"], list)
        for row in data["emails_sent"]:
            for k in ("stage", "sent_at", "sent_ok", "clicked_at", "click_count"):
                assert k in row, f"emails_sent row missing {k}: {row}"
