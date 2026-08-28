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

import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def client():
    from main import app   # main FastAPI app (registers both routers)

    # Item 2's endpoint sits behind `require_admin_dep` (router-level)
    # + `_require_admin(authorization)` (per-route). Without a real
    # founder token, TestClient hits 401/403 and the honest-empty
    # assertions never run — that's the "tautological test" review
    # finding. Override BOTH gates so the 200-branch is guaranteed
    # to execute and the actual behaviour is asserted.
    #
    # NOTE: FastAPI inspects the OVERRIDE function's signature to
    # rebuild dependencies for the request. If we used `(*a, **kw)`
    # FastAPI would treat `a`/`kw` as required query params and
    # return 422. The override MUST mirror the original signature.
    from fastapi import Header
    from cto_services import auth as _auth_mod
    from routers import admin_qa as _admin_qa_mod

    async def _ok_admin_dep(authorization: str = Header(default=None)):
        return {"user_id": "test-admin", "is_admin": True}

    async def _ok_require_admin(authorization=None):
        # Module-level helper (not a FastAPI dep) — plain call
        # signature matching the real `_require_admin(authorization)`.
        return {"user_id": "test-admin", "is_admin": True}

    app.dependency_overrides[_auth_mod.require_admin_dep] = _ok_admin_dep
    _orig = _admin_qa_mod._require_admin
    _admin_qa_mod._require_admin = _ok_require_admin
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(_auth_mod.require_admin_dep, None)
        _admin_qa_mod._require_admin = _orig


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
    # Iter arch-2a — cache now lives in services/github_last_push.py
    # (relocated out of the router to fix a boundary violation).
    from services import github_last_push as glp
    glp._GH_PUSH_CACHE["value"] = None
    glp._GH_PUSH_CACHE["expires_at"] = 0.0

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
    resolver returns a dict shaped for the frontend.

    Iter arch-2a (2026-08-22) — resolver + cache relocated verbatim
    to services/github_last_push.py (was a router→raw-httpx boundary
    violation). Same contract, corrected ownership."""
    from services import github_last_push as glp

    monkeypatch.setenv("GITHUB_ACTIONS_TOKEN", "gha_fake_token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")
    glp._GH_PUSH_CACHE["value"] = None
    glp._GH_PUSH_CACHE["expires_at"] = 0.0

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

    monkeypatch.setattr(glp.httpx, "AsyncClient", _AC)

    result = asyncio.run(glp.fetch_last_github_push())
    assert result is not None
    assert result["commit_sha"] == "abcdef123456"    # trimmed to 12 chars
    assert result["pushed_at"]  == "2026-01-01T12:00:00Z"
    # Public /version payload MUST NOT include commit message or URL —
    # would leak private-repo context to unauthenticated visitors.
    assert "message" not in result, (
        "commit message must not appear in the public /version payload"
    )
    assert "html_url" not in result, (
        "commit html_url must not appear in the public /version payload"
    )


# ─────────────────────────────────────────────────────────────
# Item 2 — /admin/qa/ci-vs-local-drift honest-empty path
# ─────────────────────────────────────────────────────────────
def test_ci_vs_local_drift_honest_empty_without_gh_creds(client, monkeypatch):
    """Without GITHUB_ACTIONS_TOKEN / GITHUB_REPO the endpoint must
    return ci_available=False WITH a reason, and drift_detected=False
    (can't assert a drift when we can't read CI).

    Both admin gates are stubbed in the client fixture (see review
    finding: previously this test never reached the 200 branch)."""
    monkeypatch.delenv("GITHUB_ACTIONS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)

    r = client.get("/api/aurem-dev/admin/qa/ci-vs-local-drift")
    assert r.status_code == 200, (
        f"Admin gate override did not land — got {r.status_code}: "
        f"{r.text[:300]}"
    )
    data = r.json()
    assert data["ci_available"] is False, (
        f"Without GITHUB_ACTIONS_TOKEN, ci_available must be False; "
        f"got {data['ci_available']!r}"
    )
    assert data["drift_detected"] is False, (
        f"Cannot assert drift when CI is unreachable; "
        f"got drift_detected={data['drift_detected']!r}"
    )
    assert data["ci_reason"], (
        "Honest-empty must include a reason string so admins "
        "know the check couldn't run (no fake green)."
    )
    # Key surface for the frontend banner:
    for key in (
        "ci_conclusions", "ci_any_failure", "ci_all_success",
        "local_grand_total_tests", "local_source", "drift_reason",
    ):
        assert key in data, f"drift payload missing key: {key}"
