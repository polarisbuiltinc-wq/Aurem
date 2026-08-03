"""
test_iter_feb2026_verifier_retry_cap.py — Feb 2026

Locks in the two-part "retry actually varies + hard cap" fix for the
independent-verifier rejection path (founder-reported issue).

Part 1 — verifier retry counter + hard cap
    The pause-response `retry` action, when triggered on a loop whose
    context recorded `independent_verifier.verdict == "no"`, must:
      1. Increment `context.verifier_retry_count` on each retry.
      2. Refuse the 4th retry (i.e. after 3 real retries) with HTTP 429
         and a `verifier_retry_cap_exceeded` error payload — never
         silently loop forever.
      3. On accepted retries, thread the verifier's rejection reason
         into the executor's feedback string so the next EXECUTE pass
         can produce a genuinely different diff.

Part 2 — per-file heal history accumulates across rounds
    The `parliament.SelfHeal.heal` call in `_do_verify` must receive a
    growing `all_attempts` list across `heal_attempt` iterations so the
    healer's "Do NOT repeat the same fix" prompt block actually fires.
    Prior bug: the list was rebuilt fresh with a single entry every
    iteration, so `len(all_attempts) > 1` was never true.

Both tests are static (source-level assertions) — no LLM calls, no
Mongo I/O — because the bug lives entirely in the retry-tracking
control-flow shape, not in the runtime output.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def test_verifier_retry_cap_present_in_pause_response_handler():
    """Part 1 lock-in — the pause-response handler must guard the
    `retry` action against unbounded verifier-rejection loops."""
    src = (_REPO / "backend" / "routers" / "loop.py").read_text(encoding="utf-8")
    assert "verifier_retry_count" in src, (
        "loop.py must track verifier retries in engine.context "
        "so the 4th retry can be refused cleanly."
    )
    assert "MAX_VERIFIER_RETRIES = 3" in src, (
        "Founder-directed hard cap of 3 verifier retries is missing."
    )
    assert "verifier_retry_cap_exceeded" in src, (
        "The 429 error payload must carry a machine-parseable error "
        "code so the frontend can surface 'refusing 4th retry' cleanly."
    )
    # The retry MUST feed the verifier's rejection reason back into
    # the executor — that's what makes the next attempt genuinely
    # different rather than a repeat.
    assert "prev_reject_reason" in src, (
        "The next EXECUTE pass must receive the previous verifier "
        "reason as feedback so it can vary the approach."
    )


def test_verify_selfheal_accumulates_attempt_history_across_rounds():
    """Part 2 lock-in — the healer must see a GROWING history so its
    'Do NOT repeat the same fix' guard actually engages."""
    src = (_REPO / "backend" / "services" / "loop_engine.py").read_text(encoding="utf-8")
    # A single dict named `per_file_attempt_history` lives OUTSIDE the
    # `heal_attempt` loop — this is the accumulator.
    assert "per_file_attempt_history" in src, (
        "loop_engine.py must accumulate per-file attempt history "
        "across heal rounds so parliament.SelfHeal.heal sees prior "
        "failed attempts and can diverge from them."
    )
    # The call site must pass the accumulated list, not a fresh
    # single-entry array. Regression guard against the pre-fix shape:
    #     all_attempts=[{"output": f["content"], ...}]   ← BAD (fresh)
    #     all_attempts=list(file_hist)                   ← GOOD (accum)
    assert "all_attempts=list(file_hist)" in src, (
        "The healer call must receive the accumulated file_hist, "
        "not a freshly-constructed single-entry list."
    )
