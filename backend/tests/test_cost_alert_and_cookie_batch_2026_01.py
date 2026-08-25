"""Test batch for: Cookie consent admin suppression + Admin QA skeleton +
Live Cost Alert endpoint (2026-01 cosmetic polish batch)."""

import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_token():
    s = requests.Session()
    # Try common login endpoints
    for path in ["/api/auth/login", "/api/aurem-dev/auth/login"]:
        r = s.post(f"{BASE_URL}{path}",
                   json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                   timeout=15)
        if r.status_code == 200:
            data = r.json()
            tok = data.get("token") or data.get("access_token") or data.get("jwt")
            if tok:
                return tok
    pytest.skip(f"Admin login failed via known paths: last status {r.status_code}")


class TestCostAlertEndpoint:
    """GET /api/aurem-dev/admin/insights/cost-alert"""

    ENDPOINT = "/api/aurem-dev/admin/insights/cost-alert"

    def test_requires_auth(self):
        r = requests.get(f"{BASE_URL}{self.ENDPOINT}", timeout=15)
        assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"

    def test_response_shape(self, admin_token):
        r = requests.get(
            f"{BASE_URL}{self.ENDPOINT}",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=30,
        )
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:400]}"
        data = r.json()
        required = [
            "period_days", "ai_cost_total", "revenue_total",
            "aggregate_breach", "paying_customers", "offenders",
            "offenders_count", "email_enabled", "recent_findings",
        ]
        for f in required:
            assert f in data, f"missing field: {f}"
        assert isinstance(data["offenders"], list)
        assert isinstance(data["recent_findings"], list)
        assert isinstance(data["aggregate_breach"], bool)
        assert data["email_enabled"] is False, "email must default OFF in preview"
        assert isinstance(data["ai_cost_total"], (int, float))
        assert isinstance(data["revenue_total"], (int, float))


class TestSLORegression:
    """Ensure SLO endpoint still works (Promise.allSettled sibling)."""

    def test_slo_endpoint(self, admin_token):
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/slo",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=30,
        )
        assert r.status_code == 200, f"status={r.status_code}"
