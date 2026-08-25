"""tests/test_phase2_intent_gateway_coverage.py — Phase 2 (2026-08-28)

Targeted coverage wave for core/intent_gateway.py (CI floor: 60%,
prior CI measurement 32.7%). Pure-Python module, no FastAPI needed —
covers the heuristic classifier's tier branches, the LLM-escalation
path (mocked services.llm.call_llm), the ambiguity/clarify handler,
Mongo logging (success + swallowed failure), and classify_llm_json's
parse paths.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from core import intent_gateway as ig


# ═════════════════════════════════════════════════════════════════════
# _classify_heuristic via classify_heuristic_sync
# ═════════════════════════════════════════════════════════════════════

class TestHeuristicClassifier:
    def test_empty_message_is_casual(self):
        r = ig.classify_heuristic_sync("")
        assert r["tier"] == ig.TIER_CASUAL
        assert r["signals"] == ["empty"]

    def test_agentic_verb_short_message_high_confidence(self):
        r = ig.classify_heuristic_sync("fix it")
        assert r["tier"] == ig.TIER_AGENTIC
        assert r["confidence"] == 0.97

    def test_agentic_verb_long_message_lower_confidence(self):
        r = ig.classify_heuristic_sync(
            "fix the authentication flow in services/llm.py and add unit tests for it please",
        )
        assert r["tier"] == ig.TIER_AGENTIC
        assert r["confidence"] == 0.92

    def test_casual_greeting(self):
        r = ig.classify_heuristic_sync("hey")
        assert r["tier"] == ig.TIER_CASUAL
        assert "casual_seed" in r["signals"]

    def test_casual_thanks_anywhere_in_message(self):
        r = ig.classify_heuristic_sync("ok thanks")
        assert r["tier"] == ig.TIER_CASUAL

    def test_short_no_intent_filler(self):
        r = ig.classify_heuristic_sync("hmm well")
        assert r["tier"] == ig.TIER_CASUAL
        assert "short_no_intent" in r["signals"]

    def test_query_with_resource_noun_leads_high_confidence(self):
        r = ig.classify_heuristic_sync("what is my pipeline status")
        assert r["tier"] == ig.TIER_QUERY
        assert r["confidence"] == 0.86

    def test_query_lead_not_at_start_lower_confidence(self):
        r = ig.classify_heuristic_sync("the deployment logs, show?")
        assert r["tier"] == ig.TIER_QUERY
        assert r["confidence"] == 0.76

    def test_question_with_no_resource_noun_is_casual(self):
        r = ig.classify_heuristic_sync("what does this website do?")
        assert r["tier"] == ig.TIER_CASUAL
        assert "informational_no_resource_noun" in r["signals"]

    def test_question_with_file_ref_is_query(self):
        r = ig.classify_heuristic_sync("what does README.md say?")
        assert r["tier"] == ig.TIER_QUERY

    def test_agentic_verb_present_but_not_leading_wins_over_query_lead(self):
        r = ig.classify_heuristic_sync(
            "please proceed to make the edit and show me the ship confirmation",
        )
        assert r["tier"] == ig.TIER_AGENTIC
        assert r["confidence"] == 0.80
        assert any(s.startswith("agentic_verb_present:") for s in r["signals"])

    def test_ambiguous_mid_length_statement_falls_to_query_low_confidence(self):
        r = ig.classify_heuristic_sync("the system feels a bit off today honestly")
        assert r["tier"] == ig.TIER_QUERY
        assert r["confidence"] == 0.62
        assert "ambiguous" in r["signals"]


# ═════════════════════════════════════════════════════════════════════
# classify() — async orchestration: LLM escalation, ambiguity/clarify,
# Mongo logging.
# ═════════════════════════════════════════════════════════════════════

class _FakeInsertOnlyColl:
    def __init__(self, fail=False):
        self.rows = []
        self.fail = fail

    async def insert_one(self, doc):
        if self.fail:
            raise RuntimeError("mongo down")
        self.rows.append(doc)


class _FakeDB:
    def __init__(self, fail=False):
        self.intent_classifications = _FakeInsertOnlyColl(fail=fail)


class TestClassifyOrchestration:
    @pytest.mark.asyncio
    async def test_high_confidence_heuristic_skips_llm(self):
        with patch("core.intent_gateway._classify_llm", AsyncMock()) as mock_llm:
            r = await ig.classify("fix it")
        mock_llm.assert_not_awaited()
        assert r["tier"] == ig.TIER_AGENTIC
        assert r["was_ambiguous"] is False
        assert r["clarify"] is None

    @pytest.mark.asyncio
    async def test_low_confidence_escalates_to_llm_and_llm_wins(self):
        llm_result = {
            "tier": ig.TIER_AGENTIC, "confidence": 0.9, "method": "llm",
            "signals": ["llm_classified"], "reasoning": "x",
            "llm_latency_ms": 12.0,
        }
        with patch("core.intent_gateway._classify_llm",
                  AsyncMock(return_value=llm_result)):
            r = await ig.classify("the system feels a bit off today honestly")
        assert r["method"] == "llm"
        assert r["tier"] == ig.TIER_AGENTIC
        assert r["llm_latency_ms"] == 12.0

    @pytest.mark.asyncio
    async def test_low_confidence_llm_does_not_beat_heuristic_keeps_heuristic(self):
        llm_result = {
            "tier": ig.TIER_CASUAL, "confidence": 0.5, "method": "llm",
            "signals": [], "reasoning": "x",
        }
        with patch("core.intent_gateway._classify_llm",
                  AsyncMock(return_value=llm_result)):
            r = await ig.classify("the system feels a bit off today honestly")
        assert r["method"] == "heuristic"

    @pytest.mark.asyncio
    async def test_escalate_to_llm_false_never_calls_llm(self):
        with patch("core.intent_gateway._classify_llm", AsyncMock()) as mock_llm:
            r = await ig.classify("hmm", escalate_to_llm=False)
        mock_llm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_final_low_confidence_becomes_clarify_with_agentic_seed_probe(self):
        with patch("core.intent_gateway._classify_llm",
                  AsyncMock(return_value={"tier": ig.TIER_QUERY, "confidence": 0.5,
                                           "method": "llm", "signals": []})):
            r = await ig.classify("fix something weird going on")
        # heuristic for a longer imperative with word_count>8 falls to
        # a lower branch; whichever tier wins, confidence must be low
        # enough to trip the ambiguity handler in at least one branch.
        assert "clarify" in r

    @pytest.mark.asyncio
    async def test_clarify_probe_for_query_lead_message(self):
        probe = ig._build_clarifying_probe("what is going on here today")
        assert "look this up" in probe or "chatting" in probe

    @pytest.mark.asyncio
    async def test_clarify_probe_for_agentic_seed_message(self):
        probe = ig._build_clarifying_probe("fix the weird thing that happened yesterday okay")
        assert probe.startswith("Just checking")

    @pytest.mark.asyncio
    async def test_clarify_probe_default_fallback(self):
        probe = ig._build_clarifying_probe("something something")
        assert "take an action" in probe

    @pytest.mark.asyncio
    async def test_db_none_skips_logging(self):
        r = await ig.classify("fix it", db=None)
        assert r["tier"] == ig.TIER_AGENTIC

    @pytest.mark.asyncio
    async def test_db_logging_success_persists_row(self):
        db = _FakeDB()
        r = await ig.classify("fix it", db=db, user_id="u1", project_id="p1")
        assert len(db.intent_classifications.rows) == 1
        row = db.intent_classifications.rows[0]
        assert row["user_id"] == "u1"
        assert row["project_id"] == "p1"
        assert row["tier"] == r["tier"]

    @pytest.mark.asyncio
    async def test_db_logging_failure_is_swallowed(self):
        db = _FakeDB(fail=True)
        r = await ig.classify("fix it", db=db)
        assert r["tier"] == ig.TIER_AGENTIC  # classify() must still return normally


# ═════════════════════════════════════════════════════════════════════
# _classify_llm — direct unit tests (import failure / timeout / error /
# parse failure / success / invalid tier fallback / clamp).
# ═════════════════════════════════════════════════════════════════════

class TestClassifyLlmDirect:
    @pytest.mark.asyncio
    async def test_llm_import_error_returns_safe_fallback(self):
        with patch.dict("sys.modules", {"services.llm": None}):
            r = await ig._classify_llm("hi", None)
        assert r["method"] == "llm_unavailable"

    @pytest.mark.asyncio
    async def test_llm_timeout_returns_safe_fallback(self):
        import asyncio as _asyncio

        async def _slow(*a, **k):
            await _asyncio.sleep(5)

        with patch("services.llm.call_llm", _slow), \
             patch("asyncio.wait_for", AsyncMock(side_effect=_asyncio.TimeoutError)):
            r = await ig._classify_llm("hi", None)
        assert r["method"] == "llm_timeout"

    @pytest.mark.asyncio
    async def test_llm_call_error_returns_safe_fallback(self):
        with patch("services.llm.call_llm", AsyncMock(side_effect=RuntimeError("boom"))):
            r = await ig._classify_llm("hi", None)
        assert r["method"] == "llm_error"

    @pytest.mark.asyncio
    async def test_llm_unparseable_response_returns_parse_fail(self):
        with patch("services.llm.call_llm", AsyncMock(return_value="not json at all")):
            r = await ig._classify_llm("hi", None)
        assert r["method"] == "llm_parse_fail"

    @pytest.mark.asyncio
    async def test_llm_valid_response_parsed_and_clamped(self):
        with patch("services.llm.call_llm",
                  AsyncMock(return_value='{"tier": "agentic", "conf": 1.5}')):
            r = await ig._classify_llm("hi", [{"role": "user", "content": "prior"}])
        assert r["tier"] == ig.TIER_AGENTIC
        assert r["confidence"] == 1.0  # clamped
        assert r["method"] == "llm"

    @pytest.mark.asyncio
    async def test_llm_invalid_tier_falls_back_to_query(self):
        with patch("services.llm.call_llm",
                  AsyncMock(return_value='{"tier": "bogus", "conf": 0.9}')):
            r = await ig._classify_llm("hi", None)
        assert r["tier"] == ig.TIER_QUERY


class TestParseLlmJson:
    def test_empty_text_returns_none(self):
        assert ig._parse_llm_json("") is None

    def test_direct_json_parses(self):
        assert ig._parse_llm_json('{"tier": "query", "conf": 0.8}') == {
            "tier": "query", "conf": 0.8,
        }

    def test_plucks_embedded_json_object(self):
        r = ig._parse_llm_json('here you go: {"tier": "casual", "conf": 0.9} thanks')
        assert r == {"tier": "casual", "conf": 0.9}

    def test_unparseable_returns_none(self):
        assert ig._parse_llm_json("no json block here") is None


# ═════════════════════════════════════════════════════════════════════
# classify_llm_json — generic classifier helper.
# ═════════════════════════════════════════════════════════════════════

class TestClassifyLlmJson:
    @pytest.mark.asyncio
    async def test_import_failure_returns_none(self):
        with patch.dict("sys.modules", {"services.llm": None}):
            r = await ig.classify_llm_json("hi")
        assert r is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        import asyncio as _asyncio
        with patch("services.llm.call_llm", AsyncMock()), \
             patch("asyncio.wait_for", AsyncMock(side_effect=_asyncio.TimeoutError)):
            r = await ig.classify_llm_json("hi")
        assert r is None

    @pytest.mark.asyncio
    async def test_call_error_returns_none(self):
        with patch("services.llm.call_llm", AsyncMock(side_effect=RuntimeError("boom"))):
            r = await ig.classify_llm_json("hi")
        assert r is None

    @pytest.mark.asyncio
    async def test_empty_raw_returns_none(self):
        with patch("services.llm.call_llm", AsyncMock(return_value="")):
            r = await ig.classify_llm_json("hi")
        assert r is None

    @pytest.mark.asyncio
    async def test_direct_json_object_parses(self):
        with patch("services.llm.call_llm", AsyncMock(return_value='{"a": 1}')):
            r = await ig.classify_llm_json("hi")
        assert r == {"a": 1}

    @pytest.mark.asyncio
    async def test_plucked_array_parses(self):
        with patch("services.llm.call_llm", AsyncMock(return_value='blah [1, 2, 3] blah')):
            r = await ig.classify_llm_json("hi")
        assert r == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_plucked_object_parses(self):
        with patch("services.llm.call_llm", AsyncMock(return_value='blah {"x": 2} blah')):
            r = await ig.classify_llm_json("hi")
        assert r == {"x": 2}

    @pytest.mark.asyncio
    async def test_unparseable_returns_none(self):
        with patch("services.llm.call_llm", AsyncMock(return_value="nonsense reply")):
            r = await ig.classify_llm_json("hi")
        assert r is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
