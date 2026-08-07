"""
tests/test_iter212m265_ora_intent_router.py — Phase 3 · Feb 2026

Unit + contract tests for the two-layer intent router.  Regex layer
is exercised deterministically (no LLM).  The LLM layer is exercised
via a stubbed `one_shot_fn` so we can assert:
  · label-sanitisation collapses loose LLM output to a fixed vocabulary,
  · classifier exceptions collapse to UNKNOWN (never raise into the SSE stream),
  · CODE_CHANGE wins tie-breaks against PREVIEW_ONLY.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services.ora_chat import intent_router as R


# ── Layer 1 · Regex pre-filter ─────────────────────────────────────
class TestRegexPreFilter:
    def test_preview_show_me_pattern(self):
        intent, matches = R.classify_intent_regex(
            "Show me a sample landing page hero"
        )
        assert intent == R.INTENT_PREVIEW
        assert matches  # at least one pattern matched

    def test_preview_what_would_it_look_like(self):
        intent, _ = R.classify_intent_regex(
            "What would that dashboard look like?"
        )
        assert intent == R.INTENT_PREVIEW

    def test_preview_just_a_snippet(self):
        intent, _ = R.classify_intent_regex(
            "Just a jsx snippet please"
        )
        assert intent == R.INTENT_PREVIEW

    def test_code_change_commit_verb(self):
        intent, _ = R.classify_intent_regex(
            "Commit this fix to the repo"
        )
        assert intent == R.INTENT_CODE_CHANGE

    def test_code_change_update_the_file(self):
        intent, _ = R.classify_intent_regex(
            "Update the src/App.jsx button colour"
        )
        assert intent == R.INTENT_CODE_CHANGE

    def test_code_change_file_path_only(self):
        # A bare file-path mention is a strong signal — the founder
        # is pointing at a real file to touch.
        intent, _ = R.classify_intent_regex(
            "Fix frontend/src/components/Login.jsx line 42"
        )
        assert intent == R.INTENT_CODE_CHANGE

    def test_tie_break_code_change_wins(self):
        # Message contains BOTH "show me a snippet" and "commit it".
        # Per Phase 3 policy the imperative CODE_CHANGE must win.
        intent, _ = R.classify_intent_regex(
            "Show me a component snippet, then commit it to the repo"
        )
        assert intent == R.INTENT_CODE_CHANGE

    def test_unknown_on_pure_question(self):
        intent, _ = R.classify_intent_regex(
            "What's the largest prime number below 100?"
        )
        assert intent == R.INTENT_UNKNOWN

    def test_empty_string_is_unknown(self):
        intent, _ = R.classify_intent_regex("")
        assert intent == R.INTENT_UNKNOWN


# ── Layer 2 · LLM sanitiser + fallback ─────────────────────────────
class TestLLMLabelSanitiser:
    def test_exact_preview_accepted(self):
        assert R._sanitize_llm_label("PREVIEW_ONLY") == R.INTENT_PREVIEW

    def test_exact_code_change_accepted(self):
        assert R._sanitize_llm_label("CODE_CHANGE") == R.INTENT_CODE_CHANGE

    def test_trailing_period_stripped(self):
        assert R._sanitize_llm_label("CODE_CHANGE.") == R.INTENT_CODE_CHANGE

    def test_backticks_stripped(self):
        assert R._sanitize_llm_label("`PREVIEW_ONLY`") == R.INTENT_PREVIEW

    def test_lowercase_promoted(self):
        assert R._sanitize_llm_label("preview_only") == R.INTENT_PREVIEW

    def test_partial_match_rejected(self):
        # Fabricated / substring output MUST collapse to UNKNOWN — the
        # classifier can never accidentally promote a made-up label.
        assert R._sanitize_llm_label("PREVIEW") == R.INTENT_UNKNOWN
        assert R._sanitize_llm_label("MAYBE PREVIEW_ONLY YES") == R.INTENT_UNKNOWN

    def test_empty_is_unknown(self):
        assert R._sanitize_llm_label("") == R.INTENT_UNKNOWN
        assert R._sanitize_llm_label(None) == R.INTENT_UNKNOWN


class TestClassifyIntentTwoLayer:
    """Wires regex + a stubbed LLM one-shot."""

    def _stub(self, label: str):
        async def _one_shot(**_kw):
            return label, {"input_tokens": 30, "output_tokens": 2}, None
        return _one_shot

    def _raising_stub(self):
        async def _one_shot(**_kw):
            raise RuntimeError("boom")
        return _one_shot

    def test_regex_short_circuit_avoids_llm(self):
        async def _never(**_kw):
            raise AssertionError("LLM must not be called when regex is confident")
        out = asyncio.run(R.classify_intent(
            "Commit this to the repo", one_shot_fn=_never,
        ))
        assert out["intent"] == R.INTENT_CODE_CHANGE
        assert out["source"] == "regex"

    def test_llm_fallback_when_regex_unknown(self):
        out = asyncio.run(R.classify_intent(
            "Hey there.", one_shot_fn=self._stub("PREVIEW_ONLY"),
        ))
        assert out["intent"] == R.INTENT_PREVIEW
        assert out["source"] == "llm"
        assert out["meta"].get("model")

    def test_llm_exception_collapses_to_unknown(self):
        # Vital contract: an LLM failure MUST NOT bubble out of the
        # classifier — the streaming /message endpoint depends on it
        # never raising.
        out = asyncio.run(R.classify_intent(
            "Hey there.", one_shot_fn=self._raising_stub(),
        ))
        assert out["intent"] == R.INTENT_UNKNOWN
        assert out["source"] == "llm"
        assert "error" in out["meta"]

    def test_no_llm_fn_returns_regex_unknown(self):
        out = asyncio.run(R.classify_intent(
            "Hey there.", one_shot_fn=None,
        ))
        assert out["intent"] == R.INTENT_UNKNOWN
        assert out["source"] == "regex"

    def test_empty_text_short_circuits(self):
        out = asyncio.run(R.classify_intent(
            "", one_shot_fn=self._stub("CODE_CHANGE"),
        ))
        assert out["intent"] == R.INTENT_UNKNOWN
        assert out["source"] == "empty"


# ── Router wiring (static) ─────────────────────────────────────────
class TestRouterWiring:
    _SRC = Path("/app/backend/routers/ora_chat.py").read_text()

    def test_message_stream_emits_intent_event(self):
        # The /message SSE handler must yield an "intent" event so
        # the frontend can badge / branch on it.  Static check keeps
        # this contract enforced without spinning FastAPI.
        assert '"type": "intent"' in self._SRC

    def test_intent_classify_endpoint_present_and_admin_gated(self):
        idx = self._SRC.find('@router.post("/intent-classify")')
        assert idx != -1, "intent-classify endpoint missing"
        body = self._SRC[idx:idx + 1200]
        assert "await require_admin(authorization)" in body

    def test_intent_classify_is_not_allowed_to_raise(self):
        # The /message hook wraps the classifier in try/except with a
        # warning log — MUST be present so a classifier crash never
        # kills the actual answer stream.
        assert "intent classify failed" in self._SRC
