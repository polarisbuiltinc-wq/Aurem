"""Phase 2c coverage wave 2 — backend/routers/chat.py (2026-08-24).

Targets the 4 standalone session-management endpoints that wave 1
left fully/mostly uncovered (all TestClient-safe, no SSE/LLM mocking
needed): `chat_sessions_list`, `chat_feedback`, `chat_session_delete`,
`chat_session_clear_messages`.

`chat_stream`'s internal `gen`/`_worker` tool-calling loop (the large
remaining gap) is NOT targeted here — same documented reason as wave 1:
would need deep multi-round tool-calling + SSE mocking, materially
bigger effort than the standalone endpoints below.
"""
from __future__ import annotations

import time
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if k == "session_id" and isinstance(v, dict) and "$not" in v:
                # E2E_SESSION_PREFIX_RE exclusion — real regex not needed here.
                continue
            if k == "$or":
                if not any(self._match(row, sub) for sub in v):
                    return False
                continue
            if row.get(k) != v:
                return False
        return True

    def find(self, query=None, projection=None, sort=None, limit=None):
        matched = [dict(r) for r in self.rows if self._match(r, query)]
        return _FakeCursor(matched)

    async def find_one(self, query=None, projection=None):
        matched = [r for r in self.rows if self._match(r, query)]
        return dict(matched[0]) if matched else None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if self._match(r, query):
                for k, v in (update.get("$set") or {}).items():
                    if "." in k:
                        top, idx, field = k.split(".", 2)
                        arr = r.setdefault(top, [])
                        idx = int(idx)
                        while len(arr) <= idx:
                            arr.append({})
                        arr[idx][field] = v
                    else:
                        r[k] = v
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    async def delete_one(self, query):
        for i, r in enumerate(self.rows):
            if self._match(r, query):
                del self.rows[i]
                return types.SimpleNamespace(deleted_count=1)
        return types.SimpleNamespace(deleted_count=0)


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


USER = {"user_id": "u1", "email": "user@example.com", "tier": "pro",
        "is_admin": False}
AUTH = {"Authorization": "Bearer u1"}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def client(fake_db):
    from routers import chat as router_mod
    from cto_services import db as _dbmod

    old_dbmod_get_db = _dbmod.get_db
    # 2026-09-08 chat.py -> chat/ package split — get_db/current_dev are
    # imported independently by each submodule; this shared fixture
    # covers history/misc/stream endpoints, so patch all 4 submodules.
    _submods = (router_mod.misc, router_mod.turn, router_mod.stream, router_mod.history)
    _old_get_db = {m: m.get_db for m in _submods}
    _old_current_dev = {m: m.current_dev for m in _submods}

    _dbmod.set_db(fake_db)
    for m in _submods:
        m.get_db = lambda: fake_db

    async def _fake_current_dev(authorization=None):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return USER
    for m in _submods:
        m.current_dev = _fake_current_dev

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    yield TestClient(app)

    _dbmod.set_db(None)
    _dbmod.get_db = old_dbmod_get_db
    for m in _submods:
        m.get_db = _old_get_db[m]
        m.current_dev = _old_current_dev[m]


# ── GET /chat/sessions ──────────────────────────────────────────────
class TestChatSessionsList:
    def test_db_none_returns_empty(self, client, monkeypatch):
        from routers import chat as router_mod
        monkeypatch.setattr(router_mod.history, "get_db", lambda: None)
        r = client.get("/api/aurem-dev/chat/sessions", headers=AUTH)
        assert r.status_code == 200
        assert r.json() == {"ok": True, "sessions": []}

    def test_home_filter_includes_null_and_missing_project_id(self, client, fake_db):
        fake_db.chat_sessions.rows = [
            {"session_id": "s1", "user_id": "u1", "project_id": None,
             "title": "a", "updated_at": 2},
            {"session_id": "s2", "user_id": "u1",
             "title": "b", "updated_at": 1},
            {"session_id": "s3", "user_id": "u1", "project_id": "p1",
             "title": "c", "updated_at": 3},
        ]
        r = client.get("/api/aurem-dev/chat/sessions?project_id=home", headers=AUTH)
        assert r.status_code == 200
        ids = {s["session_id"] for s in r.json()["sessions"]}
        assert ids == {"s1", "s2"}

    def test_project_filter(self, client, fake_db):
        fake_db.chat_sessions.rows = [
            {"session_id": "s1", "user_id": "u1", "project_id": "p1",
             "title": "a", "updated_at": 1},
            {"session_id": "s2", "user_id": "u1", "project_id": "p2",
             "title": "b", "updated_at": 2},
        ]
        r = client.get("/api/aurem-dev/chat/sessions?project_id=p1", headers=AUTH)
        assert r.status_code == 200
        ids = {s["session_id"] for s in r.json()["sessions"]}
        assert ids == {"s1"}

    def test_no_project_filter_returns_all_users_sessions(self, client, fake_db):
        fake_db.chat_sessions.rows = [
            {"session_id": "s1", "user_id": "u1", "title": "a", "updated_at": 1},
            {"session_id": "s2", "user_id": "other", "title": "b", "updated_at": 2},
        ]
        r = client.get("/api/aurem-dev/chat/sessions", headers=AUTH)
        ids = {s["session_id"] for s in r.json()["sessions"]}
        assert ids == {"s1"}


# ── POST /chat/feedback ──────────────────────────────────────────────
class TestChatFeedback:
    def test_invalid_vote_400(self, client):
        r = client.post("/api/aurem-dev/chat/feedback", headers=AUTH,
                         json={"session_id": "s1", "turn_index": 0, "vote": "meh"})
        assert r.status_code == 400

    def test_db_none_503(self, client, monkeypatch):
        from routers import chat as router_mod
        monkeypatch.setattr(router_mod.misc, "get_db", lambda: None)
        r = client.post("/api/aurem-dev/chat/feedback", headers=AUTH,
                         json={"session_id": "s1", "turn_index": 0, "vote": "up"})
        assert r.status_code == 503

    def test_success_records_vote_and_comment(self, client, fake_db):
        fake_db.chat_sessions.rows = [
            {"session_id": "s1", "user_id": "u1", "turns": [{}]},
        ]
        r = client.post("/api/aurem-dev/chat/feedback", headers=AUTH,
                         json={"session_id": "s1", "turn_index": 0,
                               "vote": "down", "comment": "meh"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        fb = fake_db.chat_sessions.rows[0]["turns"][0]["feedback"]
        assert fb["vote"] == "down"
        assert fb["comment"] == "meh"


# ── DELETE /chat/sessions/{id} ───────────────────────────────────────
class TestChatSessionDelete:
    def test_db_none_503(self, client, monkeypatch):
        from routers import chat as router_mod
        monkeypatch.setattr(router_mod.history, "get_db", lambda: None)
        r = client.delete("/api/aurem-dev/chat/sessions/s1", headers=AUTH)
        assert r.status_code == 503

    def test_deletes_matching_session(self, client, fake_db):
        fake_db.chat_sessions.rows = [{"session_id": "s1", "user_id": "u1"}]
        r = client.delete("/api/aurem-dev/chat/sessions/s1", headers=AUTH)
        assert r.status_code == 200
        assert r.json() == {"ok": True, "deleted": 1}
        assert fake_db.chat_sessions.rows == []

    def test_no_match_deleted_zero(self, client, fake_db):
        r = client.delete("/api/aurem-dev/chat/sessions/nope", headers=AUTH)
        assert r.json() == {"ok": True, "deleted": 0}


# ── DELETE /chat/sessions/{id}/messages ──────────────────────────────
class TestChatSessionClearMessages:
    def test_db_none_503(self, client, monkeypatch):
        from routers import chat as router_mod
        monkeypatch.setattr(router_mod.history, "get_db", lambda: None)
        r = client.delete("/api/aurem-dev/chat/sessions/s1/messages", headers=AUTH)
        assert r.status_code == 503

    def test_not_found_404(self, client, fake_db):
        r = client.delete("/api/aurem-dev/chat/sessions/nope/messages", headers=AUTH)
        assert r.status_code == 404

    def test_clears_turns_keeps_session(self, client, fake_db):
        fake_db.chat_sessions.rows = [
            {"session_id": "s1", "user_id": "u1",
             "turns": [{"role": "user"}], "title": "keep-me",
             "last_message": "hi"},
        ]
        r = client.delete("/api/aurem-dev/chat/sessions/s1/messages", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body == {"ok": True, "cleared": True, "session_id": "s1"}
        row = fake_db.chat_sessions.rows[0]
        assert row["turns"] == []
        assert row["last_message"] == ""
        assert row["title"] == "keep-me"


# ── _maybe_guard_shell_handoff_followup (direct, no SSE needed) ─────
class TestMaybeGuardShellHandoffFollowup:
    @pytest.fixture
    def db_patch(self, fake_db):
        from cto_services import db as _dbmod
        old = _dbmod.get_db
        _dbmod.set_db(fake_db)
        yield fake_db
        _dbmod.set_db(None)
        _dbmod.get_db = old

    @pytest.mark.asyncio
    async def test_db_none_returns_none(self, monkeypatch):
        from routers import chat as router_mod
        from cto_services import db as _dbmod
        monkeypatch.setattr(_dbmod, "get_db", lambda: None)
        body = types.SimpleNamespace(prompt="install", session_id="s1")
        out = await router_mod._maybe_guard_shell_handoff_followup(
            body=body, user_id="u1")
        assert out is None

    @pytest.mark.asyncio
    async def test_prompt_too_long_returns_none(self, db_patch):
        from routers import chat as router_mod
        body = types.SimpleNamespace(prompt="x" * 61, session_id="s1")
        out = await router_mod._maybe_guard_shell_handoff_followup(
            body=body, user_id="u1")
        assert out is None

    @pytest.mark.asyncio
    async def test_prompt_with_path_returns_none(self, db_patch):
        from routers import chat as router_mod
        body = types.SimpleNamespace(prompt="fix src/app.py", session_id="s1")
        out = await router_mod._maybe_guard_shell_handoff_followup(
            body=body, user_id="u1")
        assert out is None

    @pytest.mark.asyncio
    async def test_no_assistant_turns_returns_none(self, db_patch):
        from routers import chat as router_mod
        db_patch.chat_sessions.rows = [
            {"user_id": "u1", "session_id": "s1",
             "messages": ["not-a-dict", {"role": "user", "content": "hi"}]},
        ]
        body = types.SimpleNamespace(prompt="do it", session_id="s1")
        out = await router_mod._maybe_guard_shell_handoff_followup(
            body=body, user_id="u1")
        assert out is None

    @pytest.mark.asyncio
    async def test_assistant_turn_without_handoff_returns_none(self, db_patch):
        from routers import chat as router_mod
        db_patch.chat_sessions.rows = [
            {"user_id": "u1", "session_id": "s1",
             "messages": [{"role": "assistant", "content": "just chatting"}]},
        ]
        body = types.SimpleNamespace(prompt="do it", session_id="s1")
        out = await router_mod._maybe_guard_shell_handoff_followup(
            body=body, user_id="u1")
        assert out is None
