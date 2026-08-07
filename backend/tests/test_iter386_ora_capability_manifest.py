"""Iter 386 · Session 2.5 — ORA capability-manifest audit.

Bug fixed here: ORA's system prompt was Phase 1 vintage and had no
awareness of the Phase 2-5 surface (`/image` generation, live
preview panel, upload/vision). Founder asked "design a logo" and
ORA responded that logo design was outside its capabilities and
suggested Canva / DALL-E — while `/image` (gpt-image-1) had been
built, shipped, and rate-limited to founder-only for a full week.

These tests are contract-level guards, NOT LLM behaviour tests
(behaviour verification requires a live-LLM eval). They assert:

  · The system prompt contains explicit `/image` capability copy.
  · The proactive-offer wording is present (so an LLM cannot skip
    over the surface and default to "outside my capabilities").
  · The raster-vs-vector caveat is present AND worded as an
    honest capability disclosure, not a refusal.
  · The preview-panel capability is documented.
  · The upload/vision capability is documented.
  · A single canonical "refuse image request" failure mode is
    explicitly deprecated (competitor-tool-only recommendation).

A future refactor that drops any of these sections trips this
suite immediately.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/app/backend")

from services.ora_chat.safety import (   # noqa: E402
    AUREM_CONTEXT, assemble_system_prompt,
)


class TestImageCapabilityDocumented:
    def test_image_slash_command_named_verbatim(self):
        # The slash-command literal must appear so the LLM can quote
        # it back to the user, not invent a similar-looking one.
        assert "`/image <prompt>`" in AUREM_CONTEXT

    def test_founder_only_gating_documented(self):
        # The tier constraint MUST be visible so ORA doesn't offer
        # `/image` to a free-tier user without a caveat.
        assert "Founder-only" in AUREM_CONTEXT

    def test_gpt_image_1_backend_named(self):
        # The exact model name so ORA can answer "what generates it?"
        # honestly — no invented "OpenAI DALL-E 3" claims.
        assert "gpt-image-1" in AUREM_CONTEXT

    def test_proactive_offer_language_present(self):
        # This is the CORE fix. The prompt must instruct ORA to offer
        # /image FIRST, before external tools. Without this the LLM
        # reverts to its training-data default of "recommend Canva".
        # Whitespace-tolerant: match on the canonical phrase with
        # newlines and multi-space collapsed to single space.
        import re
        normalised = re.sub(r"\s+", " ", AUREM_CONTEXT)
        assert "proactively offer `/image` as the FIRST option " \
            "BEFORE mentioning any external tool" in normalised

    def test_visual_trigger_words_enumerated(self):
        # Broad list of user-intent words so any visual request lands
        # in the /image path, not just literal "logo".
        for kw in ("LOGO", "MOCKUP", "ILLUSTRATION", "BANNER",
                   "SOCIAL-POST-GRAPHIC", "ICON", "HERO-IMAGE"):
            assert kw in AUREM_CONTEXT, f"trigger keyword {kw!r} missing"

    def test_raster_caveat_is_honest_not_refusal(self):
        # The raster limitation MUST be worded as a capability
        # disclosure paired with a helpful offer — never as a
        # blanket refusal to attempt logo work.
        import re
        normalised = re.sub(r"\s+", " ", AUREM_CONTEXT)
        assert "raster PNG" in AUREM_CONTEXT
        assert "Do NOT refuse the logo request" in normalised
        assert "do NOT hide behind the raster limit" in normalised

    def test_competitor_tools_are_secondary_not_primary(self):
        # Competitor recommendation is not banned outright — sometimes
        # a raster PNG genuinely IS the wrong tool. But it must be
        # secondary, not the first thing ORA reaches for.
        import re
        normalised = re.sub(r"\s+", " ", AUREM_CONTEXT)
        assert ("Never recommend Canva / DALL-E / MidJourney / "
                "Figma AI etc as the primary route") in normalised


class TestPreviewPanelDocumented:
    def test_preview_capability_named(self):
        assert "Live preview panel (Phase 2" in AUREM_CONTEXT
        assert "sandboxed" in AUREM_CONTEXT.lower()

    def test_html_scope_limit_stated(self):
        # Prevent ORA from claiming Python/Node code will "preview" —
        # only browser-runnable HTML/JSX renders.
        assert "HTML/JSX only" in AUREM_CONTEXT


class TestUploadVisionDocumented:
    def test_upload_capability_named(self):
        assert "Upload + vision (Phase 4)" in AUREM_CONTEXT

    def test_mime_whitelist_stated(self):
        # If a founder wonders "can I upload a docx?" the prompt
        # itself carries the answer — no guessing.
        for mime in ("PNG", "JPG", "WEBP", "PDF"):
            assert mime in AUREM_CONTEXT

    def test_size_cap_stated(self):
        assert "10MB" in AUREM_CONTEXT

    def test_free_tier_disclaimer_present(self):
        # ORA must never claim upload is universally unavailable —
        # it's tier-gated with a 402 upgrade path.
        import re
        normalised = re.sub(r"\s+", " ", AUREM_CONTEXT)
        assert "Free-tier users get a 402 upgrade prompt" in normalised


class TestAssembledPromptIncludesTheseSections:
    def test_full_assembled_prompt_contains_all_new_surface(self):
        """The `assemble_system_prompt` pipeline is what the router
        actually ships to the LLM. Confirming the new copy survives
        the layering so a future refactor of the assembly logic
        can't accidentally drop it."""
        prompt = assemble_system_prompt(None, include_runtime=False)
        # If any of these disappear from the assembled result, we
        # ship blind on prod — this is the strongest guard.
        assert "`/image <prompt>`" in prompt
        assert "Live preview panel (Phase 2" in prompt
        assert "Upload + vision (Phase 4)" in prompt

    def test_cannot_list_still_names_the_vector_limit(self):
        """The CANNOT list must still explicitly deny SVG/AI/EPS so
        ORA doesn't over-promise vector output from `/image`."""
        import re
        prompt = assemble_system_prompt(None, include_runtime=False)
        normalised = re.sub(r"\s+", " ", prompt)
        assert "generate vector- format (SVG / AI / EPS) assets" in normalised \
            or "generate vector-format (SVG / AI / EPS) assets" in normalised
