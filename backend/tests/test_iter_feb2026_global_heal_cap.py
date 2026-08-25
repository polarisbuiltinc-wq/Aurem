"""
test_iter_feb2026_global_heal_cap.py — Feb 2026

Founder-reported bug: MAX_SELF_HEALS = 2 but live run showed 4+
"Verify failed after 2 attempts" events in ONE loop run. Root cause:
`heal_attempt` was scoped to a single `_do_verify` call. Each outer
verify-retry (user resume, independent-verifier reject auto-resume,
ship-block re-execute) re-entered `_do_verify` with a fresh
`heal_attempt = 1` counter → net 2×N heals possible.

Fix: `total_heal_attempts` in `self.context` tracks heals across the
WHOLE loop run. When it hits MAX_SELF_HEALS globally, the loop
HARD-FAILS (terminal state, not paused_for_user).

Also: each self_healing SSE event now carries `total_heal_attempts`
and `max_heal_attempts` fields so the frontend chip reads the
authoritative counter rather than regex-parsing the message text.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


@pytest.mark.source_of_truth
def test_global_heal_cap_enforced_in_do_verify():
    """`_do_verify` must consult a loop-run-wide counter BEFORE
    entering the per-file heal loop, and hard-fail (not pause) when
    the cap is already exhausted."""
    src = (_REPO / "backend" / "services" / "loop_engine.py").read_text(encoding="utf-8")
    assert "total_heal_attempts" in src, (
        "loop_engine.py must persist total_heal_attempts in "
        "engine.context so a fresh _do_verify re-entry can't get "
        "another 2 free attempts."
    )
    # The initial check MUST happen before the per-attempt loop
    # opens, and MUST hard-fail via _fail() (not paused_for_user).
    assert 'if _global_healed >= MAX_SELF_HEALS' in src, (
        "loop_engine.py must short-circuit _do_verify when the "
        "loop-wide heal cap is already reached on entry."
    )
    assert "Global heal cap reached" in src, (
        "Cap-exhaustion narration missing — user won't understand "
        "why the loop halted."
    )


def test_heal_event_carries_backend_authoritative_counter():
    """Each self-heal attempt narration must carry the loop-wide
    counter fields so the frontend chip renders the authoritative
    count, not a per-cycle-fresh 1."""
    src = (_REPO / "backend" / "services" / "loop_engine.py").read_text(encoding="utf-8")
    assert '"total_heal_attempts":' in src, (
        "Self-heal SSE event payload must include total_heal_attempts."
    )
    assert '"max_heal_attempts":' in src, (
        "Self-heal SSE event payload must include max_heal_attempts."
    )


def test_frontend_reads_backend_authoritative_counter():
    """The frontend `self_healing` event handler must prefer the
    backend `total_heal_attempts` field over the regex-parsed
    message string. Regression guard against the pre-fix shape."""
    src = (_REPO.parent / "app" / "frontend" / "src" / "components" / "ChatPanel.jsx")
    if not src.exists():
        # Alt path when the tests folder resolves differently.
        src = _REPO / "frontend" / "src" / "components" / "ChatPanel.jsx"
    body = src.read_text(encoding="utf-8")
    assert "data.total_heal_attempts" in body, (
        "ChatPanel must read backend authoritative total_heal_attempts."
    )
    assert "data.max_heal_attempts" in body, (
        "ChatPanel must read backend authoritative max_heal_attempts."
    )
    # The startedAt epoch must be captured for the per-attempt timer.
    assert "startedAt: Date.now()" in body, (
        "ChatPanel must capture the attempt start epoch so the "
        "SelfHealIndicator can render a live seconds counter."
    )


def test_selfheal_indicator_renders_live_timer():
    """SelfHealIndicator must render a data-testid='self-heal-timer'
    element that ticks from 0 up while an attempt is active."""
    src = _REPO / "frontend" / "src" / "components" / "LoopActionCards.jsx"
    body = src.read_text(encoding="utf-8")
    assert 'data-testid="self-heal-timer"' in body, (
        "SelfHealIndicator must expose a testid'd timer node for "
        "test-verification and founder-visible acknowledgement."
    )
    assert "setInterval" in body, (
        "SelfHealIndicator must actually tick the timer (setInterval)."
    )
    assert "clearInterval" in body, (
        "SelfHealIndicator must clean up the timer on unmount / "
        "attempt-end to prevent zombie counters."
    )
