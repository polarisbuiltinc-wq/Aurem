"""
tests/guardrails/test_admin_endpoints_require_admin.py — verification
requested by the founder (2026-08-28, post-Wave-1 report): confirm
GET and POST /admin/guardrails are require_admin_dep guarded, with a
named dynamic test for the non-admin 403 (not just the existing static
source-scan in test_iter358_admin_auth_hardening.py).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import admin_ops_config


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(admin_ops_config.router, prefix="/api/aurem-dev")

    async def _fail_require_admin(authorization):
        from fastapi import HTTPException
        raise HTTPException(403, "Admin access required")

    # Router-level require_admin_dep IS the gate under test — don't
    # stub it out. Only stub the DB so a hypothetically-admin caller
    # wouldn't crash on a missing Mongo connection (never reached here).
    monkeypatch.setattr(admin_ops_config, "require_db", lambda: None)
    return TestClient(app)


def test_get_guardrails_rejects_non_admin(client):
    r = client.get("/api/aurem-dev/admin/guardrails", headers={"Authorization": "Bearer not-an-admin-token"})
    assert r.status_code in (401, 403)


def test_get_guardrails_rejects_missing_auth(client):
    r = client.get("/api/aurem-dev/admin/guardrails")
    assert r.status_code in (401, 403)


def test_post_guardrail_mode_rejects_non_admin(client):
    r = client.post(
        "/api/aurem-dev/admin/guardrails/path_guard/mode",
        json={"mode": "block"},
        headers={"Authorization": "Bearer not-an-admin-token"},
    )
    assert r.status_code in (401, 403)


def test_post_guardrail_mode_rejects_missing_auth(client):
    r = client.post("/api/aurem-dev/admin/guardrails/path_guard/mode", json={"mode": "block"})
    assert r.status_code in (401, 403)
