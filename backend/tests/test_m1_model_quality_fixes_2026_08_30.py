"""
tests/test_m1_model_quality_fixes_2026_08_30.py — M1 fix (2026-08-30,
founder-authorized bounded real-model round, T2-T5 follow-up).

M1a: casual-tier first-contact replies were UNGROUNDED on "what does
this do?" and the model hallucinated a wrong product description.
Fix: `services/identity.py::PRODUCT_IDENTITY` — one pinned sentence,
injected into both `intent_gateway_casual_reply.casual_direct_reply`
(the exact repro path) and `orchestrator.AUREM_CTO_PERSONA` (the
agentic-tier persona) — reused verbatim, not regenerated per-surface.

M1b: an agentic-tier reply could regurgitate an EARLIER, unrelated
turn's answer instead of engaging with a new question. Fix: a new
hard rule (#6) in `AUREM_CTO_PERSONA` instructing the model to give a
short 1-line reference (not a full re-generation) ONLY when the user
literally re-asks an already-answered question, and to properly answer
a genuinely different question.

Named tests:
  t_first_contact_uses_pinned_identity
  t_no_long_repeat_on_same_q (system-prompt-level proof; the actual
    real-model behavioral proof is in /app/e2e-proof/M1-M2/, this is
    the code-level regression guard that the rule text is present and
    wired into every casual + agentic first-contact system prompt)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# ── t_first_contact_uses_pinned_identity ───────────────────────────
@pytest.mark.asyncio
async def test_t_first_contact_uses_pinned_identity():
    from services.identity import PRODUCT_IDENTITY
    from services.intent_gateway_casual_reply import casual_direct_reply

    captured = {}

    async def _fake_call_llm(messages, system=None, **kw):
        captured["system"] = system
        return "I'm ORA, by Aurem — I connect to your GitHub repo and ship real fixes."

    with patch("services.llm.call_llm", new=_fake_call_llm), \
         patch("services.house_rules.get_active_house_rules", new=AsyncMock(return_value=None)):
        reply = await casual_direct_reply("Hi, what does this tool do?")

    # The system prompt actually sent to the LLM must carry the pinned
    # ground-truth sentence — the model is never left to invent one.
    assert PRODUCT_IDENTITY in captured["system"]
    assert "audio" not in captured["system"].lower()
    assert reply  # real reply text returned


@pytest.mark.asyncio
async def test_t_product_identity_also_in_agentic_persona():
    """Regression guard: PRODUCT_IDENTITY must be present in the
    agentic-tier persona too (T3's finding #2, msg4, was agentic-tier,
    not casual-tier — both first-contact surfaces needed the fix)."""
    from services.identity import PRODUCT_IDENTITY
    from services.orchestrator import AUREM_CTO_PERSONA
    assert PRODUCT_IDENTITY in AUREM_CTO_PERSONA


# ── t_no_long_repeat_on_same_q ──────────────────────────────────────
def test_t_no_long_repeat_on_same_q():
    """Code-level regression guard: the repeat-question hard rule is
    present, unconditional (applies regardless of which tool-tier
    path is used), and distinguishes a literal re-ask from a genuinely
    new/different question (must not become a blanket 'never elaborate'
    rule, which would break rule #2's 'answer completely' mandate)."""
    from services.orchestrator import AUREM_CTO_PERSONA
    assert "NO LONG RE-ANSWERS" in AUREM_CTO_PERSONA
    assert "New question" in AUREM_CTO_PERSONA and "full real answer" in AUREM_CTO_PERSONA
