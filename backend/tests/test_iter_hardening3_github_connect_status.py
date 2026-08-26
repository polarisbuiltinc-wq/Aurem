"""
tests/test_iter_hardening3_github_connect_status.py — GitHub Connect
PERMANENT fix (2026-08).

GET /github/app/status is the ONE authoritative live-status endpoint
the investigation found missing (I4b). Self-healing short-TTL cache
(A1): fresh cached repos (<10s) are trusted; stale/empty ones trigger
a live GitHub re-fetch that self-heals a poisoned 0-repo row (I4) —
proven live against the real Preview installation in the founder
report; these are the automated regression equivalents.
"""
from __future__ import annotations

import os
import time

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

pytestmark = pytest.mark.asyncio


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


@pytest.fixture
async def db():
    d = _db()
    import cto_services.db as _dbmod
    _dbmod.set_db(d)
    yield d
    await d.github_installations.delete_many({"_test_run": True})


async def test_no_installation_returns_pending_not_error(db):
    from routers.github_app import connect_status

    async def _fake_current_dev(authorization=None):
        return {"user_id": "test_f2gh_no_install_user"}

    import routers.github_app as gha
    orig = gha.current_dev
    gha.current_dev = _fake_current_dev
    try:
        result = await connect_status(authorization="Bearer x")
    finally:
        gha.current_dev = orig

    assert result["installation_active"] is False
    assert result["state"] == "pending"
    assert result["error"] is None
    assert result["connected_repo"] is None


async def test_fresh_cache_is_trusted_no_live_call(db, monkeypatch):
    """T6 (rate-limit) — a <10s-old cached, non-empty repo list must
    NOT trigger a live GitHub call."""
    import routers.github_app as gha

    now = time.time()
    await db.github_installations.insert_one({
        "_test_run": True, "installation_id": 999001,
        "user_id": "test_f2gh_fresh", "active": True,
        "github_login": "acme",
        "repositories": [{"id": 1, "full_name": "acme/repo1", "private": True}],
        "updated_at": now - 2,  # 2s old — well within the 10s TTL
    })

    called = {"n": 0}
    async def _fake_list_repos(installation_id):
        called["n"] += 1
        return []
    monkeypatch.setattr(gha._ga, "list_installation_repos", _fake_list_repos)

    async def _fake_current_dev(authorization=None):
        return {"user_id": "test_f2gh_fresh"}
    monkeypatch.setattr(gha, "current_dev", _fake_current_dev)

    result = await gha.connect_status(authorization="Bearer x")

    assert called["n"] == 0, "fresh cache must not trigger a live GitHub call"
    assert result["state"] == "connected"
    assert result["connected_repo"] == "acme/repo1"


async def test_stale_or_poisoned_cache_self_heals_via_live_fetch(db, monkeypatch):
    """I4 — the exact bug found live: an installation cached with 0
    repos (a one-time fetch failure) must self-heal on the next poll
    via a live re-fetch, not stay poisoned forever."""
    import routers.github_app as gha

    await db.github_installations.insert_one({
        "_test_run": True, "installation_id": 999002,
        "user_id": "test_f2gh_poisoned", "active": True,
        "github_login": "acme",
        # No `repositories` key at all — exactly like the real poisoned
        # Preview row (installation 152797252) before this fix.
    })

    async def _fake_list_repos(installation_id):
        return [{"id": 2, "full_name": "acme/real-repo", "private": False,
                  "default_branch": "main"}]
    monkeypatch.setattr(gha._ga, "list_installation_repos", _fake_list_repos)

    async def _fake_current_dev(authorization=None):
        return {"user_id": "test_f2gh_poisoned"}
    monkeypatch.setattr(gha, "current_dev", _fake_current_dev)

    result = await gha.connect_status(authorization="Bearer x")

    assert result["state"] == "connected"
    assert result["connected_repo"] == "acme/real-repo"

    row = await db.github_installations.find_one({"installation_id": 999002})
    assert row["repositories"][0]["full_name"] == "acme/real-repo"
    assert row.get("updated_at") is not None


async def test_live_fetch_failure_does_not_long_cache_the_empty_result(db, monkeypatch):
    """A1 — on a live-fetch failure, do NOT persist/long-cache the
    empty result, so the NEXT poll retries immediately (self-healing),
    and surface state=error with a plain-language reason (no raw
    exception text)."""
    import routers.github_app as gha

    await db.github_installations.insert_one({
        "_test_run": True, "installation_id": 999003,
        "user_id": "test_f2gh_failing", "active": True,
        "github_login": "acme",
    })

    async def _fake_list_repos(installation_id):
        raise RuntimeError("simulated GitHub 500")
    monkeypatch.setattr(gha._ga, "list_installation_repos", _fake_list_repos)

    async def _fake_current_dev(authorization=None):
        return {"user_id": "test_f2gh_failing"}
    monkeypatch.setattr(gha, "current_dev", _fake_current_dev)

    result = await gha.connect_status(authorization="Bearer x")

    assert result["state"] == "error"
    assert result["error"] and "RuntimeError" not in result["error"]
    assert "simulated GitHub 500" not in (result["error"] or "")

    row = await db.github_installations.find_one({"installation_id": 999003})
    assert not row.get("updated_at")  # NOT long-cached — next poll retries


async def test_multiple_repos_leave_connected_repo_null_for_explicit_pick(db, monkeypatch):
    """When more than one repo is granted, `connected_repo` stays null
    so the frontend shows a real picker instead of guessing."""
    import routers.github_app as gha

    now = time.time()
    await db.github_installations.insert_one({
        "_test_run": True, "installation_id": 999004,
        "user_id": "test_f2gh_multi", "active": True,
        "github_login": "acme",
        "repositories": [
            {"id": 1, "full_name": "acme/repo1"},
            {"id": 2, "full_name": "acme/repo2"},
        ],
        "updated_at": now,
    })

    async def _fake_current_dev(authorization=None):
        return {"user_id": "test_f2gh_multi"}
    monkeypatch.setattr(gha, "current_dev", _fake_current_dev)

    result = await gha.connect_status(authorization="Bearer x")
    assert result["state"] == "connected"
    assert result["connected_repo"] is None
    assert len(result["repos"]) == 2
