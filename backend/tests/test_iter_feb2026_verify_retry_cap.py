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

import pytest

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


@pytest.mark.source_of_truth
def test_verify_pause_event_reinstated_with_global_cap_dedup():
    """Contract update (W3 · 2026-08) — SUPERSEDES the "Feb 2026 ·
    terminal hard-fail" contract this test previously locked in.

    That contract was an undetected contradiction with the earlier,
    still-live `tests/test_iter309_phase03_self_heal_paused.py`
    guard (PAUSED_FOR_USER on first exhaustion, so plan+execute
    context isn't thrown away). Live-repro (2026-08) proved the
    ACTUAL founder-reported bug this file's sibling
    (`test_iter_feb2026_verify_terminal_fail.py`) was fixing — "4+
    duplicate Verify failed events across retries" — is independently
    fixed by the pre-existing loop-wide `total_heal_attempts` cap
    (`services/loop_engine.py` ~line 1927): a retry-reentry with the
    cap already consumed hard-fails immediately with a DISTINCT
    message, before any new heal attempt runs, so no duplicate
    "Verify failed after N attempts" text can ever repeat. That
    means PAUSED_FOR_USER is safe to keep on first exhaustion — no
    need to remove it. New contract:

      First-time MAX_SELF_HEALS exhaustion within a loop's lifetime
      → `_do_verify` sets PAUSED_FOR_USER (not FAILED), and DOES
      forward `verify_retry_count`/`max_verify_retries` in the event
      (the router-side outer-retry cap in `routers/loop.py` is live,
      reachable code again, not dead).

    This test locks the reinstatement in so a future refactor can't
    quietly re-remove the pause-for-user path without re-checking
    the Iter309 guard + this file's sibling live-repro evidence.
    """
    src = (_REPO / "backend" / "services" / "loop_engine.py").read_text(
        encoding="utf-8")

    # Context keys are still persisted (for the router's fallback
    # feedback carrier + diagnostics/audit trail).
    assert 'self.context["verify_failed_files"]' in src, (
        "loop_engine must still persist verify_failed_files for "
        "diagnostics + audit trail."
    )
    assert 'self.context["verify_last_errors"]' in src, (
        "loop_engine must still persist verify_last_errors."
    )

    # The engine emits PAUSED_FOR_USER for the FIRST verify-exhaustion
    # in a loop's lifetime — reinstated per Iter309.
    verify_block = src[src.find("async def _do_verify"):
                       src.find("async def _do_scan")]
    assert "LoopState.PAUSED_FOR_USER" in verify_block, (
        "W3 · 2026-08 contract: _do_verify must transition to "
        "PAUSED_FOR_USER on first heal-exhaustion (Iter309 founder "
        "rationale: preserve plan+execute context) — the earlier "
        "'Feb 2026 terminal-fail' removal was an undetected "
        "contradiction with that still-live guard."
    )
    # And the emit payload DOES carry the retry-count keys again —
    # the outer-retry pause path is reinstated, not dead code.
    assert '"verify_retry_count": int(' in verify_block, (
        "W3 · 2026-08 contract: _do_verify's pause emit must carry "
        "verify_retry_count — routers/loop.py's outer-retry cap "
        "consumes this and is live code again."
    )
    assert '"max_verify_retries": 3' in verify_block, (
        "W3 · 2026-08 contract: max_verify_retries=3 must be present "
        "in the pause emit again."
    )
    # The loop-wide global heal cap guard (the actual dedup fix) must
    # still be present and still hard-fail on cap-consumed reentry —
    # this is what makes PAUSED_FOR_USER safe against duplicate events.
    assert "_global_healed" in verify_block and "MAX_SELF_HEALS" in verify_block, (
        "the pre-existing loop-wide total_heal_attempts cap guard "
        "must remain — it is what prevents duplicate 'Verify failed' "
        "events on retry, not the removal of PAUSED_FOR_USER."
    )
