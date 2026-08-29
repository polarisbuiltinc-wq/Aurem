"""
tests/test_new_p0_2026_08_28_intent_gateway_pending_fix.py

NEW #1 P0 (2026-08-28) — deeper root cause than the
response_confidence.py mismatch-gate fix (see
test_new_p0_2026_08_28_confirmation_mismatch_fix.py): the intent
gateway's heuristic classifier (`core/intent_gateway.py`) hard-codes
bare acks ("yes", "go ahead", "do it", ...) into `_CASUAL_ACK`, and
returns TIER_CASUAL at 0.94 confidence — high enough to NEVER
escalate to the LLM fallback (threshold 0.75). So a confirmation
reply to a real pending fix never even reaches the agentic pipeline;
it gets a generic casual reply instead. Live-reproduced end to end
via curl against a real connected project + real (non-mock) LLM —
see /app/e2e-proof/NEW-P0-2026-08-28/v2_turn1.json + v2_turn2.json:
turn 1 gets a real ```aurem-handoff fence, turn 2 ("yes") — BEFORE
this fix — got a generic "what can I help with?" casual reply with
no fence; AFTER this fix, turn 2 correctly re-emits the fence.

Fix: `_classify_heuristic`/`classify()` now accept `pending_fix: bool`
— when True and the message is a bare ack, tier is overridden to
TIER_AGENTIC instead of TIER_CASUAL.
"""
import pytest

from core.intent_gateway import _classify_heuristic, classify, TIER_AGENTIC, TIER_CASUAL


def test_bare_ack_without_pending_fix_is_casual_baseline():
    """Baseline / regression guard: a bare "yes" with NO pending fix
    must stay casual — this override must NOT become always-on.
    ("ship it" is excluded — "ship" is already a resource-noun/action
    signal independent of this fix, correctly agentic even without
    pending_fix.)"""
    for msg in ["yes", "go ahead", "do it", "approve"]:
        result = _classify_heuristic(msg, pending_fix=False)
        assert result["tier"] == TIER_CASUAL, f"{msg!r} should still be casual without pending_fix"


def test_bare_ack_with_pending_fix_becomes_agentic_the_fix():
    """The actual fix: with a pending fix from the prior turn, the
    SAME bare ack must route to the agentic pipeline so the fix can
    actually continue."""
    for msg in ["yes", "Yes.", "go ahead", "do it", "ship it", "approve", "approved", "confirm"]:
        result = _classify_heuristic(msg, pending_fix=True)
        assert result["tier"] == TIER_AGENTIC, f"{msg!r} with pending_fix should be agentic, got {result}"
        assert result["confidence"] >= 0.75
        # "ship it" is already agentic via an earlier resource/verb
        # signal (independent of this fix) — only assert the new
        # override signal fired for the OTHER acks that need it.
        if msg != "ship it":
            assert "pending_fix_ack_override" in result["signals"]


def test_pending_fix_does_not_override_real_greetings():
    """Guardrail: pending_fix must NOT reclassify genuine greetings/
    thanks as agentic — only the ACK vocabulary is overridden."""
    for msg in ["hi", "thanks!", "good morning"]:
        result = _classify_heuristic(msg, pending_fix=True)
        assert result["tier"] == TIER_CASUAL, f"{msg!r} should stay casual even with pending_fix"


@pytest.mark.asyncio
async def test_classify_public_api_threads_pending_fix_through():
    result_no_fix = await classify("yes", history=[], db=None, pending_fix=False)
    result_with_fix = await classify("yes", history=[], db=None, pending_fix=True)
    assert result_no_fix["tier"] == TIER_CASUAL
    assert result_with_fix["tier"] == TIER_AGENTIC
