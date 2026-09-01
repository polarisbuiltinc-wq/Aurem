"""
core/parliament/councils.py — 2026-09-08 Phase 3 god-class split.

`_CouncilMember`, `_Council`, `CouncilA`, `CouncilB`, `CouncilC`.
Moved verbatim out of the single core/parliament.py (zero logic
change) — S8: the data-table-vs-subclass smell in these 3 councils
is intentionally NOT touched this phase.
"""
from __future__ import annotations

import asyncio
import logging

from . import llm_call as _llm_call_mod
from .llm_call import MAX_CONCURRENT_LLM_CALLS
from .scoring import _strip_fences, _score_output

logger = logging.getLogger("aurem-dev.parliament")


class _CouncilMember:
    """A single voting member of a Council.  Calls an LLM at a fixed
    temperature with a fixed persona, via the global concurrency cap
    + circuit breaker."""

    def __init__(self, *, name: str, temperature: float, persona: str,
                 mode: str = "code", review_mode: str = "pro",
                 max_tokens: int = 4000):
        self.name        = name
        self.temperature = temperature
        self.persona     = persona
        self.mode        = mode
        self.review_mode = review_mode
        self.max_tokens  = max_tokens

    async def cast_vote(self, *, task: str, context: dict) -> dict:
        """Returns: {member, output, score, error, latency_ms, temp}."""
        # Iter 212m-159 — surface the V2 routing primary on each trace so
        # Langfuse dashboards can filter Parliament runs by model.
        from services.llm import (
            LONGCAT_ENABLED, COUNCIL_B_GLM_ENABLED, CEO_RESCUE_ENABLED,
            council_a_primary_model, council_b_primary_model,
        )
        council_id = context.get("council") or ""
        if council_id == "A":
            primary_model = council_a_primary_model()
        elif council_id == "B":
            primary_model = council_b_primary_model()
        else:
            primary_model = "deepseek/deepseek-chat"
        # Feb 2026 · Iter 362 — dynamic max_tokens support (Bug A).
        # Founder reproduced a size-correlated failure: large existing
        # files → "LLM produced no usable file content" (output cap
        # hit → truncation → integrity guard rejects). Small files
        # succeed but slowly (still fine — LLM latency, upstream).
        # Root cause: max_tokens=4000 (default) is not enough to emit
        # a full-file rewrite for a >~15 KB file. Fix: allow the
        # caller (loop_engine._gen_via_parliament) to pass an
        # explicit `max_tokens_override` in the context, computed
        # from the file's current byte length. Capped at 32_000 —
        # the upstream provider ceiling that the LLM gateway honours.
        _mto = context.get("max_tokens_override")
        if isinstance(_mto, int) and _mto > 0:
            _effective_max = min(32_000, max(self.max_tokens, _mto))
        else:
            _effective_max = self.max_tokens
        # 2026-08 hardening (F3) — per-agent label so this member's
        # ledger row is separable from the other 2 members + CEO
        # (Council-premium pricing needs this, not a "loop.execute" blob).
        from services.loop_token_ledger import agent_call_context
        _agent_label = f"council-{self.name.split('-')[0].lower()}"
        async with agent_call_context(_agent_label):
            content, latency_ms, err = await _llm_call_mod._llm_call_protected(
                system=self.persona, user=task,
                max_tokens=_effective_max, mode=self.mode,
                review_mode=self.review_mode,
                user_id=context.get("user_id"),
                temperature=self.temperature,
                trace_name=f"parliament.council.{council_id or '?'}.{self.name}",
                trace_metadata={
                    "trace_id":         context.get("parliament_trace_id"),
                    "council":          council_id,
                    "member":           self.name,
                    "task_type":        context.get("task_type"),
                    "user_id":          context.get("user_id"),
                    "file_path":        context.get("file_path"),
                    "primary_model":    primary_model,
                    "v2_longcat":       LONGCAT_ENABLED,
                    "v2_council_b_glm": COUNCIL_B_GLM_ENABLED,
                    "v2_ceo_rescue":    CEO_RESCUE_ENABLED,
                    "max_tokens":       _effective_max,
                    "max_tokens_default": self.max_tokens,
                },
            )
        if err:
            return {
                "member":     self.name,
                "output":     "",
                "score":      0.0,
                "error":      err,
                "latency_ms": latency_ms,
                "temp":       self.temperature,
            }
        out = _strip_fences(content)
        score = _score_output(
            out,
            task_type=context.get("task_type", "code_fix"),
        )
        return {
            "member":     self.name,
            "output":     out,
            "score":      score,
            "error":      None,
            "latency_ms": latency_ms,
            "temp":       self.temperature,
        }


# ─────────────────────────────────────────────────────────────────────
#  Councils
# ─────────────────────────────────────────────────────────────────────

class _Council:
    name: str = "?"
    members: list[_CouncilMember] = []

    async def vote(self, *, task: str, context: dict) -> list[dict]:
        if not self.members:
            return []
        logger.info("Council %s calling %d members in parallel "
                    "(global concurrency cap=%d)",
                    self.name, len(self.members), MAX_CONCURRENT_LLM_CALLS)
        for m in self.members:
            logger.info("Council %s member %s called — temp %.1f",
                        self.name, m.name, m.temperature)
        votes = await asyncio.gather(
            *[m.cast_vote(task=task, context=context) for m in self.members],
            return_exceptions=False,
        )
        return list(votes)


_COUNCIL_A_PERSONA = (
    "You are a senior AI software engineer participating in a small "
    "council that will collectively decide on a code fix.  Read the "
    "task carefully and write the COMPLETE final file contents.  Do "
    "NOT add commentary.  Do NOT wrap in code fences.  Preserve any "
    "existing functionality the task does not explicitly change.  "
    "If the task mentions a security vulnerability (SQL injection, "
    "secret leak, eval, command injection, path traversal, weak "
    "crypto), prioritise eliminating the vuln class first."
)


class CouncilA(_Council):
    name = "A"
    members = [
        _CouncilMember(name="A1-conservative",
                       temperature=0.1, persona=_COUNCIL_A_PERSONA),
        _CouncilMember(name="A2-balanced",
                       temperature=0.2, persona=_COUNCIL_A_PERSONA),
        _CouncilMember(name="A3-creative",
                       temperature=0.3, persona=_COUNCIL_A_PERSONA),
    ]


_COUNCIL_B_PERSONAS = (
    # 0.3 — precise data analyst
    "You are a data analyst.  Be precise, cite specific numbers when "
    "available.  Structure: key finding → supporting data → "
    "implication.  Return analysis only — no commentary about the task.",
    # 0.4 — strategic advisor
    "You are a strategic advisor.  Think about long-term implications "
    "and second-order effects.  Structure: situation → options → "
    "recommendation.  Return analysis only.",
    # 0.5 — skeptical reviewer
    "You are a skeptical reviewer.  Find gaps, risks, and what's "
    "missing.  Challenge assumptions.  Structure: what's claimed → "
    "what's missing → what could go wrong.  Return analysis only.",
)


class CouncilB(_Council):
    """Iter 212m-155 — analysis / advisory tasks.

    Three members with progressively higher temperatures to balance
    rigour (analyst) vs strategy (advisor) vs adversarial review
    (skeptic).  Scoring uses a structural heuristic — analysis has no
    binary pass/fail like code tests, so we credit numbers, structure,
    appropriate length, and the presence of an actionable conclusion.

    Iter 212m-159 — mode="analysis" instead of "chat".  When
    COUNCIL_B_GLM_ENABLED=true, services/llm.py routes analysis to
    GLM-5.2 (reasoning model) with DeepSeek V3 rescue.  When the flag
    is False, mode="analysis" falls through to the same DeepSeek path
    as mode="chat", so Council B is byte-identical to legacy.
    """
    name = "B"
    members = [
        _CouncilMember(name="B1-analyst",
                       temperature=0.3, persona=_COUNCIL_B_PERSONAS[0],
                       mode="analysis", max_tokens=1200),
        _CouncilMember(name="B2-advisor",
                       temperature=0.4, persona=_COUNCIL_B_PERSONAS[1],
                       mode="analysis", max_tokens=1200),
        _CouncilMember(name="B3-skeptic",
                       temperature=0.5, persona=_COUNCIL_B_PERSONAS[2],
                       mode="analysis", max_tokens=1200),
    ]


_COUNCIL_C_PERSONAS = (
    # 0.5 — direct copywriter
    "You are a direct copywriter.  Short sentences.  Active voice.  "
    "One clear call-to-action at the end.  No fluff.  Return the "
    "final copy only.",
    # 0.6 — relationship builder
    "You are a relationship builder.  Warm, personal, shows you "
    "understand the recipient's situation.  Build trust before "
    "asking.  Return the final copy only.",
    # 0.7 — data-driven marketer
    "You are a data-driven marketer.  Lead with a specific proof "
    "point or number.  Connect it to the recipient's problem.  Then "
    "ask.  Return the final copy only.",
)


class CouncilC(_Council):
    """Iter 212m-155 — writing tasks (emails, outreach, copy).

    Three voices: direct copy / relationship / data-led.  Scoring
    favours appropriate length, presence of a CTA, personalisation,
    and avoids the weak "I"-led opening anti-pattern.
    """
    name = "C"
    members = [
        _CouncilMember(name="C1-direct",
                       temperature=0.5, persona=_COUNCIL_C_PERSONAS[0],
                       mode="chat", max_tokens=600),
        _CouncilMember(name="C2-warm",
                       temperature=0.6, persona=_COUNCIL_C_PERSONAS[1],
                       mode="chat", max_tokens=600),
        _CouncilMember(name="C3-data",
                       temperature=0.7, persona=_COUNCIL_C_PERSONAS[2],
                       mode="chat", max_tokens=600),
    ]
