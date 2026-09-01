"""
core/parliament/self_heal.py — 2026-09-08 Phase 3 god-class split.

`SelfHeal`. Moved verbatim out of the single core/parliament.py
(zero logic change).
"""
from __future__ import annotations

from typing import Optional

from . import llm_call as _llm_call_mod
from .breaker import _GLOBAL_BREAKER
from .scoring import _strip_fences


class SelfHeal:
    """Healer used by Verify phase to recover linter failures.

    Critical contract: this class **never** adds its own retry counter
    on top of the caller's.  The caller (loop_engine._do_verify)
    passes `round_num` and `max_rounds`; this class enforces only the
    contract supplied.  Without this, loop_engine's existing 2-round
    loop + a parliament internal round = 4 rounds total which is
    undefined behaviour."""

    SYS_PROMPT = (
        "You are ORA in self-heal mode.  A file you wrote failed static "
        "analysis.  Rewrite ONLY the file content to fix the reported "
        "errors.  Do not add commentary.  Do not wrap in code fences.  "
        "Preserve all existing functionality that wasn't responsible "
        "for the failure."
    )

    async def heal(self, *, task: str, all_attempts: list[dict],
                   round_num: int,
                   max_rounds: int = 2) -> dict:
        """Heal a file based on the failing attempts so far.

        `all_attempts` is a list of `{output, score, error}` dicts —
        the most recent is the current broken state.  `round_num` is
        the heal-round counter (caller-owned).  `max_rounds` is the
        caller's hard ceiling.  This class respects it; no internal
        retry is added.

        Returns: {status, output, round_num, max_rounds, temp_used,
                  reason}.  Status is `"retry"` (caller may re-verify),
                  `"escalate"` (caller's max reached or LLM gave up),
                  or `"circuit_open"` (breaker tripped — caller should
                  use its legacy fallback path).
        """
        if round_num >= max_rounds:
            return {
                "status":     "escalate",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  0.0,
                "reason":     "caller max rounds reached",
            }
        if not all_attempts:
            return {
                "status":     "retry",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  0.0,
                "reason":     "no attempts supplied",
            }
        # Circuit breaker — bail out cheaply if upstream LLM is sick.
        if not _GLOBAL_BREAKER.should_attempt():
            return {
                "status":     "circuit_open",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  0.0,
                "reason":     "global circuit breaker is OPEN",
            }

        last = all_attempts[-1]
        history_block = ""
        if len(all_attempts) > 1:
            history_block = "\n\n--- PRIOR FAILED ATTEMPTS ---\n"
            for i, prev in enumerate(all_attempts[:-1], start=1):
                err = (prev.get("error") or "")[:240]
                history_block += f"Attempt {i} error: {err}\n"
            history_block += "Do NOT repeat the same fix.\n"
        # Temperature escalation per round — bounded.
        temp = min(0.05 + 0.15 * round_num, 0.35)
        user_msg = (
            task + history_block + (
                f"\n\n--- CURRENT CONTENT ---\n{last.get('output', '')[:6000]}\n"
                f"--- END CONTENT ---\n\n"
                f"--- LAST ERROR ---\n{last.get('error', '')[:1000]}\n"
                f"--- END ERROR ---\n\nReturn the corrected file content only."
            )
        )
        content, _ms, err = await _llm_call_mod._llm_call_protected(
            system=self.SYS_PROMPT, user=user_msg,
            max_tokens=2500, mode="code", review_mode="pro",
            temperature=temp,
            trace_name="parliament.selfheal",
            trace_metadata={
                "round_num":   round_num,
                "max_rounds":  max_rounds,
                "temp":        temp,
            },
        )
        if err:
            return {
                "status":     "escalate",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  temp,
                "reason":     f"llm:{err}",
            }
        out = _strip_fences(content)
        if not out:
            return {
                "status":     "escalate",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  temp,
                "reason":     "empty_output",
            }
        return {
            "status":     "retry",
            "output":     out,
            "round_num":  round_num,
            "max_rounds": max_rounds,
            "temp_used":  temp,
            "reason":     None,
        }
