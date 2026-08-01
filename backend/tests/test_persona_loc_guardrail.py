"""
tests/test_persona_loc_guardrail.py — Persona LOC early-warning guard
(Feb 2026, Session G · Batch 4d follow-up to the persona-dedupe work).

Purpose:
    The `AUREM_CTO_PERSONA` string in `services/orchestrator.py` gets
    re-sent to the LLM on every chat turn AND every tool iteration.
    A hard latency-budget test (`test_iter129_chat_latency_budget`)
    already caps it at 22,000 chars — but by the time that test
    fails, the persona has ALREADY landed on main and every prod
    chat is paying the tokens.

    This test is the early-warning: fail LOUDLY as soon as the
    persona crosses 20,000 chars (5% headroom before the real red
    line). Cheap, deterministic, catches "one more rule" drift
    before the hard budget hits.

Modes:
    - **Default**: emit `UserWarning` via `warnings.warn` — visible in
      pytest output, no fail. This is what the founder asked for:
      early-warning noise, not a merge blocker at 20k. The real
      `test_iter129_chat_latency_budget` hard-caps at 22k and
      **stays** as the merge blocker.

    - `PERSONA_GUARDRAIL_HARD=1` env var flips it to a hard fail —
      useful on branches that specifically want to enforce the
      early-warning threshold (e.g. a persona-diet PR).

Proof (verified inline):
    - simulated over-budget state (mock a 20,500-char persona) → guard fires
    - simulated under-budget state (mock a 19,500-char persona) → guard silent
    See `test_guardrail_fires_over_threshold_via_mock` +
    `test_guardrail_silent_under_threshold_via_mock` below.
"""
from __future__ import annotations

import os
import warnings

import pytest

from services.orchestrator import AUREM_CTO_PERSONA


WARN_THRESHOLD = 20_000   # 5% below the hard budget — early warning
HARD_BUDGET    = 22_000   # matches test_iter129_chat_latency_budget


# ── Live-persona guard (the one CI runs against real code) ───────────

def test_persona_under_early_warning_threshold() -> None:
    """The real AUREM_CTO_PERSONA should stay under 20,000 chars.

    Crossing 20k means the persona has drifted within 5% of the
    22k hard budget — someone should trim before the next feature
    creep pushes it over the red line. When this fires, run the
    dedupe playbook in `memory/PRD.md` (Persona-Dedupe change log
    entry, Feb 2026).

    Founder directive: this is a WARNING by default, not a merge
    blocker. Set `PERSONA_GUARDRAIL_HARD=1` to flip to hard fail.
    """
    n = len(AUREM_CTO_PERSONA)
    headroom = HARD_BUDGET - n
    if n >= WARN_THRESHOLD:
        msg = (
            f"PERSONA LOC EARLY-WARNING — AUREM_CTO_PERSONA is "
            f"{n:,} chars, which crosses the {WARN_THRESHOLD:,} "
            f"early-warning threshold ({HARD_BUDGET - WARN_THRESHOLD:,} "
            f"char headroom until the hard budget). Trim now, before "
            f"you hit the {HARD_BUDGET:,} char ceiling and every "
            f"chat turn starts paying an extra ~1k input tokens. "
            f"Current headroom to hard budget: {headroom:,} chars."
        )
        # Hard-fail opt-in for persona-diet PRs:
        if os.getenv("PERSONA_GUARDRAIL_HARD") == "1":
            pytest.fail(msg)
        warnings.warn(msg, stacklevel=2)


def test_persona_under_hard_budget() -> None:
    """Belt-and-braces mirror of `test_iter129_chat_latency_budget`.

    Even if the early-warning is toggled to warn-only, the hard
    budget stays as a real assertion so a runaway persona-growth PR
    still blocks merge.
    """
    n = len(AUREM_CTO_PERSONA)
    assert n < HARD_BUDGET, (
        f"AUREM_CTO_PERSONA is {n:,} chars — over the {HARD_BUDGET:,} "
        f"latency budget. See test_iter129_chat_latency_budget."
    )


# ── Mock-driven proof: the guard actually fires ──────────────────────
# These do NOT test the real persona — they inject fake string values
# into the check-logic to prove the guard logic itself works. Real
# persona coverage is `test_persona_under_early_warning_threshold`
# above. Together they give both "guard mechanism works" AND "current
# persona respects the guard" coverage.

def _check(persona: str) -> tuple[bool, str]:
    """Same logic as the real test — factored so we can drive it
    with synthetic values for the guard-mechanism test."""
    n = len(persona)
    if n >= WARN_THRESHOLD:
        return True, (
            f"PERSONA LOC EARLY-WARNING — {n:,} chars ≥ "
            f"{WARN_THRESHOLD:,} threshold"
        )
    return False, ""


def test_guardrail_fires_over_threshold_via_mock() -> None:
    """Simulate a 20,500-char persona → guard MUST fire."""
    fake = "x" * 20_500
    fired, msg = _check(fake)
    assert fired, "guard did not fire at 20,500 chars"
    assert "EARLY-WARNING" in msg


def test_guardrail_silent_under_threshold_via_mock() -> None:
    """Simulate a 19,500-char persona → guard MUST be silent."""
    fake = "x" * 19_500
    fired, _ = _check(fake)
    assert not fired, "guard fired at 19,500 chars (should be silent)"


def test_guardrail_fires_exactly_at_threshold() -> None:
    """Boundary — the guard uses `>=` so exactly 20,000 fires."""
    fired_at_20k, _ = _check("x" * WARN_THRESHOLD)
    fired_below,  _ = _check("x" * (WARN_THRESHOLD - 1))
    assert fired_at_20k, f"guard did not fire at exactly {WARN_THRESHOLD}"
    assert not fired_below, "guard fired at threshold - 1 (off-by-one)"


def test_warn_only_env_flag_downgrades_to_warning(monkeypatch) -> None:
    """The default (no env override) must EMIT `UserWarning` not fail
    when persona crosses the 20k threshold — founder directive.

    We simulate an over-budget state by monkeypatching the imported
    persona to a 20,500-char string, then confirm the guard emits a
    warning + returns without raising. This is the actual founder-
    directed default mode (warn-only).
    """
    import sys
    import services.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "AUREM_CTO_PERSONA", "x" * 20_500)
    # Rebind the module-local import so the guard reads the mock:
    monkeypatch.setattr(sys.modules[__name__], "AUREM_CTO_PERSONA", "x" * 20_500)
    monkeypatch.delenv("PERSONA_GUARDRAIL_HARD", raising=False)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        test_persona_under_early_warning_threshold()
        matched = [w for w in caught
                   if "EARLY-WARNING" in str(w.message)]
        assert matched, (
            "default warn mode did not emit the EARLY-WARNING message"
        )


def test_hard_env_flag_upgrades_to_pytest_fail(monkeypatch) -> None:
    """`PERSONA_GUARDRAIL_HARD=1` must upgrade the warning to a hard
    pytest.fail — useful for persona-diet PRs that want a red run
    until the dedupe lands."""
    import sys
    import services.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "AUREM_CTO_PERSONA", "x" * 20_500)
    monkeypatch.setattr(sys.modules[__name__], "AUREM_CTO_PERSONA", "x" * 20_500)
    monkeypatch.setenv("PERSONA_GUARDRAIL_HARD", "1")

    with pytest.raises(BaseException, match="EARLY-WARNING"):
        test_persona_under_early_warning_threshold()
