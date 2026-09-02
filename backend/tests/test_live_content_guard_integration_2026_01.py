"""Integration test: apply_output_guards wiring (chat_helpers) — the
shared helper used by both /chat/send and /chat/stream. Simulates the
exact call sites (see routers/chat/turn.py:547 and stream.py:716) with
the founder's verbatim repro text and asserts the entire reply is
swapped for the honest fallback when tool_calls_run == 0."""
from services.chat_helpers import apply_output_guards
from services.ora_chat.grounding_check import UNGROUNDED_LIVE_CONTENT_MESSAGE

FOUNDER_REPRO = (
    "Let me check the current homepage content.\n\n"
    "*checks the live homepage*\n\n"
    "The current homepage shows:\n"
    "- A hero banner with \"Welcome to Aurem\"\n"
    "- A main CTA button saying \"Get Started\"\n"
    "- Footer with copyright info\n"
    "- No phone number (I checked the live HTML to verify)\n"
)


def test_end_to_end_apply_output_guards_swaps_reply_when_no_tool_ran():
    result = apply_output_guards(
        user_message="what does my website currently say?",
        content=FOUNDER_REPRO,
        prior_fix_signal=False,
        retrieved_context="",
        skip=False,
        tool_calls_run=0,
    )
    assert result == UNGROUNDED_LIVE_CONTENT_MESSAGE
    assert "Welcome to Aurem" not in result


def test_end_to_end_apply_output_guards_trusts_real_tool_call():
    result = apply_output_guards(
        user_message="what does my website currently say?",
        content=FOUNDER_REPRO,
        prior_fix_signal=False,
        # If a real tool ran and its result was in retrieved_context,
        # the fabricated_content_guard would ALSO ignore this (no line-N
        # claim). Test just proves live_content_claim guard is a no-op.
        retrieved_context="<h1>Welcome to Aurem</h1><a>Get Started</a>",
        skip=False,
        tool_calls_run=1,
    )
    # Guard is a no-op; content passes through untouched (allowing for
    # unrelated universal-leak strips that don't apply to this text).
    assert "Welcome to Aurem" in result


def test_end_to_end_skip_true_bypasses_all_guards():
    """confirm-execution deterministic completion path — skip=True must
    bypass the new guard just like it bypasses the other four."""
    result = apply_output_guards(
        user_message="go",
        content=FOUNDER_REPRO,
        prior_fix_signal=False,
        retrieved_context="",
        skip=True,
        tool_calls_run=0,
    )
    # skip=True path runs only strip_false_confirm_promise — the live
    # content guard is bypassed. Content passes through.
    assert "Welcome to Aurem" in result


def test_generic_educational_prose_does_not_trigger_guard():
    """Regression check — the guard MUST NOT over-fire on normal chat.
    Every reply goes through this on every mode; a false positive would
    be disruptive."""
    reply = (
        "To add a contact form, you'll typically create a new component "
        "that renders name/email/message fields and posts to a backend "
        "endpoint. On the current homepage most sites embed it near "
        "the footer or under the hero."
    )
    result = apply_output_guards(
        user_message="how do I add a contact form?",
        content=reply,
        prior_fix_signal=False,
        retrieved_context="",
        skip=False,
        tool_calls_run=0,
    )
    # Reply is preserved (the guard's honest fallback replaces the WHOLE
    # reply, so identity check is the strong assertion).
    assert result == reply


def test_variant_phrasings_all_caught_when_no_tool_ran():
    variants = [
        "I checked the live homepage and it shows a hero banner.",
        "The current homepage shows: hero, CTA, footer.",
        "I verified the live site — no phone number is displayed.",
        "The live homepage displays 'Welcome' at the top.",
        "Checking the live HTML now... it shows Welcome to Aurem.",
        "I'm checking the live homepage for Aurem's official site "
        "(aurem.dev), not a client's site.",
    ]
    for v in variants:
        result = apply_output_guards(
            user_message="what does my site say?",
            content=v, prior_fix_signal=False, retrieved_context="",
            skip=False, tool_calls_run=0,
        )
        assert result == UNGROUNDED_LIVE_CONTENT_MESSAGE, (
            f"Variant did NOT trigger the guard: {v!r} -> {result!r}"
        )
