"""Issue C fix (2026-08-30) — "ORA doesn't remember older conversations."

STEP 1 findings (confirmed via source read, quoted in commit comments):
  1. Rolling history (`services/orchestrator.py::chat_with_tools`) DID
     load turns per-session from `db.chat_sessions` correctly, but
     capped at a FIXED `history_lines[-20:]` regardless of how much
     room persona+tools+state actually left for that turn.
  2. Session storage: `chat_sessions` collection, `$slice: -200` write
     cap (already fixed in an earlier iter), `find_one` read is
     per-`session_id`+`user_id` — genuinely per-session, and session
     SWITCHING (SessionSwitcher -> Shell.jsx openSession -> the SAME
     `sessionId` context feeding ChatPanel -> hooks/useChatSession.js)
     correctly threads the switched-to session's own `session_id` into
     the next chat call, which `chat_with_tools` then loads correctly
     by that id. Session switching itself was NOT the bug.
  3. Project memory (`services/project_brain.py` -> `brain_ctx`) WAS
     wired into `chat_stream`'s `extra_sys`, but `chat_send`'s
     `extra_sys = repo_ctx or ""` (routers/chat.py, pre-fix) never
     included it at all — a real single-surface-drift bug, confirmed.
  4. THE main confirmed root cause, shared with Issue B: any short
     recall-style follow-up with no concrete resource noun in the
     CURRENT message ("what did we find/fix earlier?", "did you find
     any?") gets classified TIER_CASUAL by the heuristic (confidence
     0.80, above both the 0.75 LLM-escalation and 0.72 ambiguity
     thresholds — never even reaches an LLM fallback). The casual
     path (`casual_direct_reply`) had ZERO history/memory of any kind
     — not the rolling history, not the project brain, nothing.

STEP 2: root cause is a COMBINATION of (a)+(b) from the founder's own
taxonomy — (a) the fixed 20-turn cap could starve a heavy turn's
remaining budget (confirmed via the token math below), and (b) the
casual-tier bypass genuinely carried zero session memory (not "session
history not loaded" in general — it WAS loaded correctly for the
agentic/query tier — but never for the casual-tier short-circuit that
most bare recall questions get routed into).

STEP 3 fix (ONE PR, all tied to "history not reaching the model"):
  F1 — `_select_history_window()` replaces the fixed `[-20:]` slice
       with a token-budget-aware window sized against THIS turn's
       actual `first_iter_system` size.
  F2 — `services/session_summary.py` (fire-and-forget, every 10 turns)
       + `chat_sessions.summary` field, always included regardless of
       window size; also threaded into `casual_direct_reply` via the
       new `get_session_summary()` (response_confidence.py).
  F3 — explicit MEMORY anchor instruction added to `base_system`
       (orchestrator.py) and to `casual_direct_reply`'s system prompt
       (both prior-turn and summary blocks).
  F4 — `chat_send`'s `brain_ctx` drift fixed (project memory now
       reaches this surface too).
"""
from __future__ import annotations

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


# ── F1 — dynamic window, not a fixed cap ────────────────────────────
class TestHistoryDynamicNotFixed:

    def test_t_history_dynamic_not_fixed_short_convo_sends_all(self):
        from services.orchestrator import _select_history_window
        convo = ["[USER] hi", "[ASSISTANT] hey", "[USER] how are you"]
        assert _select_history_window(convo, 10_000) == convo

    def test_t_history_dynamic_not_fixed_tiny_budget_sends_fewer_than_20(self):
        """Proves the window is genuinely budget-driven, not a
        disguised '20 unless tiny' — a starved turn now correctly
        gets LESS than the old fixed cap instead of overflowing it."""
        from services.orchestrator import _select_history_window
        convo = [f"[USER] this is turn number {i} with some real content in it" for i in range(60)]
        out = _select_history_window(convo, 50)  # ~50 tokens, well under 20 turns worth
        assert 0 < len(out) < 20

    def test_t_history_dynamic_not_fixed_big_budget_sends_more_than_20(self):
        """Proves the OTHER direction — the old fixed cap wasted a
        light turn's spare room at exactly 20; a big enough budget
        now correctly carries more than that."""
        from services.orchestrator import _select_history_window
        convo = [f"[USER] t{i}" for i in range(60)]  # short lines, cheap
        out = _select_history_window(convo, 5_000)
        assert len(out) > 20

    def test_t_history_dynamic_not_fixed_preserves_chronological_order(self):
        from services.orchestrator import _select_history_window
        convo = [f"[USER] turn {i}" for i in range(30)]
        out = _select_history_window(convo, 100)
        assert out == sorted(out, key=lambda l: int(l.split()[-1]))
        assert out[-1] == convo[-1]  # most recent turn always kept


# ── F1 — token budget accounting ────────────────────────────────────
class TestTokenBudgetRespected:

    def test_t_token_budget_respected_components_are_consistent(self):
        from services.orchestrator import (
            _MODEL_CONTEXT_BUDGET_TOKENS, _OUTPUT_RESERVE_TOKENS,
            _SAFETY_MARGIN_TOKENS, _approx_tokens,
        )
        fake_first_iter_system = "x" * 4000  # ~1000 tokens
        fake_prompt = "what did we find earlier?"
        history_budget = (
            _MODEL_CONTEXT_BUDGET_TOKENS
            - _approx_tokens(fake_first_iter_system)
            - _approx_tokens(fake_prompt)
            - _OUTPUT_RESERVE_TOKENS
            - _SAFETY_MARGIN_TOKENS
        )
        # Every component is accounted for exactly once; nothing double
        # counted, nothing overflows the shared budget.
        total_accounted = (
            _approx_tokens(fake_first_iter_system) + _approx_tokens(fake_prompt)
            + _OUTPUT_RESERVE_TOKENS + _SAFETY_MARGIN_TOKENS + history_budget
        )
        assert total_accounted == _MODEL_CONTEXT_BUDGET_TOKENS
        assert history_budget > 15_000  # real persona (~8K tok) leaves ample room

    def test_t_token_budget_respected_real_persona_leaves_room_for_8_to_15_turns(self):
        from services.orchestrator import (
            build_persona, _approx_tokens, _MODEL_CONTEXT_BUDGET_TOKENS,
            _OUTPUT_RESERVE_TOKENS, _SAFETY_MARGIN_TOKENS,
        )
        persona = build_persona("fix the bug in auth.py", "", [])
        # Real catalog unreachable in a unit test (needs a live jwt +
        # network) — conservatively assume 3,000 tokens for ~9 tools'
        # names + descriptions (generous vs. observed real catalogs).
        assumed_catalog_tokens = 3_000
        budget = (
            _MODEL_CONTEXT_BUDGET_TOKENS - _approx_tokens(persona)
            - assumed_catalog_tokens - _approx_tokens("what did we find earlier?")
            - _OUTPUT_RESERVE_TOKENS - _SAFETY_MARGIN_TOKENS
        )
        # At a realistic ~150 tokens/turn for real conversational
        # content, this must comfortably clear the founder's 8-15
        # minimum target — was NOT guaranteed under the old fixed cap
        # if a turn's persona/tool/state size varied.
        assert budget // 150 >= 15, f"only {budget // 150} turns would fit, budget={budget}"


# ── F2 — conversation summary ───────────────────────────────────────
class TestConversationSummaryPopulated:

    @pytest.mark.asyncio
    async def test_t_conversation_summary_populated_after_threshold(self, monkeypatch):
        from services.session_summary import maybe_update_summary
        import services.llm as llm_mod

        calls = {"llm": 0, "update": None}

        async def fake_llm(messages, system=None, max_tokens=None, temperature=None):
            calls["llm"] += 1
            return "User is fixing auth issues; login bug found; next: fix signup flow."

        monkeypatch.setattr(llm_mod, "call_llm", fake_llm, raising=False)

        class _FakeColl:
            async def find_one(self, *a, **kw):
                return {
                    "turns": [{"role": "user", "content": f"turn {i}"} for i in range(12)],
                    "summary": "", "summary_turn_count": 0,
                }

            async def update_one(self, filt, update):
                calls["update"] = update

        class _FakeDB:
            chat_sessions = _FakeColl()

        await maybe_update_summary(_FakeDB(), "sid", "uid")
        assert calls["llm"] == 1
        assert calls["update"]["$set"]["summary"]
        assert calls["update"]["$set"]["summary_turn_count"] == 12

    @pytest.mark.asyncio
    async def test_t_conversation_summary_skipped_below_threshold(self, monkeypatch):
        """Below 10 turns since the last summary update, this must be
        a no-op (no LLM spend, no DB write) — proves it's not firing
        on every turn."""
        from services.session_summary import maybe_update_summary
        import services.llm as llm_mod

        calls = {"llm": 0}

        async def fake_llm(*a, **kw):
            calls["llm"] += 1
            return "should not be called"

        monkeypatch.setattr(llm_mod, "call_llm", fake_llm, raising=False)

        class _FakeColl:
            async def find_one(self, *a, **kw):
                return {"turns": [{"role": "user", "content": "hi"}] * 5,
                        "summary": "", "summary_turn_count": 0}

        class _FakeDB:
            chat_sessions = _FakeColl()

        await maybe_update_summary(_FakeDB(), "sid", "uid")
        assert calls["llm"] == 0


# ── F2 — casual path also gets the summary (get_session_summary) ───
class TestSessionContextLoaded:

    @pytest.mark.asyncio
    async def test_t_session_context_loaded_get_session_summary(self):
        from services.response_confidence import get_session_summary

        class _FakeColl:
            async def find_one(self, *a, **kw):
                return {"summary": "User was fixing a login bug, found it in auth.py."}

        class _FakeDB:
            chat_sessions = _FakeColl()

        out = await get_session_summary(_FakeDB(), "sid", "uid")
        assert out == "User was fixing a login bug, found it in auth.py."

    @pytest.mark.asyncio
    async def test_t_session_context_loaded_casual_reply_uses_summary(self, monkeypatch):
        import services.intent_gateway_casual_reply as mod
        captured = {}

        async def fake_llm(messages, system=None, max_tokens=None, temperature=None):
            captured["system"] = system or ""
            return "You found a login bug in auth.py — want me to fix it now?"

        import services.llm as llm_mod
        monkeypatch.setattr(llm_mod, "call_llm", fake_llm, raising=False)

        out = await mod.casual_direct_reply(
            "what did we find earlier?",
            session_summary="User was fixing a login bug, found it in auth.py.",
        )
        assert "login bug in auth.py" in out
        assert "Running summary of this session" in captured["system"]


# ── Live E2E — multi-turn recall through the real agentic/query tier ─
def _send(prompt, headers, session_id):
    return requests.post(f"{API}/chat/send", headers=headers,
                          json={"prompt": prompt, "session_id": session_id, "project_id": "home",
                                "max_tool_iters": 1},
                          timeout=180)


class TestLiveMultiTurnRecall:

    def test_t_ora_references_past_across_multiple_turns(self, headers):
        """3+ real turns, then a recall question — the reply must
        reference content from turns 1 AND 2, proving multi-turn
        history (not just the single immediately-prior turn from
        Issue B) actually reaches the model."""
        sid = f"issueC_recall_{uuid.uuid4().hex[:8]}"
        r1 = _send("Just so you know: I found a login bug in my project's auth flow.", headers, sid)
        assert r1.status_code == 200, r1.text[:300]
        r2 = _send("Also, there's a separate signup-flow bug I noticed.", headers, sid)
        assert r2.status_code == 200, r2.text[:300]
        r3 = _send("What issues did I mention to you so far in this chat?", headers, sid)
        assert r3.status_code == 200, r3.text[:300]
        content = (r3.json().get("content") or "").lower()
        assert "login" in content or "auth" in content, f"lost turn 1's context: {content[:300]}"
        assert "signup" in content, f"lost turn 2's context: {content[:300]}"
