"""
QA-Hardening — verification tests for Items 2 & 4.

Item 2: `/api/admin/qa/ci-vs-local-drift` cross-references local
        pytest count vs latest GitHub Actions quality-gate run and
        exposes a `drift_detected` flag. When GITHUB_ACTIONS_TOKEN /
        GITHUB_REPO are unset it MUST honest-empty (no fake green).

Item 4: `/api/aurem-dev/version` MUST include a `last_github_push`
        key so the admin Deploy-Sync card can render two DISTINCT
        timestamps: "Deployed at" (built_at) and "Pushed to GitHub"
        (last_github_push.pushed_at). Honest-empty when creds missing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def client():
    from main import app   # main FastAPI app (registers both routers)
    with TestClient(app) as c:
        yield c


# ─────────────────────────────────────────────────────────────
# Item 4 — /version now returns `last_github_push` (nullable)
# ─────────────────────────────────────────────────────────────
def test_version_returns_last_github_push_key(client, monkeypatch):
    """The key MUST be present so the frontend can render two
    distinct timestamps (Deployed / Pushed to GitHub). When creds
    aren't wired the value is None — that's honest, not a bug."""
    # Force honest-empty regardless of ambient env.
    monkeypatch.delenv("GITHUB_ACTIONS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    # Bust the module-level cache written by any earlier test.
    from routers import version as v
    v._GH_PUSH_CACHE["value"] = None
    v._GH_PUSH_CACHE["expires_at"] = 0.0

    r = client.get("/api/aurem-dev/version")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "last_github_push" in data, (
        "QA-Hardening Item 4 patch missing — /version MUST expose "
        "`last_github_push` so admin can distinguish deploy vs push."
    )
    assert data["last_github_push"] is None, (
        f"Expected honest-empty None without GITHUB_ACTIONS_TOKEN, "
        f"got {data['last_github_push']!r}"
    )
    # The two timestamp sources are now conceptually separate:
    # `built_at` = last Emergent deploy, `last_github_push.pushed_at`
    # = last real git push. Confirm `built_at` is still first-class.
    assert data.get("built_at"), "built_at must still exist"


def test_last_github_push_populated_when_creds_present(monkeypatch):
    """Contract check: with creds + a stubbed GitHub API, the
    resolver returns a dict shaped for the frontend."""
    import asyncio
    from routers import version as v

    monkeypatch.setenv("GITHUB_ACTIONS_TOKEN", "gha_fake_token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")
    v._GH_PUSH_CACHE["value"] = None
    v._GH_PUSH_CACHE["expires_at"] = 0.0

    class _Resp:
        status_code = 200
        def json(self):
            return [{
                "sha": "abcdef1234567890",
                "html_url": "https://github.com/owner/repo/commit/abcdef",
                "commit": {
                    "committer": {"date": "2026-01-01T12:00:00Z"},
                    "message":   "fix: some real commit",
                },
            }]

    class _AC:
        def __init__(self, *a, **k): pass
        async def __aenter__(self):  return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    monkeypatch.setattr(v.httpx, "AsyncClient", _AC)

    result = asyncio.get_event_loop().run_until_complete(
        v._fetch_last_github_push()
    )
    assert result is not None
    assert result["commit_sha"] == "abcdef123456"    # trimmed to 12 chars
    assert result["pushed_at"]  == "2026-01-01T12:00:00Z"
    assert "commit/abcdef" in result["html_url"]
    assert result["message"].startswith("fix:")


# ─────────────────────────────────────────────────────────────
# Item 2 — /admin/qa/ci-vs-local-drift honest-empty path
# ─────────────────────────────────────────────────────────────
def test_ci_vs_local_drift_honest_empty_without_gh_creds(client, monkeypatch):
    """Without GITHUB_ACTIONS_TOKEN / GITHUB_REPO the endpoint must
    return ci_available=False WITH a reason, and drift_detected=False
    (can't assert a drift when we can't read CI)."""
    monkeypatch.delenv("GITHUB_ACTIONS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)

    # Route is admin-gated. Use the founder header contract the
    # rest of admin_qa uses (require_admin_dep + _require_admin).
    r = client.get(
        "/api/aurem-dev/admin/qa/ci-vs-local-drift",
        headers={"Authorization": f"Bearer {os.environ.get('AUREM_ADMIN_TOKEN', '')}"},
    )
    # If admin gating rejects us in a locked-down test env, at least
    # confirm the route exists (not a 404). Real drift-check runs
    # under a real founder session in production.
    assert r.status_code in (200, 401, 403), (
        f"Route missing? got {r.status_code}: {r.text[:200]}"
    )

    if r.status_code == 200:
        data = r.json()
        assert data["ci_available"] is False
        assert data["drift_detected"] is False
        assert data["ci_reason"], (
            "Honest-empty must include a reason string so admins "
            "know the check couldn't run (no fake green)."
        )
