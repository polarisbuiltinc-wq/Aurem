"""test_response_confidence_mismatch_gate.py — 2026-08-21

Founder-reported production bug: a fresh Pro-mode session asked the
trivial question "What is 5+5?" and got back an unrelated GitHub-auth
"Root cause" diagnosis WITH an `aurem-handoff` fence — i.e. an
unsolicited "Ship via CTO" proposal for a question that has nothing
to do with code or bugs. See services/response_confidence.py.

This drives the REAL `/api/aurem-dev/chat/stream` and
`/api/aurem-dev/chat/send` endpoints in-process (TestClient), with
`chat_with_tools` stubbed to return exactly that mismatched shape, and
asserts:
  1. The user-visible content is the friendly fallback message, not
     the mismatched diagnosis.
  2. The `aurem-handoff` fence is gone — the ShipDialog button can
     never render for it (client-side regex on the fence text).
  3. A LEGITIMATE fix request (query carries fix/bug intent) still
     gets its diagnosis + handoff fence through untouched — no
     over-blocking regression.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from main import app
import routers.chat as chat_mod

MISMATCHED_REPLY = (
    "Root cause: The API endpoint requires admin access but the "
    "request is being made without proper authentication/authorization.\n\n"
    "```aurem-handoff\n"
    '{"title": "Fix auth", "files": ["app.py"]}\n'
    "```"
)
LEGIT_FIX_REPLY = (
    "Root cause: missing null check on the checkout handler.\n\n"
    "```aurem-handoff\n"
    '{"title": "Fix checkout null check", "files": ["checkout.py"]}\n'
    "```"
)


def _test_user():
    return {"user_id": "test-customer-2", "email": "customer2@example.com",
            "is_founder": False, "is_unlimited": True, "tier": "founder"}


async def _fake_current_dev(authorization=None):
    return _test_user()


def _make_fake_chat_with_tools(reply_text):
    async def _fake(**kwargs):
        return {
            "content": reply_text,
            "provider": "test-provider",
            "tool_calls": [],
            "tool_invocations": [],
            "tool_calls_run": 0,
            "messages": [{"role": "user", "content": kwargs.get("prompt", "")}],
        }
    return _fake


@pytest.fixture
def client_factory(monkeypatch):
    monkeypatch.setattr(chat_mod, "current_dev", _fake_current_dev)

    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr("services.usage.assert_has_budget", _noop)
    monkeypatch.setattr("services.usage.assert_has_task_budget", _noop)

    def _build(reply_text):
        monkeypatch.setattr(chat_mod, "chat_with_tools", _make_fake_chat_with_tools(reply_text))
        return TestClient(app)

    return _build


def _stream_content(body: str) -> str:
    events = [json.loads(line[len("data: "):])
              for line in body.splitlines() if line.startswith("data: ")]
    tokens = [e.get("token", "") for e in events if "token" in e]
    return "".join(tokens)


def test_mismatched_response_swapped_for_fallback_in_stream(client_factory):
    client = client_factory(MISMATCHED_REPLY)
    r = client.post(
        "/api/aurem-dev/chat/stream",
        headers={"Authorization": "Bearer fake"},
        json={"prompt": "What is 5+5?",
              "session_id": "test-sess-mismatch-gate",
              "max_tool_iters": 1},
    )
    assert r.status_code == 200, r.text
    content = _stream_content(r.text)
    assert "aurem-handoff" not in content, (
        "handoff fence leaked into the stream — Ship via CTO could still render"
    )
    assert "root cause" not in content.lower()
    assert "I couldn't find a confident answer" in content

    events = [json.loads(line[len("data: "):])
              for line in r.text.splitlines() if line.startswith("data: ")]
    meta = next((e for e in events if e.get("meta") and "low_confidence" in e), None)
    done = next((e for e in events if e.get("done")), None)
    assert meta is not None and meta.get("low_confidence") is True, (
        "Confidence Badge: meta frame did not flag low_confidence"
    )
    assert done is not None and done.get("low_confidence") is True, (
        "Confidence Badge: done frame did not flag low_confidence"
    )


def test_mismatched_response_swapped_for_fallback_in_send(client_factory):
    client = client_factory(MISMATCHED_REPLY)
    r = client.post(
        "/api/aurem-dev/chat/send",
        headers={"Authorization": "Bearer fake"},
        json={"prompt": "What is 5+5?",
              "session_id": "test-sess-mismatch-gate-send",
              "max_tool_iters": 1},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    content = payload.get("content", "")
    assert "aurem-handoff" not in content
    assert "I couldn't find a confident answer" in content
    assert payload.get("low_confidence") is True, (
        "Confidence Badge: /chat/send response did not flag low_confidence"
    )


def test_legit_fix_request_keeps_handoff_fence(client_factory):
    """No over-blocking: a query that DOES carry fix/bug intent must
    still get its diagnosis + Ship via CTO fence through untouched.
    (Phrased to avoid separately triggering mode_d_debugger's own
    stack-trace-required bail path — unrelated pre-existing behaviour,
    not part of this gate — while still carrying fix-intent tokens.)"""
    client = client_factory(LEGIT_FIX_REPLY)
    r = client.post(
        "/api/aurem-dev/chat/stream",
        headers={"Authorization": "Bearer fake"},
        json={"prompt": "Please add a fix for the checkout button not working",
              "session_id": "test-sess-legit-fix-gate",
              "max_tool_iters": 1},
    )
    assert r.status_code == 200, r.text
    content = _stream_content(r.text)
    assert "aurem-handoff" in content, (
        "legitimate fix-intent request was incorrectly suppressed"
    )
    assert "root cause" in content.lower()

    events = [json.loads(line[len("data: "):])
              for line in r.text.splitlines() if line.startswith("data: ")]
    done = next((e for e in events if e.get("done")), None)
    assert done is not None and done.get("low_confidence") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
