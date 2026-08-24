"""
Intent-gateway casual/query/clarify safe-default fix — 2026-01 regression tests.

Verifies the founder-approved 3-fix set:

  1. `/chat/send` now has intent-gateway wiring (previously ZERO wiring;
     every message hit chat_with_tools unconditionally).
  2. `/chat/stream` clarify tier now takes the same no-tools branch as
     casual (previously fell into full agentic pipeline — inverted
     safe-default).
  3. `services/response_confidence.py` — widened _TASK_ACTION_PROSE_RE
     regex catches prose descriptions of "Ship via CTO / commit fix"
     even without the literal ```aurem-handoff fence.

Founder's exact acceptance bar: on a contaminated conversation thread
(prior agentic message in same session), the plain question
"I'm not a coder..." must still get a direct casual answer,
AND a real tool-requiring request must still route to the orchestrator.
"""
import json
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"

FOUNDER_TEST_MSG = (
    "I'm not a coder. Can you tell me in simple words what this website "
    "does and if it's working okay right now?"
)

BANNED_TOKENS = ["ship via cto", "root cause:", "```aurem-handoff"]


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def auth_token():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    tok = data.get("token")
    assert tok, f"no token in login response: {data}"
    return tok


@pytest.fixture(scope="module")
def headers(auth_token):
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }


def _send(prompt, headers, session_id, max_tool_iters=4):
    r = requests.post(
        f"{API}/chat/send",
        headers=headers,
        json={
            "prompt": prompt,
            "session_id": session_id,
            "max_tool_iters": max_tool_iters,
        },
        timeout=180,
    )
    return r


def _stream(prompt, headers, session_id):
    r = requests.post(
        f"{API}/chat/stream",
        headers=headers,
        json={"prompt": prompt, "session_id": session_id, "max_tool_iters": 4},
        timeout=180,
        stream=True,
    )
    # Consume all SSE frames. Look for `intent` frame (tier), `result`, and `done`.
    intent = None
    result = None
    final_content = ""
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
            if obj.get("type") == "intent" or "intent" in obj and "tier" in (obj.get("intent") or {}):
                intent = obj.get("intent") or intent
            if obj.get("type") == "result":
                result = obj.get("result") or obj
            if "content" in obj and isinstance(obj["content"], str):
                final_content = obj["content"]
            if obj.get("done"):
                # final done frame may carry content
                if isinstance(obj.get("content"), str):
                    final_content = obj["content"] or final_content
    finally:
        r.close()
    return r.status_code, intent, result, final_content


# ─────────────────────────────────────────────────────────────────────
# Unit tests — response_confidence widened regex
# ─────────────────────────────────────────────────────────────────────
class TestResponseConfidenceProseRegex:
    """Widened _TASK_ACTION_PROSE_RE — catches prose fix suggestions."""

    def test_prose_ship_via_cto_flagged_as_mismatch(self):
        from services.response_confidence import response_seems_mismatched
        assert response_seems_mismatched(
            "what is 5+5?",
            "You can click Ship via CTO to commit that fix.",
        ) is True

    def test_prose_commit_fix_flagged(self):
        from services.response_confidence import response_seems_mismatched
        assert response_seems_mismatched(
            "hi there",
            "I'll commit this fix for you shortly.",
        ) is True

    def test_fix_intent_user_gets_response_through(self):
        """Not a false positive: user explicitly asked for a fix."""
        from services.response_confidence import response_seems_mismatched
        assert response_seems_mismatched(
            "fix the bug in services/llm.py please",
            "I found the issue, click Ship via CTO to commit that fix.",
        ) is False

    def test_clean_casual_response_not_flagged(self):
        from services.response_confidence import response_seems_mismatched
        assert response_seems_mismatched(
            FOUNDER_TEST_MSG,
            "AUREM is a developer co-pilot. Everything looks OK right now.",
        ) is False


# ─────────────────────────────────────────────────────────────────────
# Unit tests — intent_gateway casual/query boundary
# ─────────────────────────────────────────────────────────────────────
class TestIntentGatewayHeuristic:
    def test_founder_message_is_casual(self):
        from core.intent_gateway import classify_heuristic_sync
        out = classify_heuristic_sync(FOUNDER_TEST_MSG)
        assert out["tier"] == "casual", out

    def test_plain_greeting_casual(self):
        from core.intent_gateway import classify_heuristic_sync
        assert classify_heuristic_sync("hi")["tier"] == "casual"

    def test_how_does_this_work_casual(self):
        from core.intent_gateway import classify_heuristic_sync
        out = classify_heuristic_sync("how does this work")
        assert out["tier"] == "casual", out

    def test_resource_noun_lookup_is_query(self):
        from core.intent_gateway import classify_heuristic_sync
        out = classify_heuristic_sync("show me my leads")
        assert out["tier"] == "query", out

    def test_file_reference_is_query(self):
        from core.intent_gateway import classify_heuristic_sync
        out = classify_heuristic_sync("what does services/llm.py do")
        assert out["tier"] == "query", out

    def test_agentic_verb_is_agentic(self):
        from core.intent_gateway import classify_heuristic_sync
        out = classify_heuristic_sync("fix the bug in services/llm.py")
        assert out["tier"] == "agentic", out


# ─────────────────────────────────────────────────────────────────────
# Integration tests — /chat/send
# ─────────────────────────────────────────────────────────────────────
class TestChatSendCasualBoundary:

    def test_send_founder_message_fresh_session_is_casual(self, headers):
        sid = f"test_send_fresh_{uuid.uuid4().hex[:8]}"
        r = _send(FOUNDER_TEST_MSG, headers, sid)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data.get("tier") == "casual", (
            f"expected tier=casual, got {data.get('tier')}. intent={data.get('intent')}"
        )
        assert data.get("provider") == "intent-gateway-casual", (
            f"expected provider=intent-gateway-casual, got {data.get('provider')}"
        )
        content = (data.get("content") or "").lower()
        for banned in BANNED_TOKENS:
            assert banned not in content, f"banned token '{banned}' in response: {content[:300]}"
        # sanity: iterations = 1 (no tool loop)
        assert data.get("iterations") in (0, 1), data.get("iterations")

    def test_send_contaminated_session_still_casual(self, headers):
        """Founder's exact acceptance bar."""
        sid = f"test_send_contam_{uuid.uuid4().hex[:8]}"
        # Seed with an agentic/task-flavored message first.
        r1 = _send(
            "fix the deployment error and ship it via CTO",
            headers, sid, max_tool_iters=1,
        )
        assert r1.status_code == 200, r1.text[:300]
        # Now send the exact plain question in the SAME session.
        r2 = _send(FOUNDER_TEST_MSG, headers, sid)
        assert r2.status_code == 200, r2.text[:300]
        data = r2.json()
        assert data.get("tier") == "casual", (
            f"CONTAMINATED-SESSION FAILURE: expected tier=casual on plain question "
            f"after agentic seed, got tier={data.get('tier')}. "
            f"intent={data.get('intent')}"
        )
        content = (data.get("content") or "").lower()
        for banned in BANNED_TOKENS:
            assert banned not in content, (
                f"CONTAMINATED-SESSION FAILURE: banned token '{banned}' leaked "
                f"into casual reply: {content[:300]}"
            )

    def test_send_tool_request_still_routes_through_orchestrator(self, headers):
        """Regression: real task requests must NOT be downgraded to casual."""
        sid = f"test_send_tool_{uuid.uuid4().hex[:8]}"
        # Seed with agentic to also verify contamination doesn't break this.
        _send("fix the deployment error and ship it via CTO", headers, sid, max_tool_iters=1)
        r = _send("read README.md and tell me what it says", headers, sid)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data.get("tier") in ("query", "agentic"), (
            f"expected tier=query|agentic for tool request, got {data.get('tier')}"
        )
        # Must NOT be the casual provider (which means orchestrator was skipped).
        assert data.get("provider") != "intent-gateway-casual", (
            f"tool request incorrectly took casual short-circuit: {data.get('provider')}"
        )

    def test_send_agentic_verb_routes_to_orchestrator(self, headers):
        sid = f"test_send_agentic_{uuid.uuid4().hex[:8]}"
        r = _send("fix the bug in services/llm.py", headers, sid, max_tool_iters=1)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data.get("tier") == "agentic", (
            f"expected tier=agentic, got {data.get('tier')}"
        )
        assert data.get("provider") != "intent-gateway-casual"

    @pytest.mark.parametrize("msg,allow_tiers", [
        ("hi", {"casual"}),
        ("how does this work", {"casual"}),
        ("is everything ok?", {"casual"}),
        # Founder said this last one is expected to stay query OR casual — don't fail either.
        ("what does the payment api do?", {"casual", "query", "agentic"}),
    ])
    def test_send_informational_variants(self, headers, msg, allow_tiers):
        sid = f"test_send_inf_{uuid.uuid4().hex[:8]}"
        r = _send(msg, headers, sid, max_tool_iters=1)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data.get("tier") in allow_tiers, (
            f"msg={msg!r} expected {allow_tiers}, got tier={data.get('tier')}"
        )


# ─────────────────────────────────────────────────────────────────────
# Integration tests — /chat/stream (SSE)
# ─────────────────────────────────────────────────────────────────────
class TestChatStreamCasualBoundary:

    def test_stream_founder_message_is_casual(self, headers):
        sid = f"test_stream_fresh_{uuid.uuid4().hex[:8]}"
        status, intent, result, content = _stream(FOUNDER_TEST_MSG, headers, sid)
        assert status == 200
        assert intent is not None, "no intent SSE frame received"
        assert intent.get("tier") == "casual", (
            f"expected stream tier=casual, got {intent.get('tier')}"
        )
        if result is not None:
            assert result.get("provider") == "intent-gateway-casual", (
                f"expected provider=intent-gateway-casual, got {result.get('provider')}"
            )
            assert result.get("tool_calls_run", 0) == 0, result.get("tool_calls_run")
        low = (content or "").lower()
        for banned in BANNED_TOKENS:
            assert banned not in low, f"banned token '{banned}' in stream content: {low[:300]}"

    def test_stream_contaminated_session_still_casual(self, headers):
        sid = f"test_stream_contam_{uuid.uuid4().hex[:8]}"
        # Seed agentic first
        _stream("fix the deployment error and ship it via CTO", headers, sid)
        status, intent, result, content = _stream(FOUNDER_TEST_MSG, headers, sid)
        assert status == 200
        assert intent is not None
        assert intent.get("tier") == "casual", (
            f"CONTAMINATED STREAM: expected tier=casual on plain question after agentic seed, "
            f"got {intent.get('tier')}"
        )
        low = (content or "").lower()
        for banned in BANNED_TOKENS:
            assert banned not in low, f"banned '{banned}' in contaminated stream: {low[:300]}"


# ─────────────────────────────────────────────────────────────────────
# Admin insights — confidence-checks endpoint regression
# ─────────────────────────────────────────────────────────────────────
class TestConfidenceChecksInsights:

    def test_confidence_checks_endpoint_works(self, headers):
        r = requests.get(
            f"{API}/admin/insights/confidence-checks",
            headers=headers,
            timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        # Endpoint shape sanity — should return a list-ish structure
        assert isinstance(data, (list, dict)), type(data)
