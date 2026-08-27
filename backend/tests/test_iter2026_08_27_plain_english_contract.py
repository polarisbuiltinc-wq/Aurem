"""Regression — Plain-English Output Contract, Phase 1/2 (2026-08-27).

Flag-gated (`explain_plain_english_v1`, default OFF, allowlist
`test_admin_001`) instruction block injected into `extra_sys` ONLY for
council-mode "A" (conversational/explain) turns on the main chat
surface (never Ask Advisor, never a mutation-shaped B/C/D/E/F turn).
See `PLAIN_ENGLISH_EXPLAIN_CONTRACT` in `routers/chat.py`.

T1 — explain turn, flag ON -> contract IS injected into the system
     prompt reaching the LLM call.
T2 — mutation-shaped turn (would ship/confirm), flag ON -> contract is
     NOT injected (ship/confirm answers keep full file:line detail).
T3 — flag OFF (non-allowlisted user) -> byte-identical to pre-feature
     behavior (contract never injected, `system` unaffected).
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from routers.chat import PLAIN_ENGLISH_EXPLAIN_CONTRACT


ALLOWLISTED_USER = {"user_id": "test_admin_001", "email": "admin@example.com",
                     "tier": "founder", "is_admin": True, "created_at": time.time()}
REGULAR_USER = {"user_id": "u_regular", "email": "user@example.com",
                 "tier": "pro", "is_admin": False, "created_at": time.time()}


class _FakeDB:
    def __getattr__(self, name):
        from unittest.mock import MagicMock
        return MagicMock()

    def __getitem__(self, name):
        return self.__getattr__(name)


def _make_client(current_user: dict):
    from routers import chat as router_mod
    from cto_services import db as _dbmod
    _dbmod.set_db(_FakeDB())

    async def _fake_current_dev(authorization=None):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return current_user

    old_current_dev = router_mod.current_dev
    router_mod.current_dev = _fake_current_dev
    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    c = TestClient(app)
    return c, router_mod, old_current_dev, _dbmod


def _flag_side_effect(allowlisted_user_id):
    async def _is_enabled(flag, user_id=None, tier=None):
        if flag != "explain_plain_english_v1":
            return False
        return user_id == allowlisted_user_id
    return _is_enabled


class TestPlainEnglishContractInjection:
    def test_t1_explain_turn_flag_on_injects_contract(self):
        client, router_mod, old_dev, _dbmod = _make_client(ALLOWLISTED_USER)
        captured = {}

        async def _spy_chat_with_tools(**kwargs):
            captured["system"] = kwargs.get("system") or ""
            return {"content": "answer", "provider": "deepseek", "meta": {}}

        try:
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.ora_council_retriever.get_council_few_shot",
                       AsyncMock(return_value=("", 0))), \
                 patch("services.feature_flags.is_enabled",
                       AsyncMock(side_effect=_flag_side_effect("test_admin_001"))), \
                 patch("routers.chat.chat_with_tools", _spy_chat_with_tools), \
                 patch("services.response_confidence.response_seems_mismatched", return_value=False), \
                 patch("routers.chat._deduct_tokens", AsyncMock(return_value=500)):
                r = client.post(
                    "/api/aurem-dev/chat/send",
                    headers={"Authorization": "Bearer test_admin_001"},
                    json={"prompt": "how do the agents in my project work? explain simply",
                          "project_id": "home", "session_id": "s1"},
                )
        finally:
            router_mod.current_dev = old_dev
            _dbmod.set_db(None)

        assert r.status_code == 200, r.text
        assert r.json()["plain_english_contract_active"] is True
        assert PLAIN_ENGLISH_EXPLAIN_CONTRACT in captured["system"]

    def test_t2_mutation_shaped_turn_flag_on_does_not_inject(self):
        client, router_mod, old_dev, _dbmod = _make_client(ALLOWLISTED_USER)
        captured = {}

        async def _spy_chat_with_tools(**kwargs):
            captured["system"] = kwargs.get("system") or ""
            return {"content": "```aurem-handoff\nfix it\n```", "provider": "deepseek", "meta": {}}

        try:
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.ora_council_retriever.get_council_few_shot",
                       AsyncMock(return_value=("", 0))), \
                 patch("services.feature_flags.is_enabled",
                       AsyncMock(side_effect=_flag_side_effect("test_admin_001"))), \
                 patch("routers.chat.chat_with_tools", _spy_chat_with_tools), \
                 patch("services.response_confidence.response_seems_mismatched", return_value=False), \
                 patch("routers.chat._deduct_tokens", AsyncMock(return_value=500)):
                r = client.post(
                    "/api/aurem-dev/chat/send",
                    headers={"Authorization": "Bearer test_admin_001"},
                    json={"prompt": "fix the deployment error and ship it via CTO",
                          "project_id": "home", "session_id": "s1"},
                )
        finally:
            router_mod.current_dev = old_dev
            _dbmod.set_db(None)

        assert r.status_code == 200, r.text
        assert r.json()["plain_english_contract_active"] is False
        assert PLAIN_ENGLISH_EXPLAIN_CONTRACT not in captured["system"]

    def test_t3_flag_off_non_allowlisted_user_byte_identical(self):
        client, router_mod, old_dev, _dbmod = _make_client(REGULAR_USER)
        captured = {}

        async def _spy_chat_with_tools(**kwargs):
            captured["system"] = kwargs.get("system") or ""
            return {"content": "answer", "provider": "deepseek", "meta": {}}

        try:
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.ora_council_retriever.get_council_few_shot",
                       AsyncMock(return_value=("", 0))), \
                 patch("services.feature_flags.is_enabled",
                       AsyncMock(side_effect=_flag_side_effect("test_admin_001"))), \
                 patch("routers.chat.chat_with_tools", _spy_chat_with_tools), \
                 patch("services.response_confidence.response_seems_mismatched", return_value=False), \
                 patch("routers.chat._deduct_tokens", AsyncMock(return_value=500)):
                r = client.post(
                    "/api/aurem-dev/chat/send",
                    headers={"Authorization": "Bearer u_regular"},
                    json={"prompt": "how do the agents in my project work? explain simply",
                          "project_id": "home", "session_id": "s1"},
                )
        finally:
            router_mod.current_dev = old_dev
            _dbmod.set_db(None)

        assert r.status_code == 200, r.text
        assert r.json()["plain_english_contract_active"] is False
        assert PLAIN_ENGLISH_EXPLAIN_CONTRACT not in captured["system"]
        # byte-identical rollback proof: no trace of the contract marker
        assert "FOUNDER-FACING EXPLANATION CONTRACT" not in captured["system"]

    def test_ask_advisor_never_gets_the_contract(self):
        """Ask Advisor (ora_panel=true) is a distinct surface — the
        contract must never inject there even if flag is ON, since
        `_recall_mode_send` is only computed for non-advisor turns."""
        client, router_mod, old_dev, _dbmod = _make_client(ALLOWLISTED_USER)
        captured = {}

        async def _spy_chat_with_tools(**kwargs):
            captured["system"] = kwargs.get("system") or ""
            return {"content": "answer", "provider": "deepseek", "meta": {}}

        try:
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.feature_flags.is_enabled",
                       AsyncMock(side_effect=_flag_side_effect("test_admin_001"))), \
                 patch("routers.chat.chat_with_tools", _spy_chat_with_tools), \
                 patch("services.response_confidence.response_seems_mismatched", return_value=False), \
                 patch("routers.chat._deduct_tokens", AsyncMock(return_value=500)):
                r = client.post(
                    "/api/aurem-dev/chat/send",
                    headers={"Authorization": "Bearer test_admin_001"},
                    json={"prompt": "how do the agents in my project work? explain simply",
                          "project_id": "home", "session_id": "s1", "ora_panel": True},
                )
        finally:
            router_mod.current_dev = old_dev
            _dbmod.set_db(None)

        assert r.status_code == 200, r.text
        assert r.json()["plain_english_contract_active"] is False
        assert PLAIN_ENGLISH_EXPLAIN_CONTRACT not in captured["system"]
