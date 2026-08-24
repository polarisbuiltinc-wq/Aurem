"""
Points 4 (Unified Mode / house-rules reuse in casual_direct_reply) and
Point 6 (RAG contamination root-cause fix: raised _MIN_SCORE + low_confidence
exclusion + logger wiring) — 2026-01 regression tests.

These tests are ADDITIVE on top of test_intent_gateway_casual_boundary_2026_01.py
(Point 3 regression) — Point 3 checks are already covered by that suite; this
file focuses on the two NEW pieces from this batch.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import uuid

import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://bin-context-pat.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"

FOUNDER_TEST_MSG = (
    "I'm not a coder. Can you tell me in simple words what this website "
    "does and if it's working okay right now?"
)
BANNED_TOKENS = ["ship via cto", "root cause:", "```aurem-handoff", "commit"]


# ─── Fixtures ────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def auth_token():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}


def _send(prompt, headers, session_id, max_tool_iters=4):
    return requests.post(
        f"{API}/chat/send",
        headers=headers,
        json={"prompt": prompt, "session_id": session_id, "max_tool_iters": max_tool_iters},
        timeout=180,
    )


def _stream(prompt, headers, session_id):
    r = requests.post(
        f"{API}/chat/stream",
        headers=headers,
        json={"prompt": prompt, "session_id": session_id, "max_tool_iters": 4},
        timeout=180,
        stream=True,
    )
    intent, result, final_content = None, None, ""
    try:
        for raw in r.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            payload = raw[len("data:"):].strip()
            if not payload:
                continue
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            if obj.get("type") == "intent" or ("intent" in obj and "tier" in (obj.get("intent") or {})):
                intent = obj.get("intent") or intent
            if obj.get("type") == "result":
                result = obj.get("result") or obj
            if "content" in obj and isinstance(obj["content"], str):
                final_content = obj["content"] or final_content
            if obj.get("done") and isinstance(obj.get("content"), str):
                final_content = obj["content"] or final_content
    finally:
        r.close()
    return r.status_code, intent, result, final_content


# ─────────────────────────────────────────────────────────────────────
# Point 4 — Unified Mode: casual_direct_reply reuses advisor house rules.
# ─────────────────────────────────────────────────────────────────────
class TestPoint4CasualReplyHouseRulesReuse:
    """Verifies casual_direct_reply now looks up advisor house rules
    and prepends them, but only as an ENHANCEMENT (never a hard dep)."""

    def test_casual_reply_calls_advisor_house_rules(self):
        # Source-level assertion (cheap + deterministic in preview
        # where the DB house_rules doc is OFF by default).
        import services.intent_gateway_casual_reply as mod
        src = inspect.getsource(mod.casual_direct_reply)
        assert "get_active_house_rules" in src
        assert "\"advisor\"" in src or "'advisor'" in src
        assert "format_house_rules_block" in src
        # Wrapped in try/except so DB failures don't crash casual replies.
        assert "except" in src

    @pytest.mark.asyncio
    async def test_casual_reply_no_crash_when_house_rules_off(self, monkeypatch):
        """Default Preview state: house rules OFF → falls back to plain
        system prompt, must not raise."""
        import services.intent_gateway_casual_reply as mod

        async def fake_llm(messages, system=None, max_tokens=None, temperature=None):
            # Assert the system prompt is still the plain casual one
            # (no ADMIN HOUSE RULES header since rules are empty).
            assert "ADMIN HOUSE RULES" not in (system or "")
            assert "You are ORA" in (system or "")
            return "Hi! AUREM helps you ship code."

        monkeypatch.setattr(mod, "call_llm", fake_llm, raising=False)
        # Also patch via the actual import path used inside the function.
        import services.llm as llm_mod
        monkeypatch.setattr(llm_mod, "call_llm", fake_llm, raising=False)

        out = await mod.casual_direct_reply("hi")
        assert isinstance(out, str) and out.strip()

    @pytest.mark.asyncio
    async def test_casual_reply_prepends_house_rules_when_enabled(self, monkeypatch):
        """When admin has advisor house rules enabled, they must appear
        prepended to the system prompt with the ADMIN HOUSE RULES header."""
        import services.intent_gateway_casual_reply as mod
        import services.house_rules as hr

        async def fake_active(target, mode):
            assert target == "advisor"
            return "Always answer in pirate voice."

        monkeypatch.setattr(hr, "get_active_house_rules", fake_active, raising=True)

        captured = {}

        async def fake_llm(messages, system=None, max_tokens=None, temperature=None):
            captured["system"] = system or ""
            return "Ahoy!"

        import services.llm as llm_mod
        monkeypatch.setattr(llm_mod, "call_llm", fake_llm, raising=False)

        out = await mod.casual_direct_reply("hi there")
        assert out == "Ahoy!"
        sys_text = captured.get("system", "")
        assert "ADMIN HOUSE RULES" in sys_text, sys_text[:400]
        assert "pirate voice" in sys_text, sys_text[:400]
        # Original casual prompt still present AFTER the house rules block.
        assert "You are ORA" in sys_text
        assert sys_text.index("ADMIN HOUSE RULES") < sys_text.index("You are ORA")

    @pytest.mark.asyncio
    async def test_casual_reply_swallows_house_rules_exception(self, monkeypatch):
        """If the house_rules lookup itself raises, casual_direct_reply
        must NOT propagate (graceful degrade to plain system prompt)."""
        import services.intent_gateway_casual_reply as mod
        import services.house_rules as hr

        async def boom(target, mode):
            raise RuntimeError("mongo down")

        monkeypatch.setattr(hr, "get_active_house_rules", boom, raising=True)

        async def fake_llm(messages, system=None, max_tokens=None, temperature=None):
            return "still works"

        import services.llm as llm_mod
        monkeypatch.setattr(llm_mod, "call_llm", fake_llm, raising=False)

        out = await mod.casual_direct_reply("hi")
        assert out == "still works"


# ─────────────────────────────────────────────────────────────────────
# Point 6 — RAG contamination root-cause fix.
# ─────────────────────────────────────────────────────────────────────
class TestPoint6MinScoreAndQualityFilter:

    def test_min_score_raised_to_0_42(self):
        from services.ora_council_retriever import _MIN_SCORE
        assert _MIN_SCORE == 0.42, f"expected 0.42, got {_MIN_SCORE}"

    def test_quality_filter_excludes_low_confidence(self):
        from services.ora_council_retriever import _quality_filter
        doc = {
            "user_message": "x", "final_output": "y", "mode": "A",
            "low_confidence": True,
        }
        assert _quality_filter(doc) is False

    def test_quality_filter_accepts_normal_conversational(self):
        from services.ora_council_retriever import _quality_filter
        doc = {"user_message": "x", "final_output": "y", "mode": "A", "low_confidence": False}
        assert _quality_filter(doc) is True

    def test_quality_filter_accepts_missing_low_confidence_key(self):
        from services.ora_council_retriever import _quality_filter
        doc = {"user_message": "x", "final_output": "y", "mode": "A"}
        assert _quality_filter(doc) is True

    def test_quality_filter_mode_c_fail_still_excluded(self):
        """Pre-existing behavior: mode C without pass_result stays excluded."""
        from services.ora_council_retriever import _quality_filter
        doc = {"user_message": "x", "final_output": "y", "mode": "C", "pass_result": False}
        assert _quality_filter(doc) is False

    def test_quality_filter_mode_c_pass_still_included(self):
        from services.ora_council_retriever import _quality_filter
        doc = {"user_message": "x", "final_output": "y", "mode": "C", "pass_result": True}
        assert _quality_filter(doc) is True

    def test_rebuild_index_projection_includes_low_confidence(self):
        """Ensure _rebuild_index projects the low_confidence field from Mongo."""
        import services.ora_council_retriever as rt
        src = inspect.getsource(rt._rebuild_index)
        assert "low_confidence" in src, "low_confidence must be in Mongo projection"


class TestPoint6LoggerWiring:

    def test_log_conversational_signature_has_low_confidence(self):
        from services.ora_council_logger import log_conversational
        sig = inspect.signature(log_conversational)
        assert "low_confidence" in sig.parameters
        p = sig.parameters["low_confidence"]
        assert p.default is False

    def test_build_log_stores_low_confidence(self):
        from services.ora_council_logger import _build_log
        doc = _build_log(
            mode="A", user_message="u", final_output="o", agent_used="ora",
            low_confidence=True,
        )
        assert doc.get("low_confidence") is True
        doc2 = _build_log(mode="A", user_message="u", final_output="o", agent_used="ora")
        assert doc2.get("low_confidence") is False  # default

    def test_chat_stream_passes_low_confidence_to_logger(self):
        """chat_stream's log_conversational call site must pass
        low_confidence=_low_confidence."""
        with open("/app/backend/routers/chat.py", "r") as f:
            src = f.read()
        # Find the chat_stream function body region.
        m = re.search(r"async def chat_stream\(", src)
        assert m, "chat_stream not found"
        # From here to next @router. or 'async def '
        rest = src[m.start():]
        end = re.search(r"\n@router\.|\nasync def [a-z_]+\(", rest[1:])
        stream_body = rest[: end.start() + 1] if end else rest
        assert "log_conversational" in stream_body
        # Find the call block and confirm low_confidence=_low_confidence is passed.
        call_match = re.search(
            r"log_conversational\((.*?)\)",
            stream_body,
            re.DOTALL,
        )
        assert call_match, "no log_conversational(...) call in chat_stream"
        args = call_match.group(1)
        assert "low_confidence=_low_confidence" in args, (
            f"low_confidence not wired into chat_stream logger call. Got:\n{args[:500]}"
        )

    def test_chat_send_does_not_call_log_conversational(self):
        """Confirmed pre-existing scope boundary: chat_send never logs
        to ora_council_logs. This is NOT a regression; just guard against
        someone accidentally adding it without matching wiring."""
        with open("/app/backend/routers/chat.py", "r") as f:
            src = f.read()
        m = re.search(r"async def chat_send\(", src)
        assert m
        rest = src[m.start():]
        end = re.search(r"\n@router\.|\nasync def [a-z_]+\(", rest[1:])
        send_body = rest[: end.start() + 1] if end else rest
        assert "log_conversational" not in send_body, (
            "chat_send now calls log_conversational — either wire "
            "low_confidence there too, or this test needs updating."
        )


# ─────────────────────────────────────────────────────────────────────
# Point 3 REGRESSION — light re-verification on live API.
# (Full Point-3 suite lives in test_intent_gateway_casual_boundary_2026_01.py;
#  this file only re-runs the founder's exact acceptance-bar tests to
#  confirm Points 4/6 edits didn't break Point 3.)
# ─────────────────────────────────────────────────────────────────────
class TestPoint3AcceptanceBarRegression:

    def test_contaminated_session_send_still_casual(self, headers):
        sid = f"p46_send_contam_{uuid.uuid4().hex[:8]}"
        r1 = _send("fix the deployment error and ship it via CTO", headers, sid, max_tool_iters=1)
        assert r1.status_code == 200, r1.text[:300]
        r2 = _send(FOUNDER_TEST_MSG, headers, sid)
        assert r2.status_code == 200, r2.text[:300]
        data = r2.json()
        assert data.get("tier") == "casual", (
            f"REGRESSION: expected tier=casual, got {data.get('tier')} intent={data.get('intent')}"
        )
        content = (data.get("content") or "").lower()
        for banned in BANNED_TOKENS:
            assert banned not in content, (
                f"REGRESSION: banned token {banned!r} in casual reply: {content[:300]}"
            )
        # Persist the verbatim response for the report.
        with open("/app/test_reports/pytest/p46_send_verbatim.txt", "w") as f:
            f.write(data.get("content") or "")

    def test_contaminated_session_stream_still_casual(self, headers):
        sid = f"p46_stream_contam_{uuid.uuid4().hex[:8]}"
        _stream("fix the deployment error and ship it via CTO", headers, sid)
        status, intent, result, content = _stream(FOUNDER_TEST_MSG, headers, sid)
        assert status == 200
        assert intent is not None
        assert intent.get("tier") == "casual", (
            f"REGRESSION STREAM: tier={intent.get('tier')}"
        )
        low = (content or "").lower()
        for banned in BANNED_TOKENS:
            assert banned not in low, f"REGRESSION STREAM: banned {banned!r} in: {low[:300]}"
        with open("/app/test_reports/pytest/p46_stream_verbatim.txt", "w") as f:
            f.write(content or "")

    def test_tool_request_still_routes_to_orchestrator(self, headers):
        sid = f"p46_tool_{uuid.uuid4().hex[:8]}"
        _send("fix the deployment error and ship it via CTO", headers, sid, max_tool_iters=1)
        r = _send("read README.md and tell me what it says", headers, sid)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data.get("tier") in ("query", "agentic"), f"tier={data.get('tier')}"
        assert data.get("provider") != "intent-gateway-casual", (
            f"tool request short-circuited to casual: provider={data.get('provider')}"
        )


# ─────────────────────────────────────────────────────────────────────
# Unrelated admin insights — regression only (unchanged).
# ─────────────────────────────────────────────────────────────────────
class TestAdminInsightsRegression:

    def test_confidence_checks_endpoint(self, headers):
        r = requests.get(f"{API}/admin/insights/confidence-checks", headers=headers, timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert isinstance(data, (list, dict))

    def test_cost_alert_endpoint(self, headers):
        r = requests.get(f"{API}/admin/insights/cost-alert", headers=headers, timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert isinstance(data, (list, dict))
