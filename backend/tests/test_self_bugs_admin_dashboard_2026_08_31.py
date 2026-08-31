"""
tests/test_self_bugs_admin_dashboard_2026_08_31.py

Item 3 (2026-08-31) — read-only self-bug admin dashboard endpoint
(GET /api/admin/self-bugs/list). Mirrors the mocking pattern in
tests/test_drift_alerts_admin_2026_08_30.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for d in self._docs:
            yield d


def _fake_db(self_bugs, learned):
    db = MagicMock()
    db.ora_self_bugs.find = MagicMock(return_value=_FakeCursor(self_bugs))
    db.self_bug_learned.find = MagicMock(return_value=_FakeCursor(learned))
    return db


@pytest.mark.asyncio
async def test_t_self_bug_dashboard_lists():
    from routers.self_bugs_admin import list_self_bugs

    self_bugs = [
        {"type": "blank_ui", "source": "ui", "what_user_saw": "e1",
         "likely_cause": "c1", "confidence": "confirmed", "severity": "low",
         "proposed_fix": None, "context": {"subject": "preview"}, "ts": 100.0},
    ]
    learned = [{"signature": "blank_ui:preview", "times_seen": 3, "last_seen": 100.0}]
    db = _fake_db(self_bugs, learned)
    with patch("routers.self_bugs_admin.require_admin", new=AsyncMock(return_value={"user_id": "founder"})), \
         patch("routers.self_bugs_admin.require_db", return_value=db):
        res = await list_self_bugs(authorization="Bearer x")

    assert res["count"] == 1
    row = res["self_bugs"][0]
    assert row["type"] == "blank_ui"
    assert row["times_seen"] == 3
    assert row["signature"] == "blank_ui:preview"


@pytest.mark.asyncio
async def test_t_self_bug_dashboard_recurring_bubbles_up():
    from routers.self_bugs_admin import list_self_bugs

    self_bugs = [
        {"type": "tool_error", "source": "tool", "what_user_saw": "rare",
         "likely_cause": "c", "confidence": "likely", "severity": "high",
         "proposed_fix": None, "context": {"subject": "x"}, "ts": 50.0},
        {"type": "missing_button", "source": "k1", "what_user_saw": "common",
         "likely_cause": "c", "confidence": "confirmed", "severity": "high",
         "proposed_fix": None, "context": {"subject": "approve"}, "ts": 60.0},
    ]
    learned = [
        {"signature": "tool_error:x", "times_seen": 1, "last_seen": 50.0},
        {"signature": "missing_button:approve", "times_seen": 7, "last_seen": 60.0},
    ]
    db = _fake_db(self_bugs, learned)
    with patch("routers.self_bugs_admin.require_admin", new=AsyncMock(return_value={"user_id": "founder"})), \
         patch("routers.self_bugs_admin.require_db", return_value=db):
        res = await list_self_bugs(authorization="Bearer x")

    assert res["self_bugs"][0]["what_user_saw"] == "common"
    assert res["self_bugs"][0]["times_seen"] == 7
    assert res["self_bugs"][1]["times_seen"] == 1


@pytest.mark.asyncio
async def test_t_self_bug_dashboard_type_filter():
    from routers.self_bugs_admin import list_self_bugs

    db = _fake_db([], [])
    with patch("routers.self_bugs_admin.require_admin", new=AsyncMock(return_value={"user_id": "founder"})), \
         patch("routers.self_bugs_admin.require_db", return_value=db):
        res = await list_self_bugs(type="jargon_leak", authorization="Bearer x")

    db.ora_self_bugs.find.assert_called_once()
    called_query = db.ora_self_bugs.find.call_args[0][0]
    assert called_query == {"type": "jargon_leak"}
    assert res["count"] == 0


@pytest.mark.asyncio
async def test_t_self_bug_dashboard_readonly():
    """No mutate/delete route exists on this router — read-only by
    construction."""
    from routers.self_bugs_admin import router
    methods = {route.methods and next(iter(route.methods)) for route in router.routes}
    for route in router.routes:
        assert "GET" in route.methods
        assert "POST" not in route.methods
        assert "DELETE" not in route.methods
        assert "PUT" not in route.methods


@pytest.mark.asyncio
async def test_t_self_bug_dashboard_requires_admin():
    from routers.self_bugs_admin import list_self_bugs
    from fastapi import HTTPException

    with patch("routers.self_bugs_admin.require_admin",
               new=AsyncMock(side_effect=HTTPException(status_code=403, detail="nope"))):
        with pytest.raises(HTTPException):
            await list_self_bugs(authorization="Bearer x")
