"""
tests/test_no_orphan_confirm_guard_2026_09_03.py

Root 1 (2026-09-03 core-flow round) — "Confirm must be coupled to a
real pending action object." Real, live-reproduced founder E2E bug
(non-technical flow, failed 3x): a reply fabricates a specific
discovery ("Found the opening hours section at line 42, current hours
show 10am-5pm") with no real `aurem-handoff` fence backing it, then
asks the user to confirm/apply — the exact setup for the "yes please"
-> "There's nothing pending" dead end.

t_no_orphan_confirm         -> contains_orphan_confirm/apply_* below
t_confirmed_action_approves_something -> a REAL pending fix (real
    fence) must route confirmations to the real pipeline, never the
    deterministic NO_PENDING_FIX_MESSAGE dead end.
"""
from __future__ import annotations

import asyncio

from services.response_confidence import (
    apply_no_orphan_confirm_guard,
    contains_orphan_confirm,
    ORPHAN_CONFIRM_MESSAGE,
    is_confirmation_reply,
    response_has_fix_signal,
)

FABRICATED_HOURS_REPLY = (
    "Found the opening hours section at line 42, current hours show "
    "10am-5pm. Would you like me to update this change?"
)

FABRICATED_HOURS_REPLY_2 = (
    "I checked your homepage and it currently shows Mon-Fri 9-5. "
    "Do you want me to apply this?"
)


# ── t_no_orphan_confirm ────────────────────────────────────────────
def test_t_no_orphan_confirm_fabricated_line_claim():
    """The exact founder-repro shape: line-number claim + confirm
    question + no real fence -> must never reach the user as-is."""
    assert contains_orphan_confirm(FABRICATED_HOURS_REPLY) is True
    out = apply_no_orphan_confirm_guard(FABRICATED_HOURS_REPLY)
    assert out == ORPHAN_CONFIRM_MESSAGE
    assert "line 42" not in out
    assert "10am-5pm" not in out


def test_t_no_orphan_confirm_current_shows_claim():
    """'I checked ... and it currently shows' + confirm question is
    the same bug class without a line-number claim."""
    assert contains_orphan_confirm(FABRICATED_HOURS_REPLY_2) is True
    out = apply_no_orphan_confirm_guard(FABRICATED_HOURS_REPLY_2)
    assert out == ORPHAN_CONFIRM_MESSAGE


def test_real_aurem_handoff_fence_is_never_touched():
    """A genuine pending fix (real fence) must pass through untouched
    even if it also happens to describe a specific finding."""
    real = (
        "Found the opening hours section at line 42.\n\n"
        "```aurem-handoff\nfile: src/Hours.jsx\n```\n\n"
        "Would you like me to update this change?"
    )
    assert contains_orphan_confirm(real) is False
    assert apply_no_orphan_confirm_guard(real) == real


def test_plain_confirm_question_without_discovery_claim_untouched():
    """A plain, honest permission-ask with NO claim of already having
    found/checked specific content is legitimate and must stay
    untouched -- same acceptance bar as
    test_confirm_question_without_codeblock_is_untouched in
    test_no_edit_deadend_guard_2026_09_02.py (must keep passing)."""
    plain = "Should I go ahead and update the homepage copy for you?"
    assert contains_orphan_confirm(plain) is False
    assert apply_no_orphan_confirm_guard(plain) == plain


def test_generic_casual_reply_about_website_is_untouched():
    """A normal, vague casual reply mentioning 'your website' with no
    line/section/current-state claim must not false-positive."""
    casual = "Hi! I can help update your website -- what would you like to change?"
    assert contains_orphan_confirm(casual) is False


# ── t_confirmed_action_approves_something ──────────────────────────
def test_t_confirmed_action_approves_something_real_fence_routes_agentic():
    """When the prior turn carried a REAL fix signal (a genuine
    aurem-handoff fence), a bare confirmation reply must classify as
    agentic (real pipeline) -- NEVER the deterministic
    NO_PENDING_FIX_MESSAGE dead end reserved for turns with nothing
    pending."""
    from core.intent_gateway import classify, TIER_AGENTIC

    real_fence_reply = (
        "Here's the fix.\n\n```aurem-handoff\nfile: src/Hours.jsx\n```"
    )
    assert response_has_fix_signal(real_fence_reply) is True
    assert is_confirmation_reply("yes please") is True

    result = asyncio.run(
        classify("yes please", history=[], pending_fix=True, escalate_to_llm=False)
    )
    assert result["tier"] == TIER_AGENTIC, (
        f"a confirmation reply to a REAL pending fix must route to the "
        f"real pipeline, got tier={result['tier']!r}"
    )


def test_t_confirmed_action_no_fence_correctly_reports_nothing_pending():
    """The inverse: when the prior turn did NOT carry a real fix
    signal (no fence -- e.g. it was an orphan-confirm turn that this
    round's guard already rewrote to an honest message), a bare
    confirmation correctly reports nothing pending rather than
    fabricating a false approval."""
    no_fence_reply = ORPHAN_CONFIRM_MESSAGE
    assert response_has_fix_signal(no_fence_reply) is False
