"""Iter 349 — read-only intent gate + plan LLM hard timeout locks."""
import asyncio

import pytest

from services.loop_intent import detect_read_only_intent


# ── Read-only queries → redirect to chat ─────────────────────────────
@pytest.mark.parametrize("q", [
    "what is the current CI status on main",          # the PROD P0 repro
    "why did the last pipeline run fail?",
    "show me the open PRs",
    "list all failing tests",
    "explain how the loop engine works",
    "is the vanguard scanner enabled?",
    "kya main branch green hai",
    "batao kitne tests fail ho rahe hain",
    "how many users signed up this week?",
])
def test_read_only_detected(q):
    ro, reason = detect_read_only_intent(q)
    assert ro is True, f"{q!r} should be read-only (reason={reason})"


# ── Write-intent / mixed queries → keep Loop Mode ────────────────────
@pytest.mark.parametrize("q,expected_reason_prefix", [
    # Founder's explicit edge case: read-looking opener + action verb.
    ("why is the login failing AND fix it", "action_verb"),
    ("what would it take to add dark mode? implement it", "action_verb"),
    ("check the CI and fix whatever is red", "action_verb"),
    ("fix the flaky test in ci.yml", "action_verb"),
    ("add a logout button to the navbar", "action_verb"),
    ("login bug theek kar do", "action_verb"),
    ("refactor the loop engine state machine", "action_verb"),
    # Explicit loop opt-in always wins.
    ("what is the CI status — run this as a loop", "explicit_loop_opt_in"),
    # No read signal → default to loop (conservative).
    ("the dashboard", "no_read_signal"),
    ("", "empty"),
])
def test_write_intent_keeps_loop(q, expected_reason_prefix):
    ro, reason = detect_read_only_intent(q)
    assert ro is False, f"{q!r} must NOT be read-only"
    assert reason.startswith(expected_reason_prefix), (
        f"{q!r}: expected reason {expected_reason_prefix}*, got {reason}")


def test_long_message_never_read_only():
    q = "what about " + "context " * 120 + "?"
    ro, reason = detect_read_only_intent(q)
    assert ro is False and reason == "too_long_for_read_only"


def test_action_verb_word_boundary():
    # "add" inside "address" must not fire.
    ro, _ = detect_read_only_intent("what is the server address?")
    assert ro is True


# ── Plan LLM hard-timeout lock ───────────────────────────────────────
def test_plan_llm_timeout_constant():
    from services import loop_engine
    assert loop_engine.PLAN_LLM_TIMEOUT_S == 30
    # Must stay under the overall plan phase budget.
    assert loop_engine.PLAN_LLM_TIMEOUT_S < loop_engine.PHASE_TIMEOUTS_S["plan"]


@pytest.mark.asyncio
async def test_generate_plan_times_out_gracefully(monkeypatch):
    """A hung LLM call must raise a clear RuntimeError at 30s (patched
    to 0.1s here), not hang until the 120s phase budget."""
    from services import loop_engine

    async def _hung_llm(*a, **kw):
        await asyncio.sleep(5)

    monkeypatch.setattr(loop_engine, "PLAN_LLM_TIMEOUT_S", 0.1)
    import services.llm as llm_mod
    monkeypatch.setattr(llm_mod, "call_llm_with_meta", _hung_llm)

    with pytest.raises(RuntimeError, match="timed out"):
        await loop_engine._generate_plan("u_test", None, "add dark mode")
