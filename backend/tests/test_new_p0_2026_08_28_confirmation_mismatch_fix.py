"""
tests/test_new_p0_2026_08_28_confirmation_mismatch_fix.py

NEW #1 P0 (2026-08-28, founder live repro) — "Ship-Approve
false-success + no-button."

Root cause (Task 1): `response_seems_mismatched()` only ever looked
at the CURRENT user message in isolation. A real live repro: turn 1
("ship a trivial README edit") legitimately gets a ```aurem-handoff
fence back. Turn 2, the user just confirms — "yes" / "go ahead" /
"approve" / "ship it" — with no `_FIX_INTENT_TOKENS` word of its own.
The model, seeing full conversation history, correctly re-emits the
SAME fix with a fresh fence. But the gate, seeing only "yes" (no fix
intent) + a response with a fix signal, flagged it as an unsolicited
mismatch and swapped the REAL fence for FALLBACK_MESSAGE — the fence
never reached the user, so the Approve button never rendered, and the
P0-1 fallback banner ("approve button didn't load") fired on every
retry because retrying hits the exact same gate again.

Fix: `response_seems_mismatched` now takes `prior_turn_had_fix_signal`
— when True AND the current message is a short confirmation reply,
the mismatch check is skipped (treated as legitimate continuation).
"""
import pytest

from services.response_confidence import (
    response_seems_mismatched,
    is_confirmation_reply,
    response_has_fix_signal,
    prior_turn_had_fix_signal,
)

FENCE_REPLY = (
    "Root cause: the README is missing a license line.\n"
    "```aurem-handoff\nIn `README.md` add a license line at the "
    "bottom.\n```"
)


def test_confirmation_reply_detects_common_phrasings():
    for msg in ["yes", "Yes.", "go ahead", "ship it", "approve",
                "approved", "confirm", "do it", "sounds good", "ok"]:
        assert is_confirmation_reply(msg), f"{msg!r} should be a confirmation"


def test_confirmation_reply_rejects_real_questions():
    # A longer, real question must NEVER be misread as a confirmation,
    # even if it contains a word like "ok" or "confirm" inside it.
    for msg in ["ok but what does this file do?",
                "can you confirm which endpoint owns billing?",
                "what is 5+5?"]:
        assert not is_confirmation_reply(msg)


def test_response_has_fix_signal_detects_fence():
    assert response_has_fix_signal(FENCE_REPLY)
    assert not response_has_fix_signal("Sure, happy to help with anything!")


def test_mismatch_gate_fires_without_context_bug_repro():
    """This is the exact live-repro bug: WITHOUT the prior-turn
    context, a bare "yes" confirming a real fix gets wrongly flagged
    as mismatched — this is what used to eat the fence."""
    assert response_seems_mismatched("yes", FENCE_REPLY, prior_turn_had_fix_signal=False)


def test_mismatch_gate_skipped_with_prior_fix_context_the_actual_fix():
    """The fix: when the prior assistant turn already had a fix
    signal, a short confirmation reply is NOT a mismatch — the real
    fence must reach the user and the Approve button must render."""
    assert not response_seems_mismatched("yes", FENCE_REPLY, prior_turn_had_fix_signal=True)
    assert not response_seems_mismatched("ship it", FENCE_REPLY, prior_turn_had_fix_signal=True)
    assert not response_seems_mismatched("approve", FENCE_REPLY, prior_turn_had_fix_signal=True)


def test_prior_fix_context_does_not_bypass_real_unrelated_mismatch():
    """Guardrail: prior fix context must NOT blanket-exempt everything
    — only genuine short confirmation replies. A real, substantive
    new message must still go through the normal mismatch check even
    if the prior turn had a fix signal (no false "always safe now")."""
    unrelated = "what is 5+5?"
    assert response_seems_mismatched(unrelated, FENCE_REPLY, prior_turn_had_fix_signal=True)


@pytest.mark.asyncio
async def test_prior_turn_had_fix_signal_reads_last_assistant_turn(monkeypatch):
    class FakeCollection:
        async def find_one(self, query, projection):
            return {"turns": [{"role": "assistant", "content": FENCE_REPLY}]}

    class FakeDB:
        chat_sessions = FakeCollection()

    result = await prior_turn_had_fix_signal(FakeDB(), "sess-1", "user-1")
    assert result is True


@pytest.mark.asyncio
async def test_prior_turn_had_fix_signal_fails_open_on_no_session():
    result = await prior_turn_had_fix_signal(None, "sess-1", "user-1")
    assert result is False
