"""Regression — "Show the Outcome, Never the Engine" P1 (2026-08-27).

The mechanical NET under the plain-English prompt instruction
(services/output_guard.py) + the scan-severity plain-scale calibration
(services/full_scan_orchestrator.py).

T-egress-no-machinery — a leaked machinery token in the model's real
    output gets stripped before the user sees it (the 3x-leak class).
T-plain-length-under-cap — a real over-long answer gets capped to the
    ~500-token target via one re-summarize pass.
T-scan-severity-calibrated — an internal-only-reachable finding never
    displays at top severity; a real externally-reachable/secret
    finding is unaffected.
T-ship-card-keeps-fileline (flip side) — the net does not fire outside
    the explain-only gate; a mutation-shaped turn's content is
    untouched.
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch


ALLOWLISTED_USER = {"user_id": "test_admin_001", "email": "admin@example.com",
                     "tier": "founder", "is_admin": True, "created_at": time.time()}


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


def _flag_on_for(user_id):
    async def _is_enabled(flag, user_id=None, tier=None):
        return flag == "explain_plain_english_v1" and user_id == "test_admin_001"
    return _is_enabled


class TestEgressNoMachineryLeak:
    def test_leaked_machinery_tokens_stripped_from_real_response(self):
        client, router_mod, old_dev, _dbmod = _make_client(ALLOWLISTED_USER)
        leaky_answer = (
            "Your project uses a 5-adviser council (chairman: the lead "
            "model) reached via deepseek true. Details live in "
            "backend/services/ora_council_retriever.py and are logged to "
            "ora_council_logs. It's built with pydantic and asyncio."
        )

        async def _fake_chat_with_tools(**kwargs):
            return {"content": leaky_answer, "provider": "deepseek", "meta": {}}

        try:
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.ora_council_retriever.get_council_few_shot",
                       AsyncMock(return_value=("", 0))), \
                 patch("services.feature_flags.is_enabled",
                       AsyncMock(side_effect=_flag_on_for("test_admin_001"))), \
                 patch("routers.chat.chat_with_tools", _fake_chat_with_tools), \
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
        body = r.json()
        assert body["plain_english_contract_active"] is True
        assert body["leak_stripped"] is True
        assert body["output_guard_ref_id"] and body["output_guard_ref_id"].startswith("ORA-")
        out = body["content"]
        for banned in ("5-adviser council", "chairman", "via deepseek true",
                       "ora_council_retriever.py", "ora_council_logs",
                       "pydantic", "asyncio"):
            assert banned not in out, f"leaked token {banned!r} still present"


class TestLengthCapNet:
    def test_over_long_answer_gets_capped(self):
        client, router_mod, old_dev, _dbmod = _make_client(ALLOWLISTED_USER)
        long_answer = " ".join(["word"] * 700)  # ~930 approx tokens, over the 500 cap
        compressed = "A short, plain-English summary of the same idea. Want the technical detail?"

        async def _fake_chat_with_tools(**kwargs):
            return {"content": long_answer, "provider": "deepseek", "meta": {}}

        async def _fake_call_llm_with_meta(*a, **kw):
            return {"content": compressed, "ok": True}

        try:
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.ora_council_retriever.get_council_few_shot",
                       AsyncMock(return_value=("", 0))), \
                 patch("services.feature_flags.is_enabled",
                       AsyncMock(side_effect=_flag_on_for("test_admin_001"))), \
                 patch("routers.chat.chat_with_tools", _fake_chat_with_tools), \
                 patch("services.llm._meta.call_llm_with_meta", _fake_call_llm_with_meta), \
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
        body = r.json()
        assert body["length_capped"] is True
        assert body["content"] == compressed
        assert len(body["content"].split()) < 700


class TestShipCardKeepsFileline:
    def test_mutation_turn_not_touched_by_output_guard(self):
        client, router_mod, old_dev, _dbmod = _make_client(ALLOWLISTED_USER)
        ship_answer = "```aurem-handoff\nfix backend/services/x.py line 42\n```"

        async def _fake_chat_with_tools(**kwargs):
            return {"content": ship_answer, "provider": "deepseek", "meta": {}}

        try:
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.ora_council_retriever.get_council_few_shot",
                       AsyncMock(return_value=("", 0))), \
                 patch("services.feature_flags.is_enabled",
                       AsyncMock(side_effect=_flag_on_for("test_admin_001"))), \
                 patch("routers.chat.chat_with_tools", _fake_chat_with_tools), \
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
        body = r.json()
        assert body["plain_english_contract_active"] is False
        assert body["leak_stripped"] is False
        assert body["length_capped"] is False
        assert body["content"] == ship_answer
        assert "backend/services/x.py line 42" in body["content"]


class TestScanSeverityCalibrated:
    def test_internal_only_finding_never_top_severity(self):
        from services.full_scan_orchestrator import plain_severity_and_exploitability
        plain, exploit = plain_severity_and_exploitability(
            "lpdos_no_body_limit_deep", "medium")
        assert exploit == "internal"
        assert plain != "needs your attention"

    def test_external_secret_finding_stays_top_severity(self):
        from services.full_scan_orchestrator import plain_severity_and_exploitability
        plain, exploit = plain_severity_and_exploitability(
            "secret_aws_access_key_deep", "critical")
        assert exploit == "external"
        assert plain == "needs your attention"

    def test_normalise_adds_plain_severity_field(self):
        from services.full_scan_orchestrator import _normalise
        f = _normalise({"rule_id": "lpdos_no_body_limit_deep",
                        "severity": "MEDIUM", "file": "a.py", "line": 3,
                        "message": "x"}, "vanguard")
        assert "plain_severity" in f and "exploitability" in f
        assert f["severity"] == "medium"  # raw field unchanged

    def test_ship_block_reason_shows_plain_scale_not_raw_caps(self):
        from services.loop_full_scan import format_ship_block_reason
        offending = {
            "a.py": [{"rule_id": "lpdos_no_body_limit_deep", "line": 3,
                      "severity": "medium", "plain_severity": "minor",
                      "message": "missing body-size limit"}],
        }
        out = format_ship_block_reason(offending)
        assert "[MEDIUM]" not in out
        assert "[minor]" in out
