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


def test_verify_pause_event_removed_after_terminal_hard_fail():
    """Contract update (Feb 2026 · terminal hard-fail) — the founder
    reported that the outer-retry path was still causing duplicate
    "Verify failed after 2 attempts" events. The bug_testing_agent
    confirmed the loop was still pausing for user instead of hard-
    failing at the cap. New contract:

      When MAX_SELF_HEALS heal rounds are exhausted with files still
      failing, `_do_verify` HARD-FAILS via `_fail("verify", ...)`
      (LoopState.FAILED). It does NOT emit a PAUSED_FOR_USER event
      and does NOT forward verify_retry_count/max_verify_retries
      into any event (those payload keys stopped being emitted by
      the engine — the router-side outer-retry cap is now dead code
      protected by an explicit terminal-state guard).

    This test locks the removal in so a refactor can't quietly
    reintroduce the pause-for-user fallback.
    """
    src = (_REPO / "backend" / "services" / "loop_engine.py").read_text(
        encoding="utf-8")

    # Context keys are still persisted (for the router's fallback
    # feedback carrier if a legacy client ever needs them).
    assert 'self.context["verify_failed_files"]' in src, (
        "loop_engine must still persist verify_failed_files for "
        "diagnostics + audit trail."
    )
    assert 'self.context["verify_last_errors"]' in src, (
        "loop_engine must still persist verify_last_errors."
    )

    # The engine no longer emits a PAUSED_FOR_USER event for verify
    # exhaustion — it emits a terminal FAILED via _fail().
    verify_block = src[src.find("async def _do_verify"):
                       src.find("async def _do_scan")]
    assert "LoopState.PAUSED_FOR_USER" not in verify_block, (
        "Feb 2026 terminal-fail contract: _do_verify must NOT "
        "transition to PAUSED_FOR_USER when heals are exhausted. "
        "Use `_fail('verify', ...)` for the terminal state instead."
    )
    # And the emit payload no longer carries the retry-count keys
    # (they were only meaningful for the removed pause path).
    assert '"verify_retry_count": int(' not in verify_block, (
        "Feb 2026 terminal-fail contract: _do_verify must not emit "
        "verify_retry_count in any event — the outer-retry pause "
        "path was removed."
    )
    assert '"max_verify_retries": 3' not in verify_block, (
        "Feb 2026 terminal-fail contract: max_verify_retries=3 "
        "leftover from the removed pause path must be gone."
    )
