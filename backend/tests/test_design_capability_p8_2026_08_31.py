"""
tests/test_design_capability_p8_2026_08_31.py

Named tests for P8 DESIGN CAPABILITY — ORA never refuses a design/
brand ask, always proposes concrete directions + a path forward,
never leaks "session"/"access to" jargon. All deterministic, zero
LLM/DB in this file.
"""
from __future__ import annotations

from services.design_ask_detector import is_design_ask
from services.design_refusal_guard import (
    has_refusal, has_session_jargon, has_can_do_now, has_concrete_directions,
    asks_at_most_one_input, has_honest_scope_line, is_compliant_design_reply,
    strip_design_refusal, DEFAULT_DESIGN_REPLY,
)


# ── detector ──────────────────────────────────────────────────────────

def test_t_design_ask_detected():
    for text in [
        "can you redesign our brand identity?",
        "our site looks dated, can you modernize it?",
        "make my site look better",
        "our colors feel dated",
    ]:
        assert is_design_ask(text), f"expected design ask: {text!r}"


def test_t_website_fix_not_design_ask():
    for text in [
        "fix the typo on my about page",
        "please put our opening hours at the top of our main page",
        "add our phone number to the bottom of my main page",
    ]:
        assert not is_design_ask(text), f"expected normal fix: {text!r}"


# ── refusal-kill ──────────────────────────────────────────────────────

def test_t_design_request_not_refused():
    assert is_compliant_design_reply(DEFAULT_DESIGN_REPLY)
    assert not has_refusal(DEFAULT_DESIGN_REPLY)


def test_t_design_refusal_pattern_caught():
    bad = (
        "Not verified — brand identity redesigns require design assets "
        "and strategy docs I don't currently have access to in this "
        "session. I can help implement specific visual changes if you "
        "provide the new brand guidelines."
    )
    assert has_refusal(bad)
    assert has_session_jargon(bad)
    fixed = strip_design_refusal(bad)
    assert not has_refusal(fixed)
    assert not has_session_jargon(fixed)
    assert is_compliant_design_reply(fixed)


def test_t_design_soft_deflection_also_caught():
    # Gate 2 testing_agent finding (2026-08-31): real model output
    # sidesteps the literal "I need your brand book" phrasing with a
    # generic deflection that matches neither has_refusal nor
    # has_session_jargon — the gate must catch THIS too, via the full
    # is_compliant_design_reply() check, not just the two regexes.
    soft_deflections = [
        "I focus on publishing code — hit me with a GitHub issue and I'll help solve it directly.",
        "Absolutely! Share the website's files link and I'll analyze the current design, then propose modern updates with a clean Review request.",
    ]
    for bad in soft_deflections:
        assert not is_compliant_design_reply(bad), f"expected non-compliant: {bad!r}"
        fixed = strip_design_refusal(bad)
        assert is_compliant_design_reply(fixed)


# ── directions ────────────────────────────────────────────────────────

def test_t_design_directions_concrete():
    assert has_concrete_directions(DEFAULT_DESIGN_REPLY)
    assert not has_concrete_directions("Please provide a design system and your brand book.")


def test_t_design_asks_one_input():
    assert asks_at_most_one_input(DEFAULT_DESIGN_REPLY)
    multi_item = "What's your primary color? What's your secondary color? What font? What logo file?"
    assert not asks_at_most_one_input(multi_item)


# ── show / scope ──────────────────────────────────────────────────────

def test_t_design_before_after_offered():
    assert "before" in DEFAULT_DESIGN_REPLY.lower() and "after" in DEFAULT_DESIGN_REPLY.lower()


def test_t_design_scope_honest():
    assert has_honest_scope_line(DEFAULT_DESIGN_REPLY)
    low = DEFAULT_DESIGN_REPLY.lower()
    assert "impossible" not in low
    assert "can't" not in low and "cannot" not in low


# ── jargon ────────────────────────────────────────────────────────────

def test_t_session_jargon_never_reaches_owner():
    leaky_samples = [
        "I don't have access to this session's design assets.",
        "In this session I can't pull your brand docs.",
        "I don't have access to your design guidelines right now.",
    ]
    for s in leaky_samples:
        assert has_session_jargon(s), f"expected jargon caught: {s!r}"
    assert not has_session_jargon(DEFAULT_DESIGN_REPLY)


def test_t_no_i_dont_have_access_framing():
    assert not has_session_jargon(DEFAULT_DESIGN_REPLY)
    assert has_can_do_now(DEFAULT_DESIGN_REPLY)


# ── pipeline (single test proving the whole guarantee) ───────────────

def test_t_design_pipeline_end_to_end():
    prompt = "can you redesign our brand identity?"
    model_draft = (
        "I can't design a full brand identity — you'd provide design "
        "guidelines and strategy docs I don't have access to in this "
        "session."
    )
    assert is_design_ask(prompt)
    assert has_refusal(model_draft) or has_session_jargon(model_draft)
    fixed = strip_design_refusal(model_draft)
    assert is_compliant_design_reply(fixed)
    assert "brand book" not in fixed.lower()
