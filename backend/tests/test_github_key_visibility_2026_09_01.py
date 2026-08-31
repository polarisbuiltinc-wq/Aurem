"""
tests/test_github_key_visibility_2026_09_01.py

NEXT ROUND Item 1 — make the GitHub App key's live state VISIBLE so
the "did my paste take?" confusion stops. Tests the three named
acceptance checks against the real router handlers in
routers/admin_ops_config.py (direct async-function calls, no
TestClient — same style as other lightweight router unit tests):

  t_key_state_shown_in_admin — GET /admin/github-app-config returns
    a `key_state` of MISSING / VALID / STALE-ON-GITHUB, computed from
    `configured` + the live probe, not a saved field.
  t_paste_failure_shows_reason — POST with a key GitHub rejects
    returns a non-empty, ACTUAL reason string (never a silent no-op).
  t_key_state_not_stale — two consecutive GETs with a live probe that
    flips ok True→False produce DIFFERENT key_state values — proving
    it's recomputed live every call, not cached.
"""
import pytest

import routers.admin_ops_config as ops_config


class _FakeDB:
    def __init__(self, row):
        self._row = row

    class _Coll:
        def __init__(self, row):
            self._row = row

        async def find_one(self, query):
            return dict(self._row) if self._row else None

        async def update_one(self, query, update, upsert=False):
            return None

    @property
    def admin_settings(self):
        return _FakeDB._Coll(self._row)


@pytest.fixture(autouse=True)
def _stub_admin(monkeypatch):
    async def _fake_require_admin(authorization):
        return {"email": "founder@aurem.dev", "user_id": "u1"}
    monkeypatch.setattr(ops_config, "_require_admin", _fake_require_admin)


@pytest.mark.asyncio
async def test_t_key_state_shown_in_admin_missing(monkeypatch):
    monkeypatch.setattr(ops_config, "require_db", lambda: _FakeDB(None))
    from services import github_app_config as gac
    monkeypatch.setattr(gac, "_RUNTIME_GITHUB_APP", {})

    out = await ops_config.admin_get_github_app_config(authorization="Bearer x")
    assert out["configured"] is False
    assert out["key_state"] == "MISSING"


@pytest.mark.asyncio
async def test_t_key_state_shown_in_admin_valid(monkeypatch):
    row = {"app_id": "123", "app_slug": "aurem-devops", "private_key": "PEM",
           "webhook_secret": "whsec", "updated_at": 1787000000.0,
           "updated_by": "founder@aurem.dev"}
    monkeypatch.setattr(ops_config, "require_db", lambda: _FakeDB(row))
    from services import github_app_config as gac
    gac.set_runtime_github_app_config(row)

    async def _probe_ok(app_id, pem):
        return {"ok": True, "app_id": 123, "app_slug": "aurem-devops"}
    monkeypatch.setattr(ops_config, "_github_app_live_probe", _probe_ok)

    out = await ops_config.admin_get_github_app_config(authorization="Bearer x")
    assert out["configured"] is True
    assert out["key_state"] == "VALID"
    assert out["last_updated"] == 1787000000.0


@pytest.mark.asyncio
async def test_t_key_state_not_stale(monkeypatch):
    """Same stored row, but the live probe result flips between the
    two calls — key_state MUST follow the live probe, not a cached
    value, proving it's recomputed fresh every GET."""
    row = {"app_id": "123", "app_slug": "aurem-devops", "private_key": "PEM",
           "webhook_secret": "whsec", "updated_at": 1787000000.0,
           "updated_by": "founder@aurem.dev"}
    monkeypatch.setattr(ops_config, "require_db", lambda: _FakeDB(row))
    from services import github_app_config as gac
    gac.set_runtime_github_app_config(row)

    probe_results = [
        {"ok": False, "error": "GitHub returned 401 — App ID and private key do not match."},
        {"ok": True, "app_id": 123, "app_slug": "aurem-devops"},
    ]

    async def _probe_sequenced(app_id, pem):
        return probe_results.pop(0)
    monkeypatch.setattr(ops_config, "_github_app_live_probe", _probe_sequenced)

    first = await ops_config.admin_get_github_app_config(authorization="Bearer x")
    second = await ops_config.admin_get_github_app_config(authorization="Bearer x")

    assert first["key_state"] == "STALE-ON-GITHUB"
    assert second["key_state"] == "VALID"
    assert first["key_state"] != second["key_state"]


@pytest.mark.asyncio
async def test_t_paste_failure_shows_reason(monkeypatch):
    """POST with a key GitHub rejects must raise with the ACTUAL
    GitHub error text, never a bare 'save failed' / silent no-op."""
    async def _probe_rejects(app_id, pem):
        return {"ok": False, "error": "GitHub returned 401 — App ID and private key do not match."}
    monkeypatch.setattr(ops_config, "_github_app_live_probe", _probe_rejects)

    body = ops_config.GitHubAppConfigBody(
        app_id="123", app_slug="aurem-devops",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
        webhook_secret="whsecwhsec",
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await ops_config.admin_set_github_app_config(body, authorization="Bearer x")

    detail = exc_info.value.detail
    assert detail["error"] == "github_probe_failed"
    assert "401" in detail["message"]
    assert "App ID and private key do not match" in detail["message"]
