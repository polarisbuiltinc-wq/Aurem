"""
2026-08-19 fix — POST /support/tickets/public.

Root cause: the ONLY unauthenticated support path was
`/support/tickets/token`, which requires a signed HMAC token that
only exists inside campaign emails. A pre-signup/logged-out visitor
clicking the site's own footer "Support" link had zero way to reach
us — `pages/Support.jsx`'s form was permanently `disabled` without
that token. This endpoint is the fix: a genuine no-login, no-token
path, rate-limited per-IP since it takes zero identity proof.

Follows the same `_FakeDB`/AsyncClient pattern as
test_iter388u_support_reply_ux.py so it doesn't need a real Mongo
connection or app lifespan.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.inserts = []

    async def find_one(self, filter_, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in filter_.items()):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        self.rows.append(dict(doc))
        return MagicMock(inserted_id="fake_id")


class _FakeDB:
    def __init__(self, seed=None):
        seed = seed or {}
        self._collections = {
            "cto_support":          _FakeCollection(seed.get("cto_support", [])),
            "cto_support_messages": _FakeCollection(seed.get("cto_support_messages", [])),
            "dev_users":            _FakeCollection(seed.get("dev_users", [])),
        }

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._collections:
            return self._collections[name]
        raise AttributeError(name)


def _reset_rl():
    import services.rate_limiter as rl
    rl._buckets.clear()


@pytest.mark.asyncio
async def test_valid_submission_returns_ticket_id(monkeypatch):
    _reset_rl()
    from main import app
    fake_db = _FakeDB()
    monkeypatch.setattr("routers.support.require_db", lambda: fake_db)

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/support/tickets/public",
            json={"name": "Jane Doe", "email": "jane@example.com",
                  "body": "I can't log in"},
            headers={"X-Forwarded-For": "10.10.10.1"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert fake_db.cto_support.rows[0]["user_email"] == "jane@example.com"


@pytest.mark.asyncio
async def test_invalid_email_rejected(monkeypatch):
    _reset_rl()
    from main import app
    monkeypatch.setattr("routers.support.require_db", lambda: _FakeDB())

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/support/tickets/public",
            json={"email": "not-an-email", "body": "hi"},
            headers={"X-Forwarded-For": "10.10.10.2"},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_empty_body_rejected(monkeypatch):
    _reset_rl()
    from main import app
    monkeypatch.setattr("routers.support.require_db", lambda: _FakeDB())

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/support/tickets/public",
            json={"email": "a@b.com", "body": "   "},
            headers={"X-Forwarded-For": "10.10.10.3"},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_rate_limited_after_five_per_ip(monkeypatch):
    _reset_rl()
    from main import app
    monkeypatch.setattr("routers.support.require_db", lambda: _FakeDB())

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        headers = {"X-Forwarded-For": "10.10.10.4"}
        for _ in range(5):
            r = await ac.post(
                "/api/aurem-dev/support/tickets/public",
                json={"email": "flood@example.com", "body": "spam test"},
                headers=headers,
            )
            assert r.status_code == 200, r.text
        r = await ac.post(
            "/api/aurem-dev/support/tickets/public",
            json={"email": "flood@example.com", "body": "spam test"},
            headers=headers,
        )
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_existing_token_endpoint_unaffected(monkeypatch):
    """Regression guard — the old email-link flow must keep working
    byte-for-byte after this change."""
    _reset_rl()
    from main import app
    monkeypatch.setattr("routers.support.require_db", lambda: _FakeDB())

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/support/tickets/token",
            json={"t": "definitely-wrong", "e": "a@b.com", "body": "x"},
        )
    assert r.status_code == 403
