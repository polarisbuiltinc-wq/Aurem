"""test_anti_fabrication_regen_admin.py — 2026-08-19

Anti-Fabrication Regen (admin "Ask ORA" tool, /ora-chat/message).

Founder-reported: Iter 388-ak backlog said this was "NOT STARTED".
On inspection the corrective-retry logic already existed (Iter 264
Fix A5) but was dormant behind `ORA_REGEN_ON_FABRICATION` (default
OFF, never set anywhere) — so a fabricated file path was detected +
logged, but the reply was never actually regenerated before reaching
the founder. Two fixes:

  1. `services/ora_chat/adversarial_review.py::trigger_reason()` now
     fires on a HARD `fabricated` claim on its own (previously only
     fired on soft `unverified` claims — a reply with ONLY a
     fabricated path and no unverified ones got zero review pass on
     the deep-research path).
  2. `backend/.env` now sets `ORA_REGEN_ON_FABRICATION=1`, switching
     on the general-chat path's silent corrective retry.

This test drives the REAL `/ora-chat/message` endpoint in-process
(TestClient) with `stream_call`/`one_shot` stubbed so the first draft
deterministically fabricates a file path, and verifies:
  - the fabricated path never reaches the client (deltas are
    buffered while `ORA_REGEN_ON_FABRICATION=1`)
  - the corrective (2nd) draft is what actually streams + persists
  - the persisted assistant turn has `ungrounded=None` (clean)
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

os.environ["ORA_REGEN_ON_FABRICATION"] = "1"

from main import app  # noqa: E402
import routers.ora_chat as ora_chat_mod  # noqa: E402
from services.ora_chat import adversarial_review, cost_tracker  # noqa: E402

FABRICATED_PATH = "services/definitely_fake_module_9182.py"
CLEAN_REPLY = ("I don't have a specific file to point to for that — "
               "I'd need to /read the relevant module first.")


def _admin_user():
    return {"user_id": "test-ora-admin", "email": "test@aurem.dev",
            "is_admin": True, "is_founder": True, "tier": "founder"}


async def _fake_stream_call(*, model, messages, temperature, top_p,
                            presence_penalty, max_tokens):
    """First (fabricating) draft — the only stream_call this test needs."""
    yield {"type": "delta",
           "content": f"I checked `{FABRICATED_PATH}` and it handles this."}
    yield {"type": "usage", "input_tokens": 12, "output_tokens": 18}
    yield {"type": "done"}


async def _fake_one_shot(*, model, messages, temperature, top_p,
                         presence_penalty, max_tokens):
    """Used for intent-classify (garbage-tolerant) AND the Fix A5
    corrective retry — always returns the clean, non-fabricating text."""
    return CLEAN_REPLY, {"input_tokens": 10, "output_tokens": 12}, None


async def _fake_budget_status():
    """Force `economy` mode — this ALSO forces the deep-research
    classifier off (send_message skips it entirely in economy mode)
    and forces the adversarial-review hostile-reviewer call to skip
    (budget guard), so this test exercises ONLY the Fix A5 backstop
    in isolation, with zero real LLM network calls."""
    return {"mode": "economy", "day_cap_usd": 5.0,
            "day_spent_usd": 4.99, "spike_cap_usd": 999.0}


@pytest.fixture
def client(monkeypatch):
    async def _fake_require_admin(authorization=None):
        return _admin_user()
    monkeypatch.setattr(ora_chat_mod, "require_admin", _fake_require_admin)
    monkeypatch.setattr(ora_chat_mod, "stream_call", _fake_stream_call)
    monkeypatch.setattr(ora_chat_mod, "one_shot", _fake_one_shot)
    monkeypatch.setattr(cost_tracker, "budget_status", _fake_budget_status)
    with TestClient(app) as c:
        yield c


def test_trigger_reason_fires_on_fabricated_alone():
    """Fix 1 — a HARD fabricated claim with zero unverified claims
    must trigger a review pass (previously returned None)."""
    grounding = {"fabricated": ["services/does_not_exist.py"],
                 "unverified": []}
    assert adversarial_review.trigger_reason([], grounding) == "grounding_fabricated"
    # Regression — unverified-only + high-stakes paths still work.
    assert adversarial_review.trigger_reason(
        [], {"fabricated": [], "unverified": ["x.py"]}) == "grounding_unverified"
    assert adversarial_review.trigger_reason(["HIGH_STAKES"], None) == "high_stakes_label"
    assert adversarial_review.trigger_reason([], {"fabricated": [], "unverified": []}) is None


def test_regen_on_fabrication_rewrites_before_reaching_client(client):
    """Fix 2 (env flag ON) — the fabricated draft must never reach the
    client; the corrective clean draft must be what streams + persists."""
    sess_r = client.post(
        "/api/aurem-dev/ora-chat/sessions",
        headers={"Authorization": "Bearer fake"},
        json={"title": "anti-fab regen test"},
    )
    assert sess_r.status_code == 200, sess_r.text
    session_id = sess_r.json()["session"]["session_id"]

    r = client.post(
        "/api/aurem-dev/ora-chat/message",
        headers={"Authorization": "Bearer fake"},
        json={"session_id": session_id,
              "content": "what handles the request queue?"},
    )
    assert r.status_code == 200, r.text
    body = r.text

    assert FABRICATED_PATH not in body, (
        "fabricated path leaked to the client — regen did not suppress "
        "the bad draft before streaming"
    )

    all_events = [json.loads(line[len("data: "):])
                  for line in body.splitlines() if line.startswith("data: ")]
    streamed_text = "".join(e["content"] for e in all_events
                            if e.get("type") == "delta")
    assert streamed_text == CLEAN_REPLY, (
        f"corrective draft did not stream verbatim, got: {streamed_text!r}"
    )

    final = next((e for e in all_events if e.get("type") == "final"), None)
    assert final is not None, f"no final event in stream: {body[:500]}"
    assert final["ungrounded"] == [], f"final event still flags fabrication: {final}"
    assert not any(e.get("type") == "grounding_warning" for e in all_events), (
        "grounding_warning fired even though regen cleared the fabrication"
    )

    persisted_r = client.get(
        f"/api/aurem-dev/ora-chat/sessions/{session_id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert persisted_r.status_code == 200, persisted_r.text
    last_turn = persisted_r.json()["session"]["messages"][-1]
    assert last_turn["role"] == "assistant"
    assert last_turn["content"] == CLEAN_REPLY, (
        "Mongo-persisted turn still has the fabricated/wrong content"
    )
    assert FABRICATED_PATH not in last_turn["content"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
