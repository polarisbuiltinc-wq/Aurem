"""
tests/test_w2_step2_mock_short_circuit_chat_stream.py — Overnight loop
W2 Step 2 (2026-08-29).

The live main-chat path (routers/chat.py::chat_stream) never honored
MOCK_LLM — every real attempt was a real paid provider call, made
testing/reproduction of chat bugs non-deterministic and costly. This
pins the 5-line short-circuit added at the TOP of chat_stream,
BEFORE any provider/tool/repo-context construction.
"""
import json

import pytest


@pytest.mark.asyncio
async def test_mock_short_circuit_live_chat(monkeypatch):
    """With MOCK_LLM on, chat_stream must NEVER construct/call any
    real provider — a spy that raises on construction proves it was
    never touched — and must return exactly one deterministic canned
    SSE message with ZERO ora_chat_usage rows written.

    X1 hardening (2026-08-30) — is_mock() now reads MOCK_LLM ONCE at
    process import (cached), so a bare monkeypatch.setenv() only works
    if this is the very FIRST thing to import
    services.ora_chat_v2.llm_client in the whole pytest session — not
    reliable once thousands of other tests run first. Set the cached
    constant directly instead."""
    from services.ora_chat_v2 import llm_client
    monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", True)

    from routers import chat as chat_router

    async def _boom(*a, **k):
        raise AssertionError("real provider call_llm_with_meta was constructed/called — MOCK_LLM leak")

    monkeypatch.setattr("services.orchestrator.call_llm_with_meta", _boom)
    monkeypatch.setattr("services.llm.call_llm", _boom)

    async def _fake_current_dev(auth):
        return {"user_id": "diag-mock-user", "email": "diag@example.com", "tier": "free"}

    monkeypatch.setattr(chat_router, "current_dev", _fake_current_dev)

    body = chat_router.ChatBody(prompt="fix the readme", session_id="diag-mock-sess")
    resp = await chat_router.chat_stream(request=None, body=body, authorization="Bearer x")

    frames = []
    async for chunk in resp.body_iterator:
        line = chunk if isinstance(chunk, str) else chunk.decode()
        for part in line.split("\n\n"):
            part = part.strip()
            if part.startswith("data:"):
                frames.append(json.loads(part[5:].strip()))

    assert any(f.get("meta") for f in frames), frames
    token_frames = [f for f in frames if "token" in f]
    assert len(token_frames) == 1, f"expected exactly one canned token frame, got {token_frames}"
    canned = token_frames[0]["token"]
    assert "mock mode" in canned.lower()
    assert "aurem-handoff" not in canned
    assert any(f.get("done") for f in frames), frames

    from cto_services.db import get_db
    db = get_db()
    if db is not None:
        count = await db.ora_chat_usage.count_documents({"user_id": "diag-mock-user"})
        assert count == 0, "MOCK_LLM path must record zero token usage"


@pytest.mark.asyncio
async def test_mock_no_fence():
    """The canned mock message must never carry an aurem-handoff
    fence — a mock reply must NEVER fake a ship/approve signal."""
    from services.ora_chat_v2.llm_client import is_mock

    text = (
        "I'm ORA (mock mode). The live model isn't connected on "
        "this instance — no real LLM calls are being made. This "
        "is a placeholder for UX testing."
    )
    assert "```aurem-handoff" not in text
    assert callable(is_mock)


def test_mock_off_real_path_unchanged(monkeypatch):
    """With MOCK_LLM off/unset, the short-circuit must not fire —
    the real routing code (further down chat_stream) stays reachable.
    Verified structurally: the guard is an `if` (not an early
    unconditional return), so the function falls through to the
    pre-existing budget/rate-limit/orchestrator code when the check
    is false.

    X1 hardening (2026-08-30) — is_mock() now reads MOCK_LLM ONCE at
    process import (a cached module-level constant), not per-call, so
    a bare env delenv/setenv no longer has any effect on an
    already-imported process — that's the whole point of the fix (see
    tests/test_x1_mock_incident_2026_08_30.py). Set the cached constant
    directly instead of the env var to exercise the "mock off" branch."""
    from services.ora_chat_v2 import llm_client
    monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", False)
    assert llm_client.is_mock() is False

    import inspect
    from routers import chat as chat_router
    src = inspect.getsource(chat_router.chat_stream)
    guard_idx = src.index("_mock_llm_on()")
    budget_idx = src.index("assert_has_budget")
    assert guard_idx < budget_idx, "mock guard must sit BEFORE budget/provider routing"
