"""test_citation_guard_persist_ordering.py — 2026-08-19

Customer Chat Regen — CitationGuard (Iter 209) already auto-corrects a
fabricated file-path claim on the LIVE stream (a `reset: True` token
frame overwrites the draft the user sees). This test proves a
previously-undiscovered gap: `_persist_turn()` ran BEFORE the
CitationGuard block, so the ORIGINAL uncorrected draft — the one WITH
the fabricated path — was what got written to Mongo. A page refresh
(`GET /chat/history`) showed the fabricated draft even though the
live viewer never saw it.

Fix: moved the `_persist_turn()` call in `chat_stream()` (routers/
chat.py) to AFTER the CitationGuard block, so it always persists the
final (possibly corrected) `content`.

This test drives the REAL `/api/aurem-dev/chat/stream` endpoint
in-process (TestClient), with `chat_with_tools` and
`services.orchestrator.respond_text` stubbed so the draft
deterministically claims a file path that was never read this turn.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from main import app
import routers.chat as chat_mod
from services import llm as llm_mod

FABRICATED_PATH = "services/billing/definitely_fake_invoice_engine.py"
CORRECTED_TEXT = "I checked and there's no dedicated invoice-engine file — billing lives in services/payment_reconciliation.py."


def _test_user():
    return {"user_id": "test-customer-1", "email": "customer@example.com",
            "is_founder": False, "is_unlimited": True, "tier": "founder"}


async def _fake_current_dev(authorization=None):
    return _test_user()


async def _fake_chat_with_tools(**kwargs):
    """First draft — fabricates a file path with ZERO matching
    read_repo_file/read_repo_files tool call this turn, so
    CitationGuard.verify() must flag it as unverified."""
    return {
        "content": f"That's handled in `{FABRICATED_PATH}`.",
        "provider": "test-provider",
        "tool_calls": [{"tool": "read_repo_file",
                        "args": {"path": "services/payment_reconciliation.py"}}],
        "tool_invocations": [],
        "tool_calls_run": 1,
        "messages": [{"role": "user", "content": "who handles billing?"}],
    }


async def _fake_call_llm(messages=None, system="", **kwargs):
    """The CitationGuard corrective retry (fixed to call the real,
    existing `services.llm.call_llm`, not the phantom `respond_text`)."""
    return CORRECTED_TEXT


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(chat_mod, "current_dev", _fake_current_dev)
    monkeypatch.setattr(chat_mod, "chat_with_tools", _fake_chat_with_tools)
    monkeypatch.setattr(llm_mod, "call_llm", _fake_call_llm)

    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr("services.usage.assert_has_budget", _noop)
    monkeypatch.setattr("services.usage.assert_has_task_budget", _noop)

    with TestClient(app) as c:
        yield c


def test_persisted_turn_reflects_citation_guard_correction(client):
    session_id = "test-sess-citation-guard-ordering"
    r = client.post(
        "/api/aurem-dev/chat/stream",
        headers={"Authorization": "Bearer fake"},
        json={"prompt": "who handles billing?", "session_id": session_id,
              "max_tool_iters": 1},
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert FABRICATED_PATH not in body, (
        "fabricated path leaked into the SSE stream — CitationGuard "
        "did not suppress it"
    )

    events = [json.loads(line[len("data: "):])
              for line in body.splitlines() if line.startswith("data: ")]
    done = next((e for e in events if e.get("done")), None)
    assert done is not None, f"no done frame in stream: {body[:500]}"
    assert done["citation_guard_triggered"] is True, (
        "citation_guard_triggered flag not set — guard didn't fire"
    )

    hist_r = client.get(
        "/api/aurem-dev/chat/history", params={"session_id": session_id},
        headers={"Authorization": "Bearer fake"},
    )
    assert hist_r.status_code == 200, hist_r.text
    turns = hist_r.json()["messages"]
    last_turn = turns[-1]
    assert last_turn["role"] == "assistant"
    assert FABRICATED_PATH not in last_turn["content"], (
        "Mongo-persisted turn STILL has the fabricated path — the "
        "reload-visible copy was never fixed even though the live "
        "stream was corrected"
    )
    assert last_turn["content"] == CORRECTED_TEXT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
