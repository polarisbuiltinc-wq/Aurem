"""
tests/test_mock_flag_visibility_2026_08_30.py — R9 MOCK_LLM
investigation follow-up (STEP 4): `GET /admin/live-model-mode` must
surface the boot-cached vs current-env mismatch explicitly, so a
founder's `.env` edit (MOCK_LLM=false) is never silently invisible
while the process hasn't restarted yet.
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


def _fake_db():
    db = MagicMock()
    db.trust_surface_events.count_documents = AsyncMock(return_value=0)
    db.trust_surface_events.find = MagicMock(return_value=_FakeCursor([]))
    return db


@pytest.mark.asyncio
async def test_no_restart_pending_when_boot_matches_env(monkeypatch):
    """Boot value == current env value -> restart_pending is False."""
    from routers.admin_analytics import live_model_mode

    monkeypatch.setenv("MOCK_LLM", "true")
    with patch("routers.admin_analytics._require_admin", new=AsyncMock(return_value={"user_id": "founder"})), \
         patch("routers.admin_analytics.require_db", return_value=_fake_db()), \
         patch("services.ora_chat_v2.llm_client.is_mock", return_value=True):
        res = await live_model_mode(authorization="Bearer x")

    assert res["mode"] == "mock"
    assert res["mock_boot_value"] is True
    assert res["mock_current_env_value"] is True
    assert res["restart_pending"] is False
    assert res["mock_flag_boot_cached"] is True


@pytest.mark.asyncio
async def test_restart_pending_when_env_edited_but_process_not_restarted(monkeypatch):
    """The exact bug this closes visibility on: founder set
    MOCK_LLM=false in the env, but the process (boot-cached) is still
    serving mock — restart_pending must be True."""
    from routers.admin_analytics import live_model_mode

    monkeypatch.setenv("MOCK_LLM", "false")
    with patch("routers.admin_analytics._require_admin", new=AsyncMock(return_value={"user_id": "founder"})), \
         patch("routers.admin_analytics.require_db", return_value=_fake_db()), \
         patch("services.ora_chat_v2.llm_client.is_mock", return_value=True):
        res = await live_model_mode(authorization="Bearer x")

    assert res["mode"] == "mock"           # still mock — boot value wins for actual serving
    assert res["mock_boot_value"] is True
    assert res["mock_current_env_value"] is False
    assert res["restart_pending"] is True  # <-- the visibility fix


@pytest.mark.asyncio
async def test_no_restart_pending_when_both_real(monkeypatch):
    from routers.admin_analytics import live_model_mode

    monkeypatch.setenv("MOCK_LLM", "false")
    with patch("routers.admin_analytics._require_admin", new=AsyncMock(return_value={"user_id": "founder"})), \
         patch("routers.admin_analytics.require_db", return_value=_fake_db()), \
         patch("services.ora_chat_v2.llm_client.is_mock", return_value=False):
        res = await live_model_mode(authorization="Bearer x")

    assert res["mode"] == "real"
    assert res["restart_pending"] is False
