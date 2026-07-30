"""Iter 357 locks — Guard 8 (partial): GitHub sync detection.

Charter (founder): reuse the EXISTING build badge + alerts banner.
ONE check (services/github_sync.py), shown on Overview badge now and
the /admin/qa guards row later. >48h gap → RED in topup_alerts banner.
"""
import os
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend" / "src"


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Async httpx.AsyncClient stand-in keyed by URL suffix."""
    routes = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        for suffix, resp in self.routes.items():
            if url.endswith(suffix):
                return resp
        return _FakeResp(404, {})


def _fresh():
    import services.github_sync as gs
    gs._CACHE.update(ts=0.0, data=None)
    return gs


@pytest.mark.asyncio
async def test_not_wired_without_env(monkeypatch):
    gs = _fresh()
    monkeypatch.delenv("GITHUB_ACTIONS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    r = await gs.get_github_sync("abc123abc123", "2026-06-30T00:00:00+00:00")
    assert r["status"] == "not_wired"
    assert r["critical"] is False


@pytest.mark.asyncio
async def test_in_sync_when_head_matches_build(monkeypatch):
    gs = _fresh()
    monkeypatch.setenv("GITHUB_ACTIONS_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_REPO", "org/repo")
    _FakeClient.routes = {
        "/commits/main": _FakeResp(200, {
            "sha": "abc123abc123ffffffff",
            "commit": {"committer": {"date": "2026-06-29T00:00:00Z"}},
        }),
    }
    monkeypatch.setattr(gs.httpx, "AsyncClient", _FakeClient)
    r = await gs.get_github_sync("abc123abc123", "2026-06-30T00:00:00+00:00")
    assert r["status"] == "in_sync"
    assert r["critical"] is False


@pytest.mark.asyncio
async def test_behind_and_critical_after_48h(monkeypatch):
    gs = _fresh()
    monkeypatch.setenv("GITHUB_ACTIONS_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_REPO", "org/repo")
    _FakeClient.routes = {
        "/commits/main": _FakeResp(200, {
            "sha": "1111111111111111",
            "commit": {"committer": {"date": "2026-06-20T00:00:00Z"}},
        }),
        # build sha NOT on GitHub → 404 (default)
    }
    monkeypatch.setattr(gs.httpx, "AsyncClient", _FakeClient)
    r = await gs.get_github_sync("deadbeef1234", "2026-06-30T00:00:00+00:00")
    assert r["status"] == "behind"
    assert r["gap_hours"] and r["gap_hours"] > 48
    assert r["critical"] is True


@pytest.mark.asyncio
async def test_github_ahead_counts_as_in_sync(monkeypatch):
    gs = _fresh()
    monkeypatch.setenv("GITHUB_ACTIONS_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_REPO", "org/repo")
    _FakeClient.routes = {
        "/commits/main": _FakeResp(200, {
            "sha": "2222222222222222",
            "commit": {"committer": {"date": "2026-06-30T00:00:00Z"}},
        }),
        "/commits/deadbeef1234": _FakeResp(200, {"sha": "deadbeef1234ffff"}),
        "/compare/deadbeef1234...2222222222222222": _FakeResp(200, {
            "status": "ahead", "ahead_by": 3}),
    }
    monkeypatch.setattr(gs.httpx, "AsyncClient", _FakeClient)
    r = await gs.get_github_sync("deadbeef1234", "2026-06-30T00:00:00+00:00")
    assert r["status"] == "in_sync"
    assert r.get("github_ahead_by") == 3


@pytest.mark.asyncio
async def test_alert_escalation_and_auto_resolve():
    """Critical behind → row in topup_alerts (existing banner engine);
    in_sync → auto-resolved. Real preview Mongo."""
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")
    from motor.motor_asyncio import AsyncIOMotorClient
    import services.github_sync as gs

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        await db.topup_alerts.delete_many({"integration_id": "github_sync"})
        await gs._sync_alert(db, {"status": "behind", "critical": True,
                                  "build_sha": "deadbeef1234",
                                  "commits_behind": 7, "gap_hours": 72.0})
        row = await db.topup_alerts.find_one(
            {"integration_id": "github_sync", "status": "active"})
        assert row, "critical behind must create an active alert"
        assert row["severity"] == "critical"
        assert "7 commits" in row["summary"]
        # duplicate escalation same day → update, not second row
        await gs._sync_alert(db, {"status": "behind", "critical": True,
                                  "build_sha": "deadbeef1234",
                                  "commits_behind": 8, "gap_hours": 73.0})
        n = await db.topup_alerts.count_documents(
            {"integration_id": "github_sync", "status": "active"})
        assert n == 1
        # back in sync → auto-resolve
        await gs._sync_alert(db, {"status": "in_sync", "critical": False})
        n = await db.topup_alerts.count_documents(
            {"integration_id": "github_sync", "status": "active"})
        assert n == 0
    finally:
        await db.topup_alerts.delete_many({"integration_id": "github_sync"})
        client.close()


def test_cache_prevents_hammering(monkeypatch):
    import services.github_sync as gs
    assert gs.CACHE_TTL_S >= 300, "GitHub API must not be hit per page load"


def test_overview_badge_wired():
    src = (FRONTEND / "pages" / "AdminOverview.jsx").read_text()
    assert '"/admin/github-sync"' in src
    assert "github-sync-badge" in src
    assert "commits behind" in src
    # rendered inside the EXISTING build banner (founder charter:
    # extend, don't build a new surface)
    banner = src.split('data-testid="admin-build-banner"')[1].split("</div>")[0]
    assert "github-sync-badge" in banner


def test_endpoint_admin_gated():
    src = (BACKEND / "routers" / "admin.py").read_text()
    import re
    m = re.search(r"async def github_sync_status.*?(?=\n@router)", src, re.S)
    assert m and "_require_admin" in m.group(0)
