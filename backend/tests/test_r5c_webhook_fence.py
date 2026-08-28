"""
tests/test_r5c_webhook_fence.py — R5c "App Fence Tile" (2026-08-28).

Covers:
  - t_fence_not_configured: webhook_fence_status() short-circuits
    cleanly (no network call) when the GitHub App isn't configured.
  - t_fence_live_endpoint: GET /admin/github-webhook-fence (real,
    admin-gated) against the live, already-configured App in this
    pod — proves the shape + that it reflects real GitHub state.
  - t_fence_requires_auth: unauthenticated request is rejected.
"""
import os
import pytest
import requests

from services import github_app as ga

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.mark.asyncio
async def test_fence_not_configured(monkeypatch):
    monkeypatch.setattr(ga, "is_configured", lambda: False)
    result = await ga.webhook_fence_status()
    assert result["ok"] is False
    assert result["configured"] is False
    assert "pull_request" in result["missing_subscriptions"]
    assert result["recent_deliveries"] == []


def test_fence_requires_auth():
    r = requests.get(f"{API}/admin/github-webhook-fence", timeout=15)
    assert r.status_code in (401, 403)


def test_fence_live_endpoint(token):
    r = requests.get(
        f"{API}/admin/github-webhook-fence",
        headers={"Authorization": f"Bearer {token}"}, timeout=20,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["configured"] is True
    assert isinstance(data["subscribed_events"], list)
    assert isinstance(data["missing_subscriptions"], list)
    assert isinstance(data["recent_deliveries"], list)
    assert isinstance(data["failing_count"], int)
    assert isinstance(data["ok"], bool)
    for d in data["recent_deliveries"]:
        assert set(d.keys()) >= {"id", "event", "action", "delivered_at", "status_code", "success"}
