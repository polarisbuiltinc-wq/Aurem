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
# Mismatched, but with NO aurem-handoff fence — a bare "Root cause:"
# diagnosis never renders a Ship via CTO button on its own (see
# MessageBubble.jsx), so this must flag low_confidence but NOT
# ship_suppressed.
MISMATCHED_NO_FENCE_REPLY = (
    "Root cause: The API endpoint requires admin access but the "
    "request is being made without proper authentication/authorization."
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


def _make_stateful_chat_with_tools(first_reply, second_reply):
    """First call returns `first_reply`, every call after returns
    `second_reply` — simulates the founder's own observation that a
    manual retry of the SAME question produces the correct answer."""
    calls = {"n": 0}

    async def _fake(**kwargs):
        calls["n"] += 1
        reply = first_reply if calls["n"] == 1 else second_reply
        return {
            "content": reply,
            "provider": "test-provider",
            "tool_calls": [],
            "tool_invocations": [],
            "tool_calls_run": 0,
            "messages": [{"role": "user", "content": kwargs.get("prompt", "")}],
        }
    return _fake, calls


def test_mismatch_auto_retry_resolves_silently(monkeypatch, client_factory):
    """Layer (d): when the retry produces a correct answer, the user
    should see the CORRECT content, never the mismatched first draft,
    and low_confidence must be False (self-corrected, not a fallback)."""
    fake, calls = _make_stateful_chat_with_tools(MISMATCHED_REPLY, "5 + 5 = 10.")
    client = client_factory("unused")  # placeholder, overridden below
    monkeypatch.setattr(chat_mod, "chat_with_tools", fake)

    r = client.post(
        "/api/aurem-dev/chat/send",
        headers={"Authorization": "Bearer fake"},
        json={"prompt": "What is 5+5?",
              "session_id": "test-sess-retry-success",
              "max_tool_iters": 1},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload.get("content") == "5 + 5 = 10."
    assert payload.get("low_confidence") is False
    assert calls["n"] == 2, "expected exactly one retry call"


def test_normal_qa_never_shows_ship_suppressed_note(client_factory):
    """A normal, non-fix question (founder's own '5+5' example) must
    never flag ship_suppressed — there was never a Ship suggestion to
    suppress in the first place."""
    client = client_factory("5 + 5 = 10.")
    r = client.post(
        "/api/aurem-dev/chat/send",
        headers={"Authorization": "Bearer fake"},
        json={"prompt": "What is 5+5?",
              "session_id": "test-sess-ship-suppressed-normal",
              "max_tool_iters": 1},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload.get("low_confidence") is False
    assert payload.get("ship_suppressed") is False


def test_mismatch_with_handoff_fence_flags_ship_suppressed_send(client_factory):
    """A mismatched reply that DOES carry a real ```aurem-handoff fence
    (i.e. a Ship via CTO button would have rendered) must flag
    ship_suppressed=True once the guard swaps it for the fallback."""
    client = client_factory(MISMATCHED_REPLY)
    r = client.post(
        "/api/aurem-dev/chat/send",
        headers={"Authorization": "Bearer fake"},
        json={"prompt": "What is 5+5?",
              "session_id": "test-sess-ship-suppressed-fence-send",
              "max_tool_iters": 1},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload.get("low_confidence") is True
    assert payload.get("ship_suppressed") is True
    assert "aurem-handoff" not in payload.get("content", ""), (
        "Ship button must never render for a ship_suppressed turn"
    )


def test_mismatch_with_handoff_fence_flags_ship_suppressed_stream(client_factory):
    client = client_factory(MISMATCHED_REPLY)
    r = client.post(
        "/api/aurem-dev/chat/stream",
        headers={"Authorization": "Bearer fake"},
        json={"prompt": "What is 5+5?",
              "session_id": "test-sess-ship-suppressed-fence-stream",
              "max_tool_iters": 1},
    )
    assert r.status_code == 200, r.text
    events = [json.loads(line[len("data: "):])
              for line in r.text.splitlines() if line.startswith("data: ")]
    meta = next((e for e in events if e.get("meta") and "ship_suppressed" in e), None)
    done = next((e for e in events if e.get("done")), None)
    assert meta is not None and meta.get("ship_suppressed") is True
    assert done is not None and done.get("ship_suppressed") is True


def test_mismatch_without_handoff_fence_does_not_flag_ship_suppressed(client_factory):
    """Mismatched (bare "Root cause:" text, no fence) → low_confidence
    True, but ship_suppressed must stay False since no Ship button was
    ever going to render — nothing code-change-shaped was suppressed."""
    client = client_factory(MISMATCHED_NO_FENCE_REPLY)
    r = client.post(
        "/api/aurem-dev/chat/send",
        headers={"Authorization": "Bearer fake"},
        json={"prompt": "What is 5+5?",
              "session_id": "test-sess-ship-suppressed-no-fence",
              "max_tool_iters": 1},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload.get("low_confidence") is True
    assert payload.get("ship_suppressed") is False


def test_legit_fix_request_no_ship_suppressed(client_factory):
    """No over-flagging: a legitimate fix-intent request that KEEPS
    its handoff fence must never flag ship_suppressed (nothing was
    suppressed — it went through)."""
    client = client_factory(LEGIT_FIX_REPLY)
    r = client.post(
        "/api/aurem-dev/chat/stream",
        headers={"Authorization": "Bearer fake"},
        json={"prompt": "Please add a fix for the checkout button not working",
              "session_id": "test-sess-ship-suppressed-legit",
              "max_tool_iters": 1},
    )
    assert r.status_code == 200, r.text
    events = [json.loads(line[len("data: "):])
              for line in r.text.splitlines() if line.startswith("data: ")]
    done = next((e for e in events if e.get("done")), None)
    assert done is not None and done.get("ship_suppressed") is False


def test_descriptive_question_mentioning_file_or_api_still_gets_caught():
    """2026-08-22 — regression for the founder-reported INTERMITTENT
    recurrence: a plain descriptive question like "what does the
    payment api do?" or "where's the config file for auth?" mentions
    a word ("api", "file", "config") that used to be in
    _FIX_INTENT_TOKENS purely because it's ALSO a word a real fix
    request might use — but these are read-only, informational
    questions, not fix requests. If the LLM ever returns an unrelated
    diagnosis + Ship proposal for one of these, it must still be
    caught as a mismatch, not waved through just because the question
    happened to mention "api"/"file"/"config"."""
    from services.response_confidence import response_seems_mismatched

    assert response_seems_mismatched(
        "what does the payment api do?", MISMATCHED_REPLY,
    ) is True
    assert response_seems_mismatched(
        "where's the config file for auth?", MISMATCHED_REPLY,
    ) is True
    assert response_seems_mismatched(
        "can you explain how the test suite is organized?", MISMATCHED_REPLY,
    ) is True


def test_legit_fix_request_with_real_action_verb_still_passes_through():
    """Regression guard for the narrowed token set: genuine fix/change
    requests using the RETAINED action verbs must still sail through
    untouched — the narrowing must not have collateral-damaged the
    core "Ship via CTO" flow for legitimate requests."""
    from services.response_confidence import response_seems_mismatched

    for prompt in [
        "please fix the checkout button, it's broken",
        "can you add a dark mode toggle to settings",
        "the login endpoint is crashing, can you debug it",
        "refactor the payment handler to remove the old code path",
    ]:
        assert response_seems_mismatched(prompt, LEGIT_FIX_REPLY) is False, prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
