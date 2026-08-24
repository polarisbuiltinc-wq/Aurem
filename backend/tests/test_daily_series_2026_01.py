"""Test new daily_series field on cost-alert endpoint (2026-01 batch 2)."""

import os
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_token():
    s = requests.Session()
    for path in ["/api/auth/login", "/api/aurem-dev/auth/login"]:
        r = s.post(f"{BASE_URL}{path}",
                   json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                   timeout=15)
        if r.status_code == 200:
            data = r.json()
            tok = data.get("token") or data.get("access_token") or data.get("jwt")
            if tok:
                return tok
    pytest.skip(f"Admin login failed: last status {r.status_code}")


def test_daily_series_shape(admin_token):
    r = requests.get(
        f"{BASE_URL}/api/aurem-dev/admin/insights/cost-alert",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=30,
    )
    assert r.status_code == 200, f"status={r.status_code} body={r.text[:400]}"
    data = r.json()
    assert "daily_series" in data, "daily_series field missing"
    ds = data["daily_series"]
    assert isinstance(ds, list), "daily_series must be list"
    assert len(ds) == 30, f"expected 30 entries got {len(ds)}"
    for i, entry in enumerate(ds):
        assert isinstance(entry, dict), f"entry {i} not dict"
        assert "day" in entry, f"entry {i} missing day"
        assert "cost" in entry, f"entry {i} missing cost"
        assert "revenue" in entry, f"entry {i} missing revenue"
        assert isinstance(entry["cost"], (int, float)), f"entry {i} cost not numeric"
        assert isinstance(entry["revenue"], (int, float)), f"entry {i} revenue not numeric"


def test_email_still_off(admin_token):
    r = requests.get(
        f"{BASE_URL}/api/aurem-dev/admin/insights/cost-alert",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=30,
    )
    assert r.status_code == 200
    assert r.json().get("email_enabled") is False, "email must stay OFF"
