"""Iter 386 · Session 2.7 — CORE-safety layer additions coverage.

The Session 2.5 prompt fix (in AUREM_CONTEXT) was insufficient — the
LLM regressed under long context and still (a) refused logo requests,
(b) faked `/image` execution with "Executing…" / "Stand by…" copy,
(c) invented "63 routers / 190 microservices / zero test files" for
a social post. The fix elevates these rules to CORE_SAFETY_RULES so
they're immutable regardless of downstream context.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from services.ora_chat.safety import CORE_SAFETY_RULES   # noqa: E402


class TestCapabilityDisciplineInCore:
    def test_executing_prefix_banned(self):
        assert "Executing:" in CORE_SAFETY_RULES
        assert "FORBIDDEN" in CORE_SAFETY_RULES

    def test_stand_by_banned(self):
        assert "Stand by" in CORE_SAFETY_RULES

    def test_generating_now_banned(self):
        assert "Generating now" in CORE_SAFETY_RULES

    def test_appear_in_next_turn_banned(self):
        assert "appear in the next turn" in CORE_SAFETY_RULES

    def test_alternative_instruction_provided(self):
        # If the LLM STOPS instead of pretending, it needs a replacement
        # sentence — verify the fallback wording is present (whitespace-
        # tolerant so line wrapping doesn't trip regression).
        import re
        normalised = re.sub(r"\s+", " ", CORE_SAFETY_RULES)
        assert "tap the button above" in normalised


class TestProactiveCapabilityRuleInCore:
    def test_logo_refusal_explicitly_banned(self):
        assert "logo design is outside my capabilities" in CORE_SAFETY_RULES
        assert "CATASTROPHIC failure" in CORE_SAFETY_RULES

    def test_image_as_first_recommendation(self):
        assert "`/image` is the FIRST recommendation" in CORE_SAFETY_RULES

    def test_competitor_tools_secondary(self):
        import re
        normalised = re.sub(r"\s+", " ", CORE_SAFETY_RULES)
        assert "NEVER lead with Canva / DALL-E / MidJourney / Figma AI" \
            in normalised


class TestNoFabricatedMetricsRule:
    def test_specific_examples_named(self):
        # Naming the exact numbers ORA hallucinated last session so a
        # future regression that produces the same shape is caught.
        assert "63 routers" in CORE_SAFETY_RULES
        assert "190 microservices" in CORE_SAFETY_RULES
        assert "zero test files" in CORE_SAFETY_RULES

    def test_marketing_context_covered(self):
        # LLM's escape hatch was "this is creative not citation" —
        # rule must close that.
        assert 'The anti-fabrication rule applies to' in CORE_SAFETY_RULES \
            or 'ALL claims, not just code citations' in CORE_SAFETY_RULES

    def test_acceptable_fallback_named(self):
        # If ORA needs a metric, the rule gives it two acceptable escapes.
        assert "vague language" in CORE_SAFETY_RULES
        assert "ask the founder" in CORE_SAFETY_RULES
