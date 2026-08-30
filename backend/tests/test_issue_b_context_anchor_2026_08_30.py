"""Issue B fix (2026-08-30) — casual-tier chat replies lost the
in-progress task's intent on a short follow-up.

Root cause (confirmed via source read, NOT a guess):
  1. `core.intent_gateway.classify()` is called with `history=[]`
     hardcoded at BOTH chat.py call sites (chat_send + chat_stream),
     a deliberate 2s-latency tradeoff — documented in the existing
     inline comments.
  2. `services.intent_gateway_casual_reply.casual_direct_reply()`
     called the LLM (DeepSeek via OpenRouter, `services.llm.call_llm`)
     with ONLY the current message — `[{"role":"user","content":prompt}]`,
     zero conversation history, by construction.
  This is a genuine "zero turns of history passed" bug, not a weak-
  model/short-context-window cap — model/context length was never the
  limiting factor since no history reached the model at all.

Fix: a new `prior_turn_context_text()` (response_confidence.py, same
cheap single-doc `$slice: -1` query shape as the existing
`prior_turn_had_fix_signal`) fetches just the immediately-prior
assistant turn's text, threaded through both chat.py call sites into
`casual_direct_reply(prompt, prior_assistant_text=...)`, which now
appends an explicit anchor instruction to the system prompt when
present. No behavior change when there's no prior turn (fresh "hi").
"""
from __future__ import annotations

import inspect
import os
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


@pytest.fixture(scope="module")
def headers():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    return {"Authorization": f"Bearer {r.json()['token']}", "Content-Type": "application/json"}


# ── Unit — prior_turn_context_text ──────────────────────────────────
class TestPriorTurnContextText:

    @pytest.mark.asyncio
    async def test_returns_none_without_db(self):
        from services.response_confidence import prior_turn_context_text
        assert await prior_turn_context_text(None, "sid", "uid") is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_turns(self):
        from services.response_confidence import prior_turn_context_text

        class _FakeDB:
            class chat_sessions:
                @staticmethod
                async def find_one(*a, **kw):
                    return {"turns": []}
        assert await prior_turn_context_text(_FakeDB(), "sid", "uid") is None

    @pytest.mark.asyncio
    async def test_returns_none_when_last_turn_is_user(self):
        from services.response_confidence import prior_turn_context_text

        class _FakeDB:
            class chat_sessions:
                @staticmethod
                async def find_one(*a, **kw):
                    return {"turns": [{"role": "user", "content": "hi"}]}
        assert await prior_turn_context_text(_FakeDB(), "sid", "uid") is None

    @pytest.mark.asyncio
    async def test_returns_text_when_last_turn_is_assistant(self):
        from services.response_confidence import prior_turn_context_text

        class _FakeDB:
            class chat_sessions:
                @staticmethod
                async def find_one(*a, **kw):
                    return {"turns": [{"role": "assistant", "content": "reading auth.py"}]}
        out = await prior_turn_context_text(_FakeDB(), "sid", "uid")
        assert out == "reading auth.py"

    @pytest.mark.asyncio
    async def test_fails_open_on_exception(self):
        from services.response_confidence import prior_turn_context_text

        class _FakeDB:
            class chat_sessions:
                @staticmethod
                async def find_one(*a, **kw):
                    raise RuntimeError("mongo down")
        assert await prior_turn_context_text(_FakeDB(), "sid", "uid") is None


# ── Unit — casual_direct_reply anchor instruction ───────────────────
class TestCasualReplyContextAnchor:

    def test_signature_accepts_prior_assistant_text(self):
        from services.intent_gateway_casual_reply import casual_direct_reply
        sig = inspect.signature(casual_direct_reply)
        assert "prior_assistant_text" in sig.parameters
        assert sig.parameters["prior_assistant_text"].default is None

    @pytest.mark.asyncio
    async def test_t_followup_anchored_to_intent_no_context_unchanged(self, monkeypatch):
        """No prior turn (fresh 'hi') — system prompt unchanged, no
        anchor block injected. Proves zero regression for real casual
        chit-chat."""
        import services.intent_gateway_casual_reply as mod
        captured = {}

        async def fake_llm(messages, system=None, max_tokens=None, temperature=None):
            captured["system"] = system or ""
            return "Hey there!"

        import services.llm as llm_mod
        monkeypatch.setattr(llm_mod, "call_llm", fake_llm, raising=False)

        out = await mod.casual_direct_reply("hi", prior_assistant_text=None)
        assert out == "Hey there!"
        assert "immediately-prior message" not in captured["system"]

    @pytest.mark.asyncio
    async def test_t_followup_anchored_to_intent_context_injected(self, monkeypatch):
        """With a prior assistant turn present, the anchor block must
        carry that context AND the explicit 'do not ask, answer
        in-thread' instruction."""
        import services.intent_gateway_casual_reply as mod
        captured = {}

        async def fake_llm(messages, system=None, max_tokens=None, temperature=None):
            captured["system"] = system or ""
            return "Still scanning — no critical issues found yet."

        import services.llm as llm_mod
        monkeypatch.setattr(llm_mod, "call_llm", fake_llm, raising=False)

        prior = "Let me dig deeper into your codebase to find real issues — I need to read the rest of auth.py..."
        out = await mod.casual_direct_reply("i didnt find any ?", prior_assistant_text=prior)
        assert out == "Still scanning — no critical issues found yet."
        sys_text = captured["system"]
        assert "immediately-prior message" in sys_text
        assert "read the rest of auth.py" in sys_text
        assert "Do NOT ask them to clarify" in sys_text


# ── Live E2E — the exact founder-reported 3-turn sequence ───────────
def _send(prompt, headers, session_id):
    return requests.post(f"{API}/chat/send", headers=headers,
                          json={"prompt": prompt, "session_id": session_id, "max_tool_iters": 1},
                          timeout=180)


class TestLiveFollowupSequence:

    def test_t_followup_answers_in_thread_not_reclarify(self, headers):
        sid = f"issueB_followup_{uuid.uuid4().hex[:8]}"
        # Turn 1 — the original ask (kept short so it's cheap; real intent
        # doesn't need to actually trigger a repo scan for this test).
        r1 = _send("take a look in my project and see if you can find any issues", headers, sid)
        assert r1.status_code == 200, r1.text[:300]

        # Turn 2 — the exact one-word-ish elliptical follow-up from the report.
        r2 = _send("i didnt find any ?", headers, sid)
        assert r2.status_code == 200, r2.text[:300]
        data = r2.json()
        content = (data.get("content") or "").lower()
        assert "clarify what you're looking for" not in content, (
            f"REGRESSION: follow-up still dropped the in-progress intent: {content[:300]}"
        )
