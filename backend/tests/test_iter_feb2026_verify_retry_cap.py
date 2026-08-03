"""
test_iter_feb2026_verify_retry_cap.py — Feb 2026

Founder-reported production bug: "loop stuck in infinite verify-fail
cycle — UI shows 1/3 retries but actually on 3rd attempt, keeps trying
the same plan again and again".

Root cause: pause_response(retry) for VERIFY-phase failures had NO cap
— clicking retry just resumed the pipeline, which re-executed the
SAME plan → verify fails again → paused → user retries → forever.
Only the independent-verifier reject path had a retry cap.

Fix:
  1. Cap outer verify retries at 3. HTTP 429 verify_retry_cap_exceeded
     on the 4th attempt.
  2. Inject prior failing_files + last_errors into the executor
     feedback so each retry sees materially different context and
     can produce a different diff.
  3. Emit `verify_retry_count` + `max_verify_retries` in the
     paused_for_user event so the frontend chip renders the OUTER
     count (0..3) instead of the stale INNER self-heal counter (0..2).
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def test_verify_retry_cap_present_in_pause_response_handler():
    """Lock-in — router must guard the retry action against unbounded
    verify-phase loops with a MAX_VERIFY_RETRIES=3 cap."""
    src = (_REPO / "backend" / "routers" / "loop.py").read_text(encoding="utf-8")
    assert 'engine.phase == "verify"' in src, (
        "loop.py pause_response must branch on phase='verify' so the "
        "verify-retry cap kicks in for lint/type-check failures."
    )
    assert "verify_retry_count" in src, (
        "Router must track outer verify retries in engine.context "
        "so the 4th attempt can be refused cleanly."
    )
    assert "MAX_VERIFY_RETRIES = 3" in src, (
        "Founder-directed hard cap of 3 verify retries is missing."
    )
    assert "verify_retry_cap_exceeded" in src, (
        "The 429 error payload must carry a machine-parseable error "
        "code so the frontend can surface 'refusing 4th retry' cleanly."
    )
    # Feedback must include prior failing files + errors so the
    # executor gets materially different context each round.
    assert "failed_files=" in src and "prev_errors=" in src, (
        "Retry feedback must carry the prior verify failing files + "
        "errors so the executor can vary the approach."
    )


def test_verify_pause_event_carries_retry_counters():
    """Lock-in — the paused_for_user event body emitted by _do_verify
    must include verify_retry_count + max_verify_retries so the
    frontend can render the outer retry count accurately."""
    src = (_REPO / "backend" / "services" / "loop_engine.py").read_text(encoding="utf-8")
    # Both context storage AND event payload must be present.
    assert 'self.context["verify_failed_files"]' in src, (
        "loop_engine must persist verify_failed_files so the retry "
        "handler in loop.py can inject them into the feedback."
    )
    assert 'self.context["verify_last_errors"]' in src, (
        "loop_engine must persist verify_last_errors for the same "
        "reason as verify_failed_files."
    )
    assert "verify_retry_count" in src, (
        "loop_engine must forward the context's verify_retry_count "
        "into the paused_for_user event payload so the frontend "
        "renders the OUTER retry counter (0..3) instead of the "
        "inner self-heal counter (0..2)."
    )
    assert "max_verify_retries" in src, (
        "loop_engine must emit max_verify_retries in the pause payload."
    )
