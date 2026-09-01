"""Regression — Run A / A0: ORA-Council recall mode-taxonomy fix (2026-08-27).

Root cause (see memory/investigation_rerank.md): both `/chat/send` and the
SSE `/chat/stream` call sites passed `mode=_detect_mode(prompt)` into
`get_council_few_shot()`. `_detect_mode()` only ever returns "code"/"chat",
but the retriever's candidate index is keyed by the REAL council taxonomy
("A"/"B"/"C"/"D"/"E"/"F", written by `classify_intent()`). Since "code"/
"chat" are never keys in `_index["by_mode"]`, `_candidate_indices()` always
intersected to the empty set — recall silently returned 0 candidates on
EVERY real request, regardless of corpus size or `_MIN_SCORE`.

Fix: both call sites now pass `classify_intent(prompt, f12_payload)`
(the same taxonomy the index is built from) instead of `_detect_mode()`.

This file proves the fix at the REAL call-path boundary (the actual
`/chat/send` endpoint), not just against the retriever module in
isolation — the retriever's own unit tests already pass literal "A"/"C"
values directly and would never have caught this call-site bug.
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

import services.ora_council_retriever as retriever
from services import chat_helpers


# ── Fake Mongo, supports BOTH db["coll"] (retriever) and db.coll (rest of
#   chat_send) access on the same underlying collections ────────────────
class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        v = self._rows[self._i]
        self._i += 1
        return v


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if row.get(k) != v:
                return False
        return True

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [r for r in self.rows if self._match(r, query)]
        return dict(matched[0]) if matched else None

    async def update_one(self, query, update, upsert=False):
        import types
        for r in self.rows:
            if self._match(r, query):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    def find(self, query=None, projection=None, sort=None, limit=None):
        matched = [dict(r) for r in self.rows if self._match(r, query)]
        if limit:
            matched = matched[:limit]
        return _FakeCursor(matched)


class _FakeDB:
    def __init__(self):
        self._cols: dict[str, _FakeCollection] = {}

    def _coll(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeCollection()
        return self._cols[name]

    def __getattr__(self, name):
        return self._coll(name)

    def __getitem__(self, name):
        return self._coll(name)


USER = {"user_id": "u1", "email": "user@example.com", "tier": "pro",
        "is_admin": False, "created_at": time.time()}
AUTH = {"Authorization": "Bearer u1"}


def _council_row(msg, reply, mode="A", user="u1"):
    from datetime import datetime, timezone
    return {
        "user_message": msg, "final_output": reply, "mode": mode,
        "user_id": user, "project_id": "home",
        "pass_result": True, "lint_blocked": False,
        "timestamp": datetime.now(timezone.utc),
    }


@pytest.fixture(autouse=True)
def _reset_retriever_index():
    retriever._reset_for_tests()
    yield
    retriever._reset_for_tests()


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def client(fake_db):
    from routers import chat as router_mod
    from cto_services import db as _dbmod
    _dbmod.set_db(fake_db)

    async def _fake_current_dev(authorization=None):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return USER

    old_current_dev = router_mod.turn.current_dev
    router_mod.turn.current_dev = _fake_current_dev

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    c = TestClient(app)
    yield c

    router_mod.turn.current_dev = old_current_dev
    _dbmod.set_db(None)


# ═════════════════════════════════════════════════════════════════════
# 1) Call-site boundary — the mode actually PASSED to the retriever must
#    be the real "A"-"F" taxonomy, never _detect_mode()'s "code"/"chat".
# ═════════════════════════════════════════════════════════════════════
class TestCallSitePassesRealTaxonomyMode:
    def test_chat_send_passes_classify_intent_mode_not_detect_mode(
        self, client, fake_db,
    ):
        captured = {}

        async def _spy_recall(db, user_message, mode="A", **kw):
            captured["mode"] = mode
            return ("", 0)

        # "should I pivot or persevere" -> classify_intent() == "B".
        # _detect_mode() (no code hints) would have passed "chat" — the
        # pre-fix bug. If this test ever sees "chat" again, the taxonomy
        # regression has silently returned.
        with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
             patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.ora_council_retriever.get_council_few_shot", _spy_recall), \
             patch("routers.chat.turn.chat_with_tools",
                   AsyncMock(return_value={"content": "hi", "provider": "deepseek", "meta": {}})), \
             patch("services.response_confidence.response_seems_mismatched", return_value=False), \
             patch("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500)):
            r = client.post(
                "/api/aurem-dev/chat/send", headers=AUTH,
                json={"prompt": "should I pivot or persevere",
                      "project_id": "home", "session_id": "s1"},
            )
        assert r.status_code == 200, r.text
        assert captured["mode"] == "B", (
            f"expected real taxonomy mode 'B', got {captured['mode']!r} — "
            "if this is 'chat', the _detect_mode() regression is back."
        )
        assert captured["mode"] not in ("code", "chat")


# ═════════════════════════════════════════════════════════════════════
# 2) End-to-end — a REAL recall call (real retriever, no mocking of
#    get_council_few_shot) must now return >=1 candidate for a bucket
#    that, pre-fix, always returned 0.
# ═════════════════════════════════════════════════════════════════════
class TestRealRecallNowReturnsCandidates:
    @pytest.mark.asyncio
    async def test_end_to_end_recall_returns_at_least_one_candidate(
        self, client, fake_db,
    ):
        # 25 rows >= _MIN_BUCKET(20), same user, mode "A" (the real mode
        # classify_intent() gives a greeting like "hi there"). A few rows
        # are near-identical to the live query so TF-IDF clears _MIN_SCORE.
        rows = [
            _council_row("hi there", "Hey! How can I help today?"),
            _council_row("hi there, how's it going",
                         "Doing well — what would you like to work on?"),
            _council_row("hey there", "Hello! What can I do for you?"),
        ] + [
            _council_row(f"unrelated filler question number {i}",
                         f"unrelated filler answer number {i}")
            for i in range(22)
        ]
        fake_db._coll("ora_council_logs").rows = rows

        # Sanity: PRE-FIX behavior (the bug) — calling the real retriever
        # with the old _detect_mode() output for the same query/corpus
        # proves it was unconditionally 0.
        old_mode = chat_helpers._detect_mode("hi there")
        assert old_mode in ("code", "chat")
        pre_fix_block, pre_fix_n = await retriever.get_council_few_shot(
            fake_db, "hi there", mode=old_mode,
            user_id="u1", project_id="home", k=2,
        )
        assert pre_fix_n == 0, "pre-fix mode value must recall nothing (the bug)"
        retriever._reset_for_tests()  # fresh index for the real call below

        with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
             patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("routers.chat.turn.chat_with_tools",
                   AsyncMock(return_value={"content": "hi", "provider": "deepseek", "meta": {}})), \
             patch("services.response_confidence.response_seems_mismatched", return_value=False), \
             patch("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500)):
            r = client.post(
                "/api/aurem-dev/chat/send", headers=AUTH,
                json={"prompt": "hi there", "project_id": "home", "session_id": "s1"},
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["council_recalled"] >= 1, (
            "post-fix real /chat/send call must recall >=1 candidate "
            f"where pre-fix it always recalled 0; got {body.get('council_recalled')}"
        )
