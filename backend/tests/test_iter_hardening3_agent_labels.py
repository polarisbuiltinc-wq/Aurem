"""
tests/test_iter_hardening3_agent_labels.py — 2026-08 hardening (F3).

Council-premium pricing needs the 3 council-member votes + the CEO
call + a single-model fallback call SEPARABLE in the ledger — not
one "loop.execute" blob. `agent_call_context()` tags each call
per-call (not per-phase); `log_llm_usage()` encodes it into `route`
so no schema/collection change is needed (reuses `ora_chat_usage`
via the existing `cost_tracker.log_call`).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_agent_call_context_tags_route_distinctly_per_agent(monkeypatch):
    from services import loop_token_ledger as ledger

    captured = []

    async def _fake_log_call(**kwargs):
        captured.append(kwargs)
        return 0.0

    from services.ora_chat import cost_tracker as _ct
    monkeypatch.setattr(_ct, "log_call", _fake_log_call)

    usage = {"prompt_tokens": 100, "completion_tokens": 50}

    async with ledger.loop_call_context(loop_id="loop-f3", phase_tag="execute", user_id="u1"):
        for label in ("council-a1", "council-a2", "council-a3", "ceo", "single-model"):
            async with ledger.agent_call_context(label):
                await ledger.log_llm_usage("anthropic/claude-sonnet-4.5", usage)

    routes = [c["route"] for c in captured]
    assert routes == [
        "loop.execute.council-a1",
        "loop.execute.council-a2",
        "loop.execute.council-a3",
        "loop.execute.ceo",
        "loop.execute.single-model",
    ]
    # All 5 rows are separable — not one blob.
    assert len(set(routes)) == 5


async def test_no_agent_label_falls_back_to_plain_phase_route(monkeypatch):
    """Plan-phase / non-Council calls (no agent_call_context wrap) keep
    the pre-F3 route shape — no behavior change for existing callers."""
    from services import loop_token_ledger as ledger
    from services.ora_chat import cost_tracker as _ct

    captured = []
    async def _fake_log_call(**kwargs):
        captured.append(kwargs)
        return 0.0
    monkeypatch.setattr(_ct, "log_call", _fake_log_call)

    async with ledger.loop_call_context(loop_id="loop-f3b", phase_tag="plan", user_id="u1"):
        await ledger.log_llm_usage("deepseek/deepseek-chat", {"prompt_tokens": 10, "completion_tokens": 5})

    assert captured[0]["route"] == "loop.plan"


async def test_agent_label_resets_after_context_exits(monkeypatch):
    """Nested agent_call_context must not leak into the NEXT call once
    it exits — each council member's label is scoped to its own call."""
    from services import loop_token_ledger as ledger
    from services.ora_chat import cost_tracker as _ct

    captured = []
    async def _fake_log_call(**kwargs):
        captured.append(kwargs)
        return 0.0
    monkeypatch.setattr(_ct, "log_call", _fake_log_call)

    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    async with ledger.loop_call_context(loop_id="loop-f3c", phase_tag="execute", user_id="u1"):
        async with ledger.agent_call_context("council-a1"):
            await ledger.log_llm_usage("m", usage)
        # Outside the agent context now — should NOT still say council-a1.
        await ledger.log_llm_usage("m", usage)

    assert captured[0]["route"] == "loop.execute.council-a1"
    assert captured[1]["route"] == "loop.execute"


async def test_ceo_decide_flags_error_code_when_all_votes_cost_capped():
    """F2/F3 integration — when every council vote failed specifically
    because of the cost cap (not a real LLM error), CEO.decide must
    surface error_code=COST_CAP_REACHED so loop_engine.py's additive
    pause-check can tell budget-exhausted apart from a real failure."""
    from core.parliament import CEO

    ceo = CEO()
    votes = [
        {"member": "A1-conservative", "output": "", "score": 0.0, "temp": 0.2, "error": "cost_cap_reached"},
        {"member": "A2-balanced",     "output": "", "score": 0.0, "temp": 0.5, "error": "cost_cap_reached"},
        {"member": "A3-aggressive",   "output": "", "score": 0.0, "temp": 0.9, "error": "cost_cap_reached"},
    ]
    decision = await ceo.decide(
        task="write a function", votes=votes,
        context={"council": "A", "user_id": "u1"},
    )
    assert decision["status"] == "manual_review"
    assert decision["error_code"] == "COST_CAP_REACHED"
    assert "budget" in decision["reasoning"].lower()


async def test_ceo_decide_generic_failure_has_no_cost_cap_error_code():
    """Regression guard — a REAL failure (not cost-cap) must NOT be
    mislabeled as a budget pause."""
    from core.parliament import CEO

    ceo = CEO()
    votes = [
        {"member": "A1-conservative", "output": "", "score": 0.0, "temp": 0.2, "error": "timeout"},
        {"member": "A2-balanced",     "output": "", "score": 0.0, "temp": 0.5, "error": "empty"},
    ]
    decision = await ceo.decide(
        task="write a function", votes=votes,
        context={"council": "A", "user_id": "u1"},
    )
    assert decision["status"] == "manual_review"
    assert "error_code" not in decision
