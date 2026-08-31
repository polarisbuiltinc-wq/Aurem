"""
tests/test_grounding_content_claim_2026_09_03.py

Root 2 (2026-09-03 core-flow round) — grounding wired into the MAIN
business-owner chat surface (`routers/chat.py`), not just the admin
ORA panel. Content-VALUE claims ("line 42 shows X") are checked
against what was ACTUALLY retrieved this turn, not just file-path
existence.

t_fabricated_content_not_shown -> a reply claiming 'line 15 has "Open
9-5"' where "Open 9-5" is NOT in the retrieved context must be
caught and never shown to the user as-is.

This is DIFFERENT from Root 1's `apply_no_orphan_confirm_guard`
(response_confidence.py) -- that one guards a discovery claim PAIRED
WITH a confirm question; this one guards ANY specific line-content
claim against real retrieved evidence, confirm question or not. Both
guards are required and independent.
"""
from __future__ import annotations

from services.ora_chat.grounding_check import (
    extract_line_content_claims,
    contains_fabricated_content_claim,
    apply_fabricated_content_guard,
    FABRICATED_CONTENT_MESSAGE,
)
from services.chat_helpers import retrieved_context_for_grounding


def test_t_fabricated_content_not_shown_line_claim_absent_from_context():
    """The exact founder-repro shape: a specific line+content claim
    that is NOT present anywhere in what was actually retrieved this
    turn must never reach the user as a factual statement."""
    reply = 'Found the opening hours section at line 15, which has "Open 9-5".'
    retrieved_context = "some other unrelated file content, nothing about hours"
    assert contains_fabricated_content_claim(reply, retrieved_context) is True
    out = apply_fabricated_content_guard(reply, retrieved_context)
    assert out == FABRICATED_CONTENT_MESSAGE
    assert "Open 9-5" not in out
    assert "line 15" not in out.lower()


def test_grounded_line_claim_present_in_context_is_untouched():
    """The inverse: when the quoted content genuinely IS a substring
    of what was retrieved this turn, the claim is grounded and must
    pass through untouched."""
    reply = 'Line 15 currently reads "Open 9-5".'
    retrieved_context = 'const Hours = () => <p>Open 9-5</p>;'
    assert contains_fabricated_content_claim(reply, retrieved_context) is False
    assert apply_fabricated_content_guard(reply, retrieved_context) == reply


def test_no_line_content_claim_is_untouched():
    """A reply with no specific line/content claim at all (the common
    case) never triggers this guard."""
    reply = "Sure, tell me what you'd like your opening hours to say."
    assert contains_fabricated_content_claim(reply, "") is False


def test_empty_retrieved_context_means_any_claim_is_fabricated():
    """Zero tool calls this turn (empty retrieved context, e.g. the
    tool-free casual path) means ANY specific line/content claim is
    fabricated by construction -- there was nothing real to ground it
    in."""
    reply = 'Line 42 shows "10am-5pm".'
    assert contains_fabricated_content_claim(reply, "") is True
    assert contains_fabricated_content_claim(reply, None) is True


def test_extract_line_content_claims_both_orderings():
    fwd = 'line 42 shows "10am-5pm"'
    rev = '"10am-5pm" at line 42'
    assert extract_line_content_claims(fwd) == [(42, "10am-5pm")]
    assert extract_line_content_claims(rev) == [(42, "10am-5pm")]


def test_retrieved_context_for_grounding_joins_extra_sys_and_tool_results():
    """Verifies the chat.py wiring helper: joins the system context
    string with every real tool-call result from this turn."""
    result = {
        "tool_invocations": [
            {"tool": "read_repo_file", "result": "line1\nOpen 9-5\nline3"},
            {"tool": "other_tool", "result": None},
        ]
    }
    ctx = retrieved_context_for_grounding("[PROJECT MEMORY]\nsome brain ctx", result)
    assert "Open 9-5" in ctx
    assert "some brain ctx" in ctx


def test_retrieved_context_for_grounding_handles_missing_result():
    assert retrieved_context_for_grounding(None, None) == ""
    assert retrieved_context_for_grounding("", {}) == ""
