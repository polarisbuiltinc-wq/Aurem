"""
tests/test_drift_alerts_admin_2026_08_30.py — R1a gap#4 admin
visibility (2026-08-30): a compact `GET /admin/drift-alerts` endpoint,
read-only, feeding the AdminSystemHealth "Drift-Blocked Rollbacks"
tile. Data source: the existing `ship_rollback_drift_detected` trust
event (already logged by the drift-detection fix, no new writer).
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


def _fake_db(docs):
    db = MagicMock()
    db.trust_surface_events.find = MagicMock(return_value=_FakeCursor(docs))
    return db


@pytest.mark.asyncio
async def test_t_drift_alert_shows_count():
    """2 injected ship_rollback_drift_detected events -> count == 2,
    each event carries the 5 fields the tile needs."""
    from routers.admin_analytics import drift_alerts

    docs = [
        {"loop_id": "loop_a", "branch": "main", "expected": "aaa1111", "actual": "bbb2222", "at": "t2"},
        {"loop_id": "loop_b", "branch": "auremcto/ship-1", "expected": "ccc3333", "actual": "ddd4444", "at": "t1"},
    ]
    db = _fake_db(docs)
    with patch("routers.admin_analytics._require_admin", new=AsyncMock(return_value={"user_id": "founder"})), \
         patch("routers.admin_analytics.require_db", return_value=db):
        res = await drift_alerts(authorization="Bearer x")

    assert res["count"] == 2
    assert res["events"][0] == {
        "loop_id": "loop_a", "branch": "main",
        "expected_sha": "aaa1111", "current_sha": "bbb2222", "timestamp": "t2",
    }


@pytest.mark.asyncio
async def test_t_drift_alert_empty():
    """0 events -> count == 0, events == []."""
    from routers.admin_analytics import drift_alerts

    db = _fake_db([])
    with patch("routers.admin_analytics._require_admin", new=AsyncMock(return_value={"user_id": "founder"})), \
         patch("routers.admin_analytics.require_db", return_value=db):
        res = await drift_alerts(authorization="Bearer x")

    assert res == {"count": 0, "events": []}


@pytest.mark.asyncio
async def test_t_drift_alert_expands_data_shape():
    """Each event carries everything the UI's expand needs:
    {loop_id, branch, expected_sha, current_sha, timestamp}."""
    from routers.admin_analytics import drift_alerts

    docs = [{"loop_id": "loop_x", "branch": "main", "expected": "e" * 40, "actual": "c" * 40, "at": "2026-08-30T10:00:00Z"}]
    db = _fake_db(docs)
    with patch("routers.admin_analytics._require_admin", new=AsyncMock(return_value={"user_id": "founder"})), \
         patch("routers.admin_analytics.require_db", return_value=db):
        res = await drift_alerts(authorization="Bearer x")

    assert res["count"] == 1
    ev = res["events"][0]
    for key in ("loop_id", "branch", "expected_sha", "current_sha", "timestamp"):
        assert key in ev and ev[key]


@pytest.mark.asyncio
async def test_drift_alerts_requires_admin():
    """Non-admin callers never see any drift data — same fail-closed
    gate as every other admin tile."""
    from routers.admin_analytics import drift_alerts
    from fastapi import HTTPException

    with patch("routers.admin_analytics._require_admin",
               new=AsyncMock(side_effect=HTTPException(403, "not admin"))):
        with pytest.raises(HTTPException):
            await drift_alerts(authorization="Bearer not-admin")
