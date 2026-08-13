"""
Iter 388x — Deploy Insights Panel · Option B fix regression tests.

Bug: /api/health surfaced `build_hash` + `built_at` from the legacy
cascade (BUILD_INFO.txt / emergent.yml.created_at / .build_info mtime /
START_TIME).  Prod containers strip .git, BUILD_INFO.txt is gitignored
so it doesn't travel with the snapshot, and emergent.yml.created_at is
job-creation time not deploy time — so all three legacy sources lagged
behind actual deploys.

Fix: at boot, cache the deploy_event row (populated by
services.deploy_logger.log_deploy_event) on app.state.deploy_event.
/api/health prefers those values (real commit_sha + real boot ISO
timestamp) and only falls back to the legacy resolvers if the state
hasn't hydrated yet (first-boot race).

These tests verify the CONTRACT: when app.state.deploy_event is set,
/api/health returns those values; when unset, it falls back cleanly.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_prefers_deploy_event_when_set(monkeypatch):
    """When app.state.deploy_event is populated, /api/health surfaces
    those exact values (not the legacy _resolve_* fallbacks)."""
    from main import app

    # Lifespan startup didn't run in test context — stub app.state.db
    # so /api/health doesn't 500 on the `db: app.state.db is not None`
    # line.
    app.state.db = None
    app.state.deploy_event = {
        "commit_sha":  "abc123deadbee",
        "commit_msg":  "test commit",
        "commit_ts":   "2026-08-13T05:00:00+00:00",
        "boot_ts_iso": "2026-08-13T05:07:42.216771+00:00",
        "boot_id":     "boot_x",
        "trigger":     "boot",
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as ac:
            r = await ac.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        # commit_sha is truncated to 12 chars in the cache
        assert body["build_hash"] == "abc123deadbee"
        assert body["built_at"] == "2026-08-13T05:07:42.216771+00:00"
    finally:
        # Clean up so we don't pollute other tests.
        if hasattr(app.state, "deploy_event"):
            del app.state.deploy_event


@pytest.mark.asyncio
async def test_health_falls_back_to_legacy_when_deploy_event_missing(monkeypatch):
    """When app.state.deploy_event is unset, /api/health uses the
    legacy _resolve_build_hash / _resolve_built_at cascade."""
    from main import app
    import main as _main

    # Ensure state is clear.
    app.state.db = None
    if hasattr(app.state, "deploy_event"):
        del app.state.deploy_event

    monkeypatch.setattr(_main, "_resolve_build_hash", lambda: "LEGACY_SHA_7c")
    monkeypatch.setattr(_main, "_resolve_built_at",
                         lambda: "2026-01-01T00:00:00+00:00")

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["build_hash"] == "LEGACY_SHA_7c"
    assert body["built_at"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_health_falls_back_when_deploy_event_lacks_commit(monkeypatch):
    """Defense-in-depth: if deploy_event dict exists but its
    commit_sha is empty/None, still fall back to the legacy resolver
    rather than surface an empty string as the build hash."""
    from main import app
    import main as _main

    app.state.db = None
    app.state.deploy_event = {
        "commit_sha": "",
        "boot_ts_iso": "",
        "boot_id": "boot_y",
    }
    monkeypatch.setattr(_main, "_resolve_build_hash", lambda: "FALLBACK_SHA")
    monkeypatch.setattr(_main, "_resolve_built_at",
                         lambda: "2026-06-01T00:00:00+00:00")

    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as ac:
            r = await ac.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["build_hash"] == "FALLBACK_SHA"
        assert body["built_at"] == "2026-06-01T00:00:00+00:00"
    finally:
        del app.state.deploy_event
