"""
tests/test_intent_router_casual_chat.py
========================================
Session 3 · 3.3 sanitizer coverage test.

Founder's rule: adding CASUAL_CHAT as a new sanctioned label means we
must have automated coverage that (a) the sanitizer whitelists it and
(b) fabricated / typo'd variants still collapse to UNKNOWN. Without
this test, a future refactor could silently drop CASUAL_CHAT from
the whitelist and every casual message would fall back to UNKNOWN
without anyone noticing.
"""
from services.ora_chat.intent_router import (
    ALL_INTENTS,
    INTENT_CASUAL,
    INTENT_CODE_CHANGE,
    INTENT_PREVIEW,
    INTENT_UNKNOWN,
    _sanitize_llm_label,
)


def test_casual_chat_is_in_all_intents():
    """Contract: CASUAL_CHAT must be sanctioned in the intent tuple
    downstream code type-checks against."""
    assert INTENT_CASUAL in ALL_INTENTS
    assert INTENT_CASUAL == "CASUAL_CHAT"


def test_sanitizer_passes_casual_chat_through():
    """The LLM outputting 'CASUAL_CHAT' must survive the sanitizer as
    CASUAL_CHAT — not get coerced to UNKNOWN like it would have
    pre-fix when the whitelist only knew PREVIEW_ONLY / CODE_CHANGE."""
    assert _sanitize_llm_label("CASUAL_CHAT") == INTENT_CASUAL
    # Common LLM wrappings — quotes, backticks, trailing period.
    assert _sanitize_llm_label(" CASUAL_CHAT ") == INTENT_CASUAL
    assert _sanitize_llm_label("`CASUAL_CHAT`") == INTENT_CASUAL
    assert _sanitize_llm_label("'CASUAL_CHAT'.") == INTENT_CASUAL
    assert _sanitize_llm_label("casual_chat") == INTENT_CASUAL  # case-insensitive uppercasing
    assert _sanitize_llm_label("CASUAL_CHAT\n") == INTENT_CASUAL


def test_sanitizer_still_passes_original_two_labels():
    """Regression guard: PREVIEW_ONLY and CODE_CHANGE must continue to
    work exactly as they did pre-fix. Adding CASUAL_CHAT to the
    whitelist must not break the existing two."""
    assert _sanitize_llm_label("PREVIEW_ONLY") == INTENT_PREVIEW
    assert _sanitize_llm_label("CODE_CHANGE") == INTENT_CODE_CHANGE
    assert _sanitize_llm_label(" preview_only ") == INTENT_PREVIEW
    assert _sanitize_llm_label("`CODE_CHANGE`.") == INTENT_CODE_CHANGE


def test_sanitizer_rejects_fabricated_or_typo_variants():
    """Anything that isn't one of the three sanctioned labels must
    collapse to UNKNOWN. This is the safety net against an LLM
    hallucinating a label that downstream code doesn't handle."""
    for bad in [
        "casual",                # missing _chat suffix
        "CASUAL",                # ditto
        "CASUAL_CHATTER",        # extra suffix
        "CHAT_CASUAL",           # reordered
        "SMALL_TALK",            # different label the LLM might invent
        "GREETING",              # ditto
        "CODE_CASUAL",           # made-up mash-up
        "PREVIEW_ONLY_CASUAL",   # concatenation
        "",                      # empty
        "   ",                   # whitespace only
        "PREVIEW_ONLY | CASUAL_CHAT",  # LLM emitted both
    ]:
        assert _sanitize_llm_label(bad) == INTENT_UNKNOWN, (
            f"expected UNKNOWN for {bad!r}, got {_sanitize_llm_label(bad)!r}"
        )
