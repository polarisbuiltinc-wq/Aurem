"""
QA regression probe: POST /loop/{loop_id}/pause-response should return
409 loop_terminal for persisted terminal loops, not fall through to 404.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


class _Coll:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def find_one(self, query, *args, **kwargs):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                return dict(row)
        return None


class _DB:
    def __init__(self):
        self.loop_sessions = _Coll([
            {
                "loop_id": "iter360_persisted_failed",
                "user_id": "qa_user",
                "project_id": "qa_project",
                "state": "failed",
                "phase": "verify",
                "context": {},
            }
        ])


@pytest.mark.asyncio
async def test_pause_response_retry_on_persisted_terminal_loop_returns_409(monkeypatch):
    from routers import loop as loop_router
    from services import loop_engine as le

    le._LIVE.pop("iter360_persisted_failed", None)

    async def fake_current_dev(_authorization):
        return {"user_id": "qa_user"}

    db = _DB()
    monkeypatch.setattr(loop_router, "current_dev", fake_current_dev)
    monkeypatch.setattr(loop_router, "get_db", lambda: db)

    with pytest.raises(HTTPException) as excinfo:
        await loop_router.pause_response(
            "iter360_persisted_failed",
            loop_router.PauseResponseBody(action="retry"),
            authorization="Bearer qa",
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "loop_terminal"