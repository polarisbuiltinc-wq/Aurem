"""test_anti_fabrication_regen_admin.py — 2026-08-19, ported 2026-09-08

Anti-Fabrication Regen (admin "Ask ORA" tool, /ora-chat/message).

Founder-reported: Iter 388-ak backlog said this was "NOT STARTED".
On inspection the corrective-retry logic already existed (Iter 264
Fix A5) but was dormant behind `ORA_REGEN_ON_FABRICATION` (default
OFF, never set anywhere) — so a fabricated file path was detected +
logged, but the reply was never actually regenerated before reaching
the founder. Two original fixes:

  1. `services/ora_chat/adversarial_review.py::trigger_reason()` now
     fires on a HARD `fabricated` claim on its own (previously only
     fired on soft `unverified` claims).
  2. `backend/.env` now sets `ORA_REGEN_ON_FABRICATION=1`.

2026-09-08 audit follow-up (Decision 1) — root-caused: the 2026-08-27
"ORA Chat v2 rebuild" rewired `/ora-chat/message` to
`services.ora_chat_v2.engine.run_turn`, which had ZERO grounding-check
or regen logic (confirmed by grep at the time — this whole feature was
silently orphaned, not intentionally dropped). The fabrication-check
IS the technical enforcement of the product's core "no fabrication"
promise, so it was ported into `run_turn` itself (reusing the same
`services.ora_chat.grounding_check.run_post_response_check` used
everywhere else in the codebase — no new detection logic invented).

This test now drives the REAL `/ora-chat/message` endpoint in-process
(TestClient) with `services.ora_chat_v2.llm_client.stream_chat` stubbed
so the first draft deterministically fabricates a file path, and
verifies:
  - the fabricated path never reaches the client (the v2 engine
    computes the full round's text BEFORE chunking it into deltas, so
    the regen check runs before ANY delta for that round is yielded)
  - the corrective (2nd) draft is what actually streams + persists
  - the persisted assistant turn's final SSE event carries
    `ungrounded: []` (clean) after a successful regen
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

os.environ["ORA_REGEN_ON_FABRICATION"] = "1"

from main import app  # noqa: E402
import routers.ora_chat as ora_chat_mod  # noqa: E402
from services.ora_chat import adversarial_review  # noqa: E402
from services.ora_chat_v2 import llm_client  # noqa: E402

FABRICATED_PATH = "services/definitely_fake_module_9182.py"
CLEAN_REPLY = ("I don't have a specific file to point to for that — "
               "I'd need to /read the relevant module first.")


def _admin_user():
    return {"user_id": "test-ora-admin", "email": "test@aurem.dev",
            "is_admin": True, "is_founder": True, "tier": "founder"}


async def _fake_stream_chat(*, messages, tools=None, reasoning=False,
                            vision=False, max_tokens=2000, db=None,
                            user_id=None):
    """First round (tools is a non-empty list — the normal tool-loop
    call) fabricates a file path. The corrective regen call (engine.py
    always passes `tools=None` for it) returns the clean reply. This
    mirrors exactly how the real engine distinguishes the two calls."""
    if tools:
        yield {"type": "resolved", "model": "mock-v3", "label": "mock"}
        yield {"type": "delta",
               "content": f"I checked `{FABRICATED_PATH}` and it handles this."}
        yield {"type": "usage", "input_tokens": 12, "output_tokens": 18}
        yield {"type": "done"}
    else:
        yield {"type": "resolved", "model": "mock-v3", "label": "mock"}
        yield {"type": "delta", "content": CLEAN_REPLY}
        yield {"type": "usage", "input_tokens": 10, "output_tokens": 12}
        yield {"type": "done"}


@pytest.fixture
def client(monkeypatch):
    async def _fake_require_admin(authorization=None):
        return _admin_user()
    monkeypatch.setattr(ora_chat_mod, "require_admin", _fake_require_admin)
    monkeypatch.setattr(llm_client, "stream_chat", _fake_stream_chat)
    with TestClient(app) as c:
        yield c


def test_trigger_reason_fires_on_fabricated_alone():
    """A HARD fabricated claim with zero unverified claims must
    trigger a review pass (previously returned None) — unrelated unit
    test for the adversarial-review module, unaffected by the v2
    port, kept as regression coverage."""
    grounding = {"fabricated": ["services/does_not_exist.py"],
                 "unverified": []}
    assert adversarial_review.trigger_reason([], grounding) == "grounding_fabricated"
    assert adversarial_review.trigger_reason(
        [], {"fabricated": [], "unverified": ["x.py"]}) == "grounding_unverified"
    assert adversarial_review.trigger_reason(["HIGH_STAKES"], None) == "high_stakes_label"
    assert adversarial_review.trigger_reason([], {"fabricated": [], "unverified": []}) is None


def test_regen_on_fabrication_rewrites_before_reaching_client(client):
    """The anti-fabrication guarantee is live in v2: a fabricated
    draft must never reach the client; the corrective clean draft
    must be what streams + persists."""
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
    assert final["ungrounded"] == [], (
        f"final event still flags fabrication after regen: {final}"
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
