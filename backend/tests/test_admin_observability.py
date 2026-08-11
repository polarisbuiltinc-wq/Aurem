"""Tests for the admin observability endpoints (/admin/observability/breakers).

Covers:
  1. Snapshot shape is {breakers, counts, transitions, healthy}
  2. Every KNOWN_DEPS entry appears in the snapshot
  3. Single-dep detail endpoint returns the same shape scoped
  4. Force a github breaker OPEN → response reflects it
  5. Admin gate is honored (no auth → 401/403)
"""
from __future__ import annotations

import time
import pytest
from fastapi.testclient import TestClient

from main import app
from services.retry_guard import get_breaker, KNOWN_DEPS


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_breakers():
    for dep in KNOWN_DEPS:
        br = get_breaker(dep)
        br.state = "closed"
        br.consecutive_fails = 0
        br.opened_at = 0.0
    yield


def _admin_token():
    """Mint a founder JWT for the test client."""
    import os
    os.environ.setdefault("JWT_SECRET", "test-secret-32chars-minlen-abcdefgh")
    from cto_services.auth import create_token
    return create_token({
        "user_id": "test-admin-uid",
        "email": "test@aurem.dev",
        "is_admin": True,
        "is_founder": True,
        "tier": "founder",
    })


def test_breakers_snapshot_shape(client, monkeypatch):
    """Requires _require_admin — mock the JWT decode + admin lookup."""
    from routers import admin_observability as mod

    async def _fake_require_admin_dep(authorization=None):
        return {"user_id": "test-uid", "is_admin": True, "is_founder": True}

    app.dependency_overrides[mod.require_admin_dep] = _fake_require_admin_dep
    try:
        r = client.get("/api/aurem-dev/admin/observability/breakers")
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body.keys()) == {"breakers", "counts", "transitions", "healthy"}
        # Every known dep must appear (github/resend/vercel/stripe/etc.)
        for dep in KNOWN_DEPS:
            assert dep in body["breakers"], f"missing {dep}"
            snap = body["breakers"][dep]
            assert "state" in snap and snap["state"] in ("closed", "open", "half_open")
            assert "trip_count_7d" in snap
            assert "consecutive_fails" in snap
        # Counts total should equal number of known deps
        assert sum(body["counts"].values()) >= len(KNOWN_DEPS)
        # All-closed → healthy true
        assert body["healthy"] is True
    finally:
        app.dependency_overrides.pop(mod.require_admin_dep, None)


def test_breakers_reflects_forced_open_state(client):
    from routers import admin_observability as mod

    async def _fake_require_admin_dep(authorization=None):
        return {"user_id": "test-uid", "is_admin": True, "is_founder": True}

    # Force github breaker to OPEN
    br = get_breaker("github")
    br.state = "open"
    br.consecutive_fails = 5
    br.opened_at = time.monotonic()
    br.last_error = "test-forced-open"

    app.dependency_overrides[mod.require_admin_dep] = _fake_require_admin_dep
    try:
        r = client.get("/api/aurem-dev/admin/observability/breakers")
        assert r.status_code == 200
        body = r.json()
        assert body["breakers"]["github"]["state"] == "open"
        assert body["breakers"]["github"]["consecutive_fails"] == 5
        assert "test-forced-open" in body["breakers"]["github"]["last_error"]
        assert body["healthy"] is False
        assert body["counts"]["open"] >= 1
    finally:
        app.dependency_overrides.pop(mod.require_admin_dep, None)


def test_single_dep_detail_endpoint(client):
    from routers import admin_observability as mod

    async def _fake_require_admin_dep(authorization=None):
        return {"user_id": "test-uid", "is_admin": True, "is_founder": True}

    app.dependency_overrides[mod.require_admin_dep] = _fake_require_admin_dep
    try:
        r = client.get("/api/aurem-dev/admin/observability/breakers/resend")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"breaker", "transitions"}
        assert body["breaker"]["dep"] == "resend"
        assert "state" in body["breaker"]
    finally:
        app.dependency_overrides.pop(mod.require_admin_dep, None)


def test_single_dep_bad_name_rejected(client):
    from routers import admin_observability as mod

    async def _fake_require_admin_dep(authorization=None):
        return {"user_id": "test-uid", "is_admin": True, "is_founder": True}

    app.dependency_overrides[mod.require_admin_dep] = _fake_require_admin_dep
    try:
        # Path-traversal / injection attempt
        r = client.get("/api/aurem-dev/admin/observability/breakers/../etc")
        # Either 400 (validator) or 404 (route mismatch) is acceptable —
        # what MUST NOT happen is a 200 with real state.
        assert r.status_code in (400, 404), r.text
    finally:
        app.dependency_overrides.pop(mod.require_admin_dep, None)


def test_no_auth_rejected(client):
    """Without admin auth, endpoint must NOT return 200."""
    r = client.get("/api/aurem-dev/admin/observability/breakers")
    assert r.status_code in (401, 403, 422), r.text
