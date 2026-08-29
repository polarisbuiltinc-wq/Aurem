"""
tests/test_new_p0_2026_08_28_false_success_guard.py

NEW #1 P0 (2026-08-28, founder live repro) — Task 2, the #1 fix:
"typing 'approve' yielded 'Approved! Let me know what you need,' but
GitHub remained at the pre-turn SHA — no commit landed."

Root cause: the intent-gateway CASUAL branch (`casual_direct_reply`,
services/intent_gateway_casual_reply.py) is a free-form, no-tool LLM
call with zero guard against claiming a ship/approve action already
happened. A bare "approve" with NOTHING pending still reaches this
call, and the model (mock or real) can improvise a warm, confident-
sounding "Approved!" with no real execution behind it — real
execution is a separate, explicit, button-triggered async flow
(POST /cto/tasks/submit, polled to completion — see
MessageBubble.jsx `shipViaCTO`/`TaskProgressCard`), so no chat TEXT
reply can ever legitimately claim it happened synchronously.

Fix: `services/response_confidence.py` gained
`contains_false_success_claim()` + `apply_no_false_success_guard()`,
wired into BOTH chat_send and chat_stream at two chokepoints:
  1. the casual/clarify branch — a bare confirmation with no prior
     fix signal skips the LLM call entirely (deterministic, honest,
     zero spend).
  2. a final defense-in-depth pass on whatever content is about to
     reach the user (agentic path included) — catches a model
     hallucination on either path.
"""
import pytest

from services.response_confidence import (
    contains_false_success_claim,
    apply_no_false_success_guard,
    is_confirmation_reply,
    NO_PENDING_FIX_MESSAGE,
    RETRY_FIX_MESSAGE,
)

FAKE_APPROVED_REPLY = "Approved! Let me know what you need."
FAKE_SHIPPED_REPLY = "Shipped it — all set, let me know if you need anything else."
HONEST_NO_FENCE_REPLY = "Sure, happy to help — what would you like me to look at?"
REAL_FENCE_REPLY = (
    "Root cause: the README is missing a license line.\n"
    "```aurem-handoff\nIn `README.md` add a license line at the "
    "bottom.\n```"
)


def test_false_success_claim_detects_founder_exact_repro():
    assert contains_false_success_claim(FAKE_APPROVED_REPLY)
    assert contains_false_success_claim(FAKE_SHIPPED_REPLY)


def test_false_success_claim_does_not_flag_honest_replies():
    assert not contains_false_success_claim(HONEST_NO_FENCE_REPLY)
    assert not contains_false_success_claim("")
    assert not contains_false_success_claim(None)


def test_false_success_claim_detects_present_tense_promise_from_testing_agent_finding():
    """testing_agent (iteration_p0_ship_approve_fix_verify_2026_01_29)
    found: fresh-session 'yes please ship it' got 'On it—shipping
    now! 🚀' back from the same unguarded casual path — a present-
    tense in-progress promise, not caught by the past-tense-only
    regex. No commit lands, but the wording still misleads."""
    assert contains_false_success_claim("On it—shipping now! 🚀")
    guarded = apply_no_false_success_guard(
        "yes please ship it", "On it—shipping now! 🚀", prior_turn_had_fix_signal=False,
    )
    assert guarded == NO_PENDING_FIX_MESSAGE


def test_false_success_claim_ignores_a_real_fence_reply():
    # A reply carrying a real fence never says "approved"/"shipped"
    # itself — sanity check the token regex doesn't over-fire on
    # legitimate diagnosis prose.
    assert not contains_false_success_claim(REAL_FENCE_REPLY)


# ─── t_text_approve_without_commit_never_claims_success ──────────────

def test_t_text_approve_without_commit_never_claims_success_no_pending_fix():
    """The #1 fix. User types a bare 'approve'/'yes'/'ship it' with NO
    valid handoff / NO prior fix signal in this session -> the reply
    the user actually sees must NEVER contain a success claim."""
    for msg in ["approve", "yes", "ship it", "confirm", "go ahead"]:
        guarded = apply_no_false_success_guard(msg, FAKE_APPROVED_REPLY, prior_turn_had_fix_signal=False)
        assert not contains_false_success_claim(guarded), (
            f"{msg!r} must never surface a false success claim, got: {guarded!r}"
        )
        assert guarded == NO_PENDING_FIX_MESSAGE
        assert "approved" not in guarded.lower()
        assert "shipped" not in guarded.lower()


def test_t_text_approve_without_commit_never_claims_success_stale_pending_fix():
    """Even when a fix WAS proposed earlier (prior_turn_had_fix_signal
    =True) but the model's re-confirmation reply STILL comes back with
    no real fence (e.g. it hallucinated a bare 'approved' instead of
    re-emitting the handoff) — the guard must still strip the false
    claim, this time pointing the user at a retry action rather than
    a flat 'nothing pending' (there WAS something pending, it just
    didn't come back clean)."""
    guarded = apply_no_false_success_guard("approve", FAKE_APPROVED_REPLY, prior_turn_had_fix_signal=True)
    assert not contains_false_success_claim(guarded)
    assert guarded == RETRY_FIX_MESSAGE


def test_t_text_approve_without_commit_broad_scan_guard_any_confirmation_phrasing():
    """Broadened per founder instruction: no success-string on ANY
    bare-confirmation phrasing that lacks a real fence, regardless of
    which exact word triggered it."""
    for msg in ["yes", "yeah", "sure", "ok", "do it", "proceed", "confirmed"]:
        assert is_confirmation_reply(msg)
        guarded = apply_no_false_success_guard(msg, FAKE_SHIPPED_REPLY, prior_turn_had_fix_signal=False)
        assert not contains_false_success_claim(guarded)


# ─── t_text_approve_with_handoff_executes_and_verifies ────────────────

def test_t_text_approve_with_handoff_executes_and_verifies_real_fence_passes_through():
    """Verify-before-success, applied to plain ships (the R10
    standard): when the reply to a confirmation DOES carry a real,
    freshly re-emitted ```aurem-handoff fence, the guard leaves it
    completely untouched — that fence is the ONE real mechanism that
    renders the Approve button (MessageBubble.jsx extractHandoffBrief
    / ShipDialog), and actual execution only starts once the user
    clicks it and POST /cto/tasks/submit is polled to a verified
    terminal state. The text reply itself never claims success —
    it only offers the real, clickable path."""
    guarded = apply_no_false_success_guard("approve", REAL_FENCE_REPLY, prior_turn_had_fix_signal=True)
    assert guarded == REAL_FENCE_REPLY
    assert "```aurem-handoff" in guarded
    # And critically: the untouched reply itself still does not
    # contain a past-tense completion claim — the fence proposes,
    # it doesn't claim the ship already happened.
    assert not contains_false_success_claim(guarded.split("```aurem-handoff")[0])


def test_t_text_approve_with_handoff_never_shortcuts_when_fence_present():
    """Guardrail: even if a real fence reply also contains a stray
    "approved"-shaped word somewhere in its OWN prose, the guard must
    not strip a real fence — has_ship_suggestion() short-circuits
    before the false-success scan runs, matching MessageBubble.jsx's
    own precondition (canShip is gated on handoffBrief truthiness,
    not on the surrounding prose wording)."""
    reply_with_stray_word = (
        "Approved plan below.\n```aurem-handoff\nFix the typo in "
        "README.md.\n```"
    )
    guarded = apply_no_false_success_guard("approve", reply_with_stray_word, prior_turn_had_fix_signal=True)
    assert guarded == reply_with_stray_word


def test_guard_is_a_noop_for_non_confirmation_messages():
    """Guardrail: the guard only ever touches bare-confirmation
    replies — a real, substantive message must pass through
    untouched even if its reply happens to contain "approved"."""
    real_question = "what does the payments module do?"
    assert not is_confirmation_reply(real_question)
    guarded = apply_no_false_success_guard(real_question, FAKE_APPROVED_REPLY, prior_turn_had_fix_signal=False)
    assert guarded == FAKE_APPROVED_REPLY
