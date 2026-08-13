"""
Iter 388u — Support Reply UX Fix (Option A) regression tests.

Bug: admin_reply() in routers/admin_support.py wrote to Mongo but
NEVER notified the user. `SupportPopup.jsx` promised "You'll see the
reply in this same app" — but no code fetched replies. No email, no
badge, no polling. Ticketing system was a black hole.

Fix (Option A — email + public thread view):
  · services/support_email.py::send_reply_notification()
  · GET  /support/tickets/{id}/thread  (public, HMAC-token-verified)
  · POST /support/tickets/{id}/reply/token  (public reply-back)
  · admin_reply() now fires notification email best-effort

Tests below verify:
  · HMAC token round-trip
  · thread endpoint 403 on bad token, 404 on wrong owner, 200 on match
  · reply/token endpoint 403 on bad token, 200 appends message
  · admin_reply() calls send_reply_notification with correct args
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.updates = []
        self.inserts = []

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
            def sort(self, *_a, **_k): return self
            async def to_list(self, n): return self.docs[:n]
        return _Cursor(matched)

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        self.rows.append(dict(doc))
        return MagicMock(inserted_id="fake_id")

    async def update_one(self, filter_, update, upsert=False):
        self.updates.append({"filter": dict(filter_), "update": dict(update)})
        matched = 0
        for r in self.rows:
            if all(r.get(k) == v for k, v in filter_.items()):
                matched += 1
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                break
        return MagicMock(matched_count=matched, modified_count=matched)


class _FakeDB:
    def __init__(self, seed):
        self._collections = {
            "cto_support":          _FakeCollection(seed.get("cto_support", [])),
            "cto_support_messages": _FakeCollection(seed.get("cto_support_messages", [])),
        }

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._collections:
            return self._collections[name]
        raise AttributeError(name)


# ── Unit: token pattern ──────────────────────────────────────────────
def test_support_token_deterministic_and_case_insensitive():
    from services.first50_campaign import support_token
    t1 = support_token("Alice@Example.com")
    t2 = support_token("alice@example.com")
    t3 = support_token(" ALICE@EXAMPLE.COM ")
    assert t1 == t2 == t3
    assert len(t1) == 16


def test_thread_url_shape():
    from services.support_email import thread_url
    url = thread_url("tkt_abc123", "u@ex.com")
    assert url.startswith("http")
    assert "/support/thread/tkt_abc123" in url
    assert "t=" in url
    assert "e=u%40ex.com" in url


# ── Unit: email render is safe & correct ─────────────────────────────
def test_email_render_escapes_html():
    from services.support_email import _render
    subject, text, html = _render(
        "tkt_abc12345", "<script>alert(1)</script>\nline2",
        "u@ex.com", "Alice",
    )
    assert "[Support #abc12345]" in subject
    assert "<script>" not in html  # escaped
    assert "&lt;script&gt;" in html
    assert "line2" in text
    assert "Hi Alice," in text and "Hi Alice," in html


# ── Endpoint: GET /support/tickets/{id}/thread ───────────────────────
@pytest.mark.asyncio
async def test_public_thread_rejects_bad_token(monkeypatch):
    from main import app
    monkeypatch.setattr("routers.support.require_db",
                        lambda: _FakeDB({}))

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.get(
            "/api/aurem-dev/support/tickets/tkt_x/thread",
            params={"t": "wrong-token", "e": "u@ex.com"},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_public_thread_returns_messages_on_valid_token(monkeypatch):
    from main import app
    from services.first50_campaign import support_token

    seed = {
        "cto_support": [{
            "ticket_id": "tkt_ok1", "user_email": "u@ex.com",
            "subject": "hello", "status": "pending_user",
        }],
        "cto_support_messages": [
            {"ticket_id": "tkt_ok1", "sender": "user",  "message": "hi",   "ts": 1.0},
            {"ticket_id": "tkt_ok1", "sender": "admin", "message": "hey!", "ts": 2.0},
        ],
    }
    monkeypatch.setattr("routers.support.require_db",
                        lambda: _FakeDB(seed))

    tok = support_token("u@ex.com")
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.get(
            "/api/aurem-dev/support/tickets/tkt_ok1/thread",
            params={"t": tok, "e": "u@ex.com"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticket_id"] == "tkt_ok1"
    assert body["subject"] == "hello"
    assert len(body["messages"]) == 2
    assert body["messages"][0]["message"] == "hi"
    assert body["messages"][1]["message"] == "hey!"


@pytest.mark.asyncio
async def test_public_thread_404_when_owner_mismatch(monkeypatch):
    """Valid token but ticket belongs to a different email → 404
    (never leak existence to non-owners)."""
    from main import app
    from services.first50_campaign import support_token

    seed = {"cto_support": [{
        "ticket_id": "tkt_x", "user_email": "someone-else@ex.com",
    }]}
    monkeypatch.setattr("routers.support.require_db",
                        lambda: _FakeDB(seed))

    tok = support_token("u@ex.com")
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.get(
            "/api/aurem-dev/support/tickets/tkt_x/thread",
            params={"t": tok, "e": "u@ex.com"},
        )
    assert r.status_code == 404


# ── Endpoint: POST /support/tickets/{id}/reply/token ─────────────────
@pytest.mark.asyncio
async def test_public_reply_rejects_bad_token(monkeypatch):
    from main import app
    monkeypatch.setattr("routers.support.require_db",
                        lambda: _FakeDB({}))

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/support/tickets/tkt_x/reply/token",
            json={"t": "wrong", "e": "u@ex.com", "body": "reply"},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_public_reply_appends_message_and_reopens(monkeypatch):
    from main import app
    from services.first50_campaign import support_token

    seed = {"cto_support": [{
        "ticket_id": "tkt_r1", "user_email": "u@ex.com",
        "status": "pending_user",
    }]}
    fake_db = _FakeDB(seed)
    monkeypatch.setattr("routers.support.require_db", lambda: fake_db)

    tok = support_token("u@ex.com")
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/support/tickets/tkt_r1/reply/token",
            json={"t": tok, "e": "u@ex.com", "body": "follow-up question"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    msgs = fake_db.cto_support_messages.inserts
    assert len(msgs) == 1
    assert msgs[0]["sender"] == "user"
    assert msgs[0]["message"] == "follow-up question"
    # Ticket reopened for admin attention.
    ticket_updates = fake_db.cto_support.updates
    assert any(u["update"]["$set"]["status"] == "open"
               for u in ticket_updates)


# ── admin_reply() hooks notification ─────────────────────────────────
@pytest.mark.asyncio
async def test_admin_reply_fires_notification_email(monkeypatch):
    """admin_reply() calls send_reply_notification with the ticket's
    email + the admin message."""
    from main import app
    from cto_services.auth import require_admin_dep

    seed = {"cto_support": [{
        "ticket_id": "tkt_n1", "user_email": "user@example.com",
        "user_name": "Alice", "status": "open",
    }]}
    fake_db = _FakeDB(seed)

    async def fake_admin(authorization=None):
        return {"user_id": "admin", "email": "founder@aurem.com",
                "is_admin": True}

    called = {}
    async def fake_send(*, user_email, user_name, ticket_id, admin_message):
        called.update(dict(
            user_email=user_email, user_name=user_name,
            ticket_id=ticket_id, admin_message=admin_message,
        ))
        return True, None

    monkeypatch.setattr("routers.admin_support._require_admin", fake_admin)
    monkeypatch.setattr("routers.admin_support.require_db", lambda: fake_db)
    monkeypatch.setattr(
        "services.support_email.send_reply_notification", fake_send,
    )
    app.dependency_overrides[require_admin_dep] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as ac:
            r = await ac.post(
                "/api/aurem-dev/admin/support/tkt_n1/reply",
                headers={"Authorization": "Bearer admin"},
                json={"message": "here is my reply"},
            )
    finally:
        app.dependency_overrides.pop(require_admin_dep, None)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["email_notified"] is True
    assert called["user_email"] == "user@example.com"
    assert called["user_name"] == "Alice"
    assert called["ticket_id"] == "tkt_n1"
    assert called["admin_message"] == "here is my reply"


@pytest.mark.asyncio
async def test_admin_reply_survives_email_failure(monkeypatch):
    """Email send failure must NEVER break the admin reply — the
    reply is durable in Mongo. Endpoint returns 200 with
    email_notified=False + email_error string."""
    from main import app
    from cto_services.auth import require_admin_dep

    seed = {"cto_support": [{
        "ticket_id": "tkt_f1", "user_email": "user@example.com",
    }]}
    fake_db = _FakeDB(seed)

    async def fake_admin(authorization=None):
        return {"user_id": "admin", "email": "founder@aurem.com"}
    async def failing_send(**_k):
        return False, "RESEND_API_KEY not configured"

    monkeypatch.setattr("routers.admin_support._require_admin", fake_admin)
    monkeypatch.setattr("routers.admin_support.require_db", lambda: fake_db)
    monkeypatch.setattr(
        "services.support_email.send_reply_notification", failing_send,
    )
    app.dependency_overrides[require_admin_dep] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as ac:
            r = await ac.post(
                "/api/aurem-dev/admin/support/tkt_f1/reply",
                headers={"Authorization": "Bearer admin"},
                json={"message": "reply"},
            )
    finally:
        app.dependency_overrides.pop(require_admin_dep, None)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["email_notified"] is False
    assert "RESEND_API_KEY" in (body.get("email_error") or "")
