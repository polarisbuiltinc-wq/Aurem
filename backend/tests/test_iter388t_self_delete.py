"""
Iter 388t — GDPR/DSAR self-serve account deletion regression tests.

Bug: no self-serve delete existed.  Legal compliance risk (GDPR right
to be forgotten).  Admin cascade at admin_users.py:730-747 was also
missing 5 collections + Stripe subscription cancel + GitHub App
revocation — silently failing to fully wipe the user.

Fix: services/user_deletion.py::cascade_delete_user_data() shared
helper called by BOTH self-serve (POST /auth/delete-me) and admin
(DELETE /admin/users/{id}).  Cancels Stripe subscription, revokes
every GitHub installation, then purges 15 collections.

Tests below run with in-memory Mongo mocks + monkeypatched external
APIs so no real Stripe/GitHub calls fire.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def find_one(self, filter_, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in filter_.items()):
                return dict(r)
        return None

    def find(self, filter_, projection=None):
        matched = [dict(r) for r in self.rows
                   if all(r.get(k) == v for k, v in filter_.items())]
        class _Cursor:
            def __init__(self, docs):
                self.docs = docs
            async def to_list(self, n):
                return self.docs[:n]
        return _Cursor(matched)

    async def delete_many(self, filter_):
        before = len(self.rows)
        self.rows = [r for r in self.rows
                     if not all(r.get(k) == v for k, v in filter_.items())]
        return MagicMock(deleted_count=before - len(self.rows))


class _FakeDB:
    def __init__(self, seed):
        self._collections = {name: _FakeCollection(seed.get(name, []))
                             for name in [
            "dev_users", "cto_sessions", "chat_sessions", "cto_projects",
            "cto_tasks", "cto_payments", "api_keys", "post_task_scans",
            "warm_start_jobs", "oauth_codes", "github_installations",
            "ui_settings", "user_seo_claims", "login_attempts", "oauth_states",
        ]}

    def __getitem__(self, name):
        return self._collections[name]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._collections:
            return self._collections[name]
        raise AttributeError(name)


@pytest.mark.asyncio
async def test_cascade_deletes_all_15_collections():
    """Every user-scoped collection loses its rows for user_id."""
    from services.user_deletion import cascade_delete_user_data

    seed = {name: [{"user_id": "uid_x", "n": 1}] for name in [
        "dev_users", "cto_sessions", "chat_sessions", "cto_projects",
        "cto_tasks", "cto_payments", "api_keys", "post_task_scans",
        "warm_start_jobs", "oauth_codes", "github_installations",
        "ui_settings", "user_seo_claims", "login_attempts", "oauth_states",
    ]}
    seed["dev_users"] = [{"user_id": "uid_x", "email": "u@ex.com"}]
    seed["github_installations"] = []  # no installs → skip revoke path

    db = _FakeDB(seed)
    report = await cascade_delete_user_data(db, "uid_x")

    for coll in ("dev_users", "cto_sessions", "chat_sessions", "cto_projects",
                 "cto_tasks", "cto_payments", "api_keys", "post_task_scans",
                 "warm_start_jobs", "oauth_codes", "github_installations",
                 "ui_settings", "user_seo_claims", "login_attempts", "oauth_states"):
        assert coll in report["deletions"], f"{coll} missing from report"


@pytest.mark.asyncio
async def test_cascade_cancels_stripe_subscription(monkeypatch):
    """When user has stripe_subscription_id, cascade calls
    stripe.Subscription.delete(sub_id) immediately."""
    from services import user_deletion

    seed = {"dev_users": [{
        "user_id": "uid_x", "email": "u@ex.com",
        "stripe_subscription_id": "sub_test_123",
    }]}
    db = _FakeDB(seed)

    called_with = {}
    class _FakeSub:
        @staticmethod
        def delete(sub_id):
            called_with["sub_id"] = sub_id
            return {"id": sub_id, "status": "canceled"}
    class _FakeStripe:
        api_key = None
        Subscription = _FakeSub

    import sys
    monkeypatch.setitem(sys.modules, "stripe", _FakeStripe)
    monkeypatch.setattr(
        "services.stripe_client.stripe_key", lambda: "sk_test_dummy",
    )

    report = await user_deletion.cascade_delete_user_data(db, "uid_x")
    assert called_with.get("sub_id") == "sub_test_123"
    assert report["stripe_cancelled"] is True


@pytest.mark.asyncio
async def test_cascade_revokes_github_installations(monkeypatch):
    """When user owns github installations, each is revoked via API
    before local rows are deleted."""
    from services import user_deletion

    seed = {
        "dev_users": [{"user_id": "uid_x", "email": "u@ex.com"}],
        "github_installations": [
            {"user_id": "uid_x", "installation_id": 111, "active": True},
            {"user_id": "uid_x", "installation_id": 222, "active": True},
            {"user_id": "uid_x", "installation_id": 333, "active": False},
        ],
    }
    db = _FakeDB(seed)

    revoked_calls = []
    class _FakeGA:
        @staticmethod
        async def revoke_installation(iid):
            revoked_calls.append(iid)

    import sys
    monkeypatch.setitem(sys.modules, "services.github_app", _FakeGA)

    report = await user_deletion.cascade_delete_user_data(db, "uid_x")
    # Active installs revoked; inactive one skipped.
    assert set(revoked_calls) == {111, 222}
    revoked_ids = [r.get("id") for r in report["github_revoked"] if r.get("ok")]
    assert set(revoked_ids) == {111, 222}


@pytest.mark.asyncio
async def test_cascade_swallows_stripe_error(monkeypatch):
    """A Stripe outage / already-cancelled sub must NOT block the local
    purge — reports the error instead."""
    from services import user_deletion

    seed = {"dev_users": [{
        "user_id": "uid_x", "email": "u@ex.com",
        "stripe_subscription_id": "sub_dead",
    }]}
    db = _FakeDB(seed)

    class _FakeSub:
        @staticmethod
        def delete(sub_id):
            raise RuntimeError("Stripe network hiccup")
    class _FakeStripe:
        api_key = None
        Subscription = _FakeSub

    import sys
    monkeypatch.setitem(sys.modules, "stripe", _FakeStripe)
    monkeypatch.setattr(
        "services.stripe_client.stripe_key", lambda: "sk_test_dummy",
    )

    report = await user_deletion.cascade_delete_user_data(db, "uid_x")
    assert isinstance(report["stripe_cancelled"], str)
    assert "error" in report["stripe_cancelled"]
    # Local cascade still ran.
    assert report["deletions"]["dev_users"] == 1


@pytest.mark.asyncio
async def test_delete_me_endpoint_requires_email_match(monkeypatch):
    """POST /auth/delete-me refuses if email_confirmation != user email."""
    from main import app

    async def fake_dev(authorization=None):
        return {"user_id": "uid_x", "email": "real@user.com"}

    monkeypatch.setattr("routers.auth.current_dev", fake_dev)
    monkeypatch.setattr("routers.auth.get_db", lambda: object())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/auth/delete-me",
            headers={"Authorization": "Bearer x"},
            json={"email_confirmation": "wrong@user.com"},
        )
    assert r.status_code == 422
    assert "match" in r.text.lower()


@pytest.mark.asyncio
async def test_delete_me_refuses_founder(monkeypatch):
    """A founder email must not be able to self-delete (403)."""
    from main import app

    async def fake_dev(authorization=None):
        return {"user_id": "uid_f", "email": "founder@aurem.com"}

    monkeypatch.setattr("routers.auth.current_dev", fake_dev)
    monkeypatch.setattr("routers.auth.get_db", lambda: object())
    monkeypatch.setenv("FOUNDER_EMAILS", "founder@aurem.com,other@x.io")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/auth/delete-me",
            headers={"Authorization": "Bearer x"},
            json={"email_confirmation": "founder@aurem.com"},
        )
    assert r.status_code == 403
    assert "founder" in r.text.lower()


@pytest.mark.asyncio
async def test_delete_me_success_calls_cascade(monkeypatch):
    """Correct email + non-founder → cascade helper is called and 200
    returned with a deletion report."""
    from main import app

    async def fake_dev(authorization=None):
        return {"user_id": "uid_ok", "email": "u@ex.com"}

    called = {}
    async def fake_cascade(db, user_id):
        called["user_id"] = user_id
        return {
            "user_id":          user_id,
            "stripe_cancelled": None,
            "github_revoked":   [],
            "github_errors":    [],
            "deletions":        {"dev_users": 1, "cto_projects": 3},
            "email":            "u@ex.com",
        }

    monkeypatch.setattr("routers.auth.current_dev", fake_dev)
    monkeypatch.setattr("routers.auth.get_db", lambda: object())
    monkeypatch.setattr(
        "services.user_deletion.cascade_delete_user_data", fake_cascade,
    )
    monkeypatch.setenv("FOUNDER_EMAILS", "someone-else@x.io")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/auth/delete-me",
            headers={"Authorization": "Bearer x"},
            json={"email_confirmation": "u@ex.com"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert called["user_id"] == "uid_ok"
    assert body["report"]["deletions"]["cto_projects"] == 3
