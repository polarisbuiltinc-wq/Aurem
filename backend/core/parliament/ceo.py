"""
core/parliament/ceo.py — 2026-09-08 Phase 3 god-class split.

`CEO`, `_ceo_judge_call_with_rescue`. Moved verbatim out of the
single core/parliament.py (zero logic change).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

from . import llm_call as _llm_call_mod
from .scoring import detect_output_type, CEO_TEMPS

logger = logging.getLogger("aurem-dev.parliament")


class CEO:
    SCORE_FLOOR = 0.55      # any candidate ≥ this is acceptable.

    async def decide(self, *, task: str, votes: list[dict],
                     context: dict) -> dict:
        """Returns {status, output, winner, scores, ceo_picked,
                    reasoning, ceo_temp_key, ceo_temp_value}."""
        # GAP 3 — explicit output-type detection, NOT council-based assumption.
        output_type = detect_output_type(task, council=context.get("council"))
        ceo_temp    = CEO_TEMPS.get(output_type, 0.0)
        if not votes:
            return {
                "status":         "manual_review",
                "output":         None,
                "winner":         None,
                "scores":         [],
                "ceo_picked":     False,
                "reasoning":      "No council votes were cast.",
                "ceo_temp_key":   output_type,
                "ceo_temp_value": ceo_temp,
            }
        usable = [v for v in votes if v.get("output") and v.get("score", 0) > 0]
        scores = [
            {"member": v["member"], "score": v["score"],
             "temp":   v["temp"],   "len":   len(v.get("output") or ""),
             "error":  v.get("error")}
            for v in votes
        ]
        if not usable:
            logger.warning("CEO deciding — temp %.2f — but no usable votes",
                           ceo_temp)
            # 2026-08 hardening (F2) — if every vote failed because the
            # cost cap blocked the call (not a real LLM error), surface a
            # distinguishable error_code so loop_engine.py's additive
            # pause-check can PAUSE (not fail) the loop cleanly.
            _all_cost_capped = bool(votes) and all(
                v.get("error") == "cost_cap_reached" for v in votes
            )
            return {
                "status":         "manual_review",
                "output":         None,
                "winner":         None,
                "scores":         scores,
                "ceo_picked":     False,
                "reasoning":      ("Monthly task budget reached." if _all_cost_capped
                                    else "All council members refused or errored."),
                "ceo_temp_key":   output_type,
                "ceo_temp_value": ceo_temp,
                **({"error_code": "COST_CAP_REACHED"} if _all_cost_capped else {}),
            }
        # Heuristic pick: best score, ties broken by lowest temperature.
        usable.sort(key=lambda v: (-v["score"], v["temp"]))
        winner = usable[0]
        logger.info(
            "Council %s winner: member %s — score %.2f (temp %.1f)",
            context.get("council", "A"), winner["member"],
            winner["score"], winner["temp"],
        )
        if winner["score"] >= self.SCORE_FLOOR:
            logger.info("CEO deciding — temp %.2f — accepting %s @ %.2f "
                        "(output_type=%s)",
                        ceo_temp, winner["member"], winner["score"],
                        output_type)
            return {
                "status":         "success",
                "output":         winner["output"],
                "winner":         winner["member"],
                "scores":         scores,
                "ceo_picked":     True,
                "reasoning":      (
                    f"Winner {winner['member']} scored {winner['score']:.2f} "
                    f">= floor {self.SCORE_FLOOR}.  Output type: "
                    f"{output_type}, CEO temp: {ceo_temp}"
                ),
                "ceo_temp_key":   output_type,
                "ceo_temp_value": ceo_temp,
            }
        # Below floor — invoke the LLM judge to break ties.
        ceo_pick = await self._llm_judge(
            task=task, candidates=usable, context=context,
            temperature=ceo_temp,
        )
        if ceo_pick is not None:
            chosen = usable[ceo_pick]
            return {
                "status":         "success",
                "output":         chosen["output"],
                "winner":         chosen["member"],
                "scores":         scores,
                "ceo_picked":     True,
                "reasoning":      "Below floor — CEO LLM picked best of class.",
                "ceo_temp_key":   output_type,
                "ceo_temp_value": ceo_temp,
            }
        return {
            "status":         "manual_review",
            "output":         None,
            "winner":         None,
            "scores":         scores,
            "ceo_picked":     False,
            "reasoning":      "All candidates below acceptance floor and CEO "
                              "judge could not break the tie.",
            "ceo_temp_key":   output_type,
            "ceo_temp_value": ceo_temp,
        }

    async def _llm_judge(self, *, task: str, candidates: list[dict],
                         context: dict, temperature: float) -> Optional[int]:
        if not candidates:
            return None

        def _excerpt(s):
            return (s or "")[:800]

        choices_text = "\n\n".join(
            f"--- CANDIDATE {i} (member={c['member']}, score={c['score']:.2f}) ---\n"
            f"{_excerpt(c['output'])}"
            for i, c in enumerate(candidates)
        )
        sys = (
            "You are the CEO of an engineering council.  Three members "
            "proposed candidate file contents for a task.  Pick the "
            "best one.  Reply ONLY with the candidate index (single "
            "digit, 0/1/2).  No explanation, no JSON, no commentary."
        )
        usr = f"TASK:\n{task[:1500]}\n\nCANDIDATES:\n{choices_text}"
        content, _ms, err = await _ceo_judge_call_with_rescue(
            system=sys, user=usr, max_tokens=8,
            user_id=context.get("user_id"),
            temperature=temperature,
            trace_metadata={
                "trace_id":   context.get("parliament_trace_id"),
                "council":    context.get("council"),
                "n_candidates": len(candidates),
                "task_type":  context.get("task_type"),
            },
        )
        if err:
            logger.warning("CEO judge LLM error: %s", err)
            return None
        m = re.search(r"\d", content)
        if not m:
            return None
        idx = int(m.group(0))
        return idx if 0 <= idx < len(candidates) else None


# ─────────────────────────────────────────────────────────────────────
#  Iter 212m-159 — CEO judge primary+rescue wrapper.
# ─────────────────────────────────────────────────────────────────────
async def _ceo_judge_call_with_rescue(
    *, system: str, user: str, max_tokens: int,
    user_id: Optional[str], temperature: float,
    trace_metadata: dict,
) -> tuple[str, float, Optional[str]]:
    """CEO judge LLM call with optional DeepSeek rescue.

    When `CEO_RESCUE_ENABLED=False` (default): single call to
    `_llm_call_protected` with the legacy params (mode=chat, review_mode=swift
    → GLM-5.2 primary via the V2 routing).

    When True (V2): wrap the primary call in `CEO_PRIMARY_TIMEOUT_S` seconds.
    On TimeoutError OR empty content, issue a second call with the rescue
    model (DeepSeek V3 by default) under a separate Langfuse span
    `parliament.ceo.rescue`.  This eliminates the single-point-of-failure
    that the CEO was previously.

    Returns the same (content, latency_ms, err_tag) tuple shape as
    `_llm_call_protected`.
    """
    from services.llm import CEO_RESCUE_ENABLED, CEO_PRIMARY_TIMEOUT_S

    md_primary = {**trace_metadata, "ceo_role": "primary", "ceo_rescue_enabled": CEO_RESCUE_ENABLED}
    # 2026-08 hardening (F3) — CEO calls (primary + rescue) get their
    # own "ceo" agent label, separable from the 3 council-member votes.
    from services.loop_token_ledger import agent_call_context

    if not CEO_RESCUE_ENABLED:
        async with agent_call_context("ceo"):
            return await _llm_call_mod._llm_call_protected(
                system=system, user=user, max_tokens=max_tokens,
                mode="chat", review_mode="swift",
                user_id=user_id, temperature=temperature,
                trace_name="parliament.ceo.judge",
                trace_metadata=md_primary,
            )

    # V2 — primary with hard timeout
    async def _primary_call():
        async with agent_call_context("ceo"):
            return await _llm_call_mod._llm_call_protected(
                system=system, user=user, max_tokens=max_tokens,
                mode="chat", review_mode="swift",
                user_id=user_id, temperature=temperature,
                trace_name="parliament.ceo.judge",
                trace_metadata=md_primary,
            )
    primary_task = _primary_call()
    t0 = time.monotonic()
    primary_timed_out = False
    primary_err: Optional[str] = None
    primary_content = ""
    primary_latency = 0.0
    try:
        primary_content, primary_latency, primary_err = await asyncio.wait_for(
            primary_task, timeout=CEO_PRIMARY_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        primary_timed_out = True
        primary_err = "primary_timeout"
        primary_latency = round((time.monotonic() - t0) * 1000, 1)
        logger.warning(
            "CEO judge primary (GLM-5.2) exceeded %.1fs — firing DeepSeek rescue",
            CEO_PRIMARY_TIMEOUT_S,
        )

    # Decide whether to rescue: timeout OR primary failed OR empty content
    needs_rescue = primary_timed_out or bool(primary_err) or not (primary_content or "").strip()
    if not needs_rescue:
        return primary_content, primary_latency, None

    md_rescue = {
        **trace_metadata,
        "ceo_role":          "rescue",
        "rescue_reason":     "timeout" if primary_timed_out else (primary_err or "empty"),
        "primary_latency_ms": primary_latency,
    }
    # DeepSeek rescue via mode="chat" (no review_mode → bypasses GLM, uses DeepSeek)
    async with agent_call_context("ceo"):
        rescue_content, rescue_latency, rescue_err = await _llm_call_mod._llm_call_protected(
            system=system, user=user, max_tokens=max_tokens,
            mode="chat", review_mode="",
            user_id=user_id, temperature=temperature,
            trace_name="parliament.ceo.rescue",
            trace_metadata=md_rescue,
        )
    if rescue_err or not (rescue_content or "").strip():
        # Both primary and rescue failed → return whichever has signal.
        if (primary_content or "").strip():
            return primary_content, primary_latency, None
        return "", rescue_latency, (rescue_err or "rescue_empty")
    return rescue_content, rescue_latency, None
