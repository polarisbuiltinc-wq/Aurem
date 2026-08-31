"""
tests/test_admin_connect_diagnostic_2026_09_01.py

Connect-flow investigation — the read-only diagnostic endpoint
(`GET /admin/connect-diagnostic/{user_id}`) must return exactly the
state named in the brief: install row(s), project rows, oauth state
issued-vs-used, funnel events, and one ordered combined event stream
(funnel + webhook, sorted by timestamp). Read-only: asserts no write
methods are ever invoked on the fake db.
"""
import time

import pytest

import routers.admin_connect_diagnostic as diag


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    async def to_list(self, length=None):
        return self._rows


class _Coll:
    def __init__(self, rows):
        self._rows = rows

    def find(self, query):
        def _match(row):
            for k, v in query.items():
                if isinstance(v, dict) and "$in" in v:
                    if row.get(k) not in v["$in"]:
                        return False
                elif row.get(k) != v:
                    return False
            return True
        return _Cursor([r for r in self._rows if _match(r)])

    async def insert_one(self, *a, **k):
        raise AssertionError("diagnostic endpoint must never write")

    async def update_one(self, *a, **k):
        raise AssertionError("diagnostic endpoint must never write")

    async def delete_one(self, *a, **k):
        raise AssertionError("diagnostic endpoint must never write")


class _FakeDB:
    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, name):
        return _Coll(self._data.get(name, []))


@pytest.fixture(autouse=True)
def _stub_admin(monkeypatch):
    async def _fake_require_admin(authorization):
        return {"email": "founder@aurem.dev", "user_id": "admin1"}
    monkeypatch.setattr(diag, "require_admin", _fake_require_admin)


@pytest.mark.asyncio
async def test_diagnostic_returns_all_named_state(monkeypatch):
    now = time.time()
    data = {
        "github_installations": [
            {"installation_id": 9001, "user_id": "u_mike", "github_login": "mikef",
             "active": True, "installed_at": now - 1000, "linked_at": now - 999,
             "updated_at": now - 500, "repositories": [{"id": 1, "full_name": "mikef/site"}]},
        ],
        "cto_projects": [],
        "oauth_states": [
            {"state": "gha:u_mike:abcdef1234567890", "kind": "github_app_install",
             "user_id": "u_mike", "created_at": now - 1000,
             "expires_at": now - 1000 + 900, "used": True, "used_at": now - 998},
        ],
        "funnel_events": [],
        "github_funnel_events": [
            {"stage": "app_install_granted", "origin": "server", "source": "wizard",
             "event_id": "e1", "user_id": "u_mike", "meta": {"installation_id": 9001}, "ts": now - 998},
            {"stage": "app_install_denied", "origin": "client", "source": "wizard",
             "event_id": "e2", "user_id": "u_mike", "meta": {"reason": "popup_closed"}, "ts": now - 900},
        ],
        "webhook_deliveries": [
            {"_id": "dlv1", "event": "installation", "action": "created",
             "installation": 9001, "received_at": now - 999},
        ],
    }
    monkeypatch.setattr(diag, "require_db", lambda: _FakeDB(data))

    out = await diag.connect_flow_diagnostic(user_id="u_mike", authorization="Bearer x")

    assert out["user_id"] == "u_mike"
    assert len(out["github_installations"]) == 1
    assert out["github_installations"][0]["user_id"] == "u_mike"
    assert out["github_installations"][0]["repo_full_names"] == ["mikef/site"]
    assert out["projects"]["count"] == 0
    assert out["oauth_states"][0]["used"] is True
    assert out["oauth_states"][0]["issued_at"] is not None

    stream = out["ordered_event_stream"]
    # 3 events total: install-created webhook, app_install_granted, app_install_denied
    assert len(stream) == 3
    kinds = [e["event_type"] for e in stream]
    assert kinds == ["installation", "app_install_granted", "app_install_denied"]


@pytest.mark.asyncio
async def test_diagnostic_is_read_only_and_scoped(monkeypatch):
    """No installations/projects for this user_id -> everything empty,
    not an error, and the fake db's write methods (which raise) are
    never triggered."""
    monkeypatch.setattr(diag, "require_db", lambda: _FakeDB({}))
    out = await diag.connect_flow_diagnostic(user_id="nobody", authorization="Bearer x")
    assert out["github_installations"] == []
    assert out["projects"]["count"] == 0
    assert out["ordered_event_stream"] == []
