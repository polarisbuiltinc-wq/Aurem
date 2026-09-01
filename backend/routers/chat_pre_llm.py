"""
routers/chat_pre_llm.py — 2026-09-06 chat.py refactor, Phase 1.

THE LOAD-BEARING DEDUP (founder's explicit north star for this
refactor round): `chat_send` and `chat_stream` used to each
hand-maintain their OWN copy of the exact sequence that makes
"confirm is a deterministic state transition, not a model turn"
true — the confirm-boundary check, intent classification, the
upgrade-offer short-circuit, the self-bug short-circuit, and the
casual/clarify direct-reply-or-honest-no-op branch. Every new
confirm-context added to one endpoint over the last several rounds
needed a matching edit in the other — and was missed at least once
(that's the root cause of the 4x-reproduced confirm bug).
`resolve_pre_llm()` is now the ONLY place this sequence is
implemented; both endpoints call it identically (see
routers/chat.py — exactly 2 call sites, guarded by
test_chat_pre_llm_dedup_2026_09_06.py::test_dedup_single_call_site_
each_endpoint).

Deliberate, tested behavior change (founder-authorized for Phase 1 —
see the S11 instruction allowing "the correct superset" unification):
the 4 short-circuit result dicts this function can produce
(upgrade-offer / self-bug-reply / no-pending-fix / casual-direct-
reply) are now built with ONE unified shape — the union of every key
either endpoint's own copy of that dict used — instead of chat_send's
narrower shape and chat_stream's differently-narrower shape. Every
key either endpoint's downstream code reads is accessed via
`dict.get(...)` (confirmed by source read before making this change),
so a superset never causes a KeyError; it only ever ADDS previously-
missing keys. See test_chat_pre_llm_dedup_2026_09_06.py::
test_unified_short_circuit_shape_* for the guard tests. Confirm-
boundary results (services/commit_boundary.py /
services/actions/pending_action.py) were already a single shared
shape before this round and are untouched here.

Everything else (which check fires, in what order, under which
`ora_panel` / tier / prior_fix_signal condition) is a byte-identical
port of each endpoint's PRE-EXISTING logic — including 3 real,
pre-existing asymmetries this refactor intentionally PRESERVES
rather than silently "fixing" (S8 — report, don't fix, outside what
was explicitly authorized):

  1. chat_send calls the confirm-boundary (`resolve_turn_start`)
     unconditionally; chat_stream only when `not ora_panel` — moot in
     practice for chat_stream, since its `ora_panel=True` branch
     already returns earlier via the Ask-Advisor cascade, so this
     was always dead code there either way. Modeled here via the
     `run_confirm_boundary` parameter (chat_send always passes
     `True`; chat_stream passes `not body.ora_panel`).

  2. chat_send's `core.intent_gateway.classify()` call omits
     `db`/`user_id`/`project_id` (no analytics log write); chat_
     stream's includes them. Neither affects the returned tier/
     confidence — those kwargs are Mongo-logging-only (see
     core/intent_gateway.py:600-615) — so this is a silent
     analytics-coverage gap for chat_send, not a decision-affecting
     bug. Preserved via the `log_ctx` parameter (`None` for
     chat_send, populated for chat_stream) rather than silently
     "fixing" the gap by always logging.

  3. chat_stream strips a leading `LOOP_PHASE:...\\n` marker from the
     text handed to `classify()` (Loop Mode turns); chat_send does
     not. Preserved via the caller-supplied `intent_probe_text`
     parameter (defaults to the raw prompt when omitted).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PreLLMOutcome:
    result: Optional[dict]
    intent_result: dict
    tier: str
    mode: str


def _shape_short_circuit(
    content: str, provider: str, *, intent_result: dict, tier: str,
    fallback_chain: list, iterations: int = 0, tool_calls_run: int = 0,
) -> dict:
    """The ONE unified shape for every short-circuit result this
    module produces — see module docstring's "deliberate, tested
    behavior change" note."""
    return {
        "ok": True,
        "content": content,
        "provider": provider,
        "fallback_chain": fallback_chain,
        "iterations": iterations,
        "tool_calls_run": tool_calls_run,
        "tool_invocations": [],
        "meta": {},
        "council": None,
        "task_type": None,
        "findings_saved_this_turn": [],
        "intent": intent_result,
        "tier": tier,
        "mode": "chat",
    }


async def resolve_pre_llm(
    *, db, user: dict, body, bin_ctx,
    prior_fix_signal: bool, prior_turn_text: str, session_summary: str,
    allowed_modes: list, req_mode: str,
    run_confirm_boundary: bool = True,
    ora_panel: bool = False,
    intent_probe_text: Optional[str] = None,
    log_ctx: Optional[dict] = None,
) -> PreLLMOutcome:
    """The pre-LLM boundary — checked BEFORE any tool-calling LLM
    call, for both /chat/send and /chat/stream identically.

    Returns `result=None` when the caller should proceed to its own
    `chat_with_tools(...)` call (using `outcome.mode`/`outcome.tier`);
    returns a non-None `result` when a deterministic short-circuit
    fired and the caller should use THAT as the turn's result
    instead — and must NEVER hand this turn to the model."""
    result = None

    # 1) Confirm-boundary — S5: a confirm/cancel resolving a real
    # pending action is a deterministic server-side state transition,
    # executed here, NEVER re-sent to the model for re-generation.
    # See services/commit_boundary.py + services/actions/
    # pending_action.py for the full state machine this delegates to.
    if run_confirm_boundary:
        from services.commit_boundary import resolve_turn_start
        result = await resolve_turn_start(
            db, user=user, session_id=body.session_id, project_id=body.project_id,
            prompt=body.prompt or "", bin_ctx=bin_ctx,
        )

    # 2) Intent classification — always runs (even when (1) already
    # set `result`) so the caller always has a real `tier`/
    # `intent_result` to stamp on the final response, exactly as both
    # endpoints already did independently before this refactor.
    from core.intent_gateway import classify as _classify_intent
    _classify_kwargs = dict(history=[], pending_fix=prior_fix_signal)
    if log_ctx:
        _classify_kwargs.update(log_ctx)
    intent_result = await _classify_intent(
        intent_probe_text if intent_probe_text is not None else (body.prompt or ""),
        **_classify_kwargs,
    )
    tier = intent_result.get("tier") or "agentic"

    # 3) Tier-gated model-mode resolution + the Root-4 honest upgrade
    # offer (see services/mode_routing.py). Runs even when `result`
    # is already set (matches both endpoints' pre-existing
    # `if result is None and ...` guards) so `mode` is always
    # correctly resolved for the caller's later cost-logging etc.
    from services.mode_routing import (
        resolve_model_mode, needs_edit_upgrade_offer, UPGRADE_OFFER_MESSAGE,
    )
    account_has_pro = "pro" in allowed_modes
    needs_upgrade_offer = needs_edit_upgrade_offer(tier, req_mode, account_has_pro=account_has_pro)
    mode = resolve_model_mode(tier, req_mode, account_has_pro=account_has_pro)

    if result is None and needs_upgrade_offer and not ora_panel:
        result = _shape_short_circuit(
            UPGRADE_OFFER_MESSAGE, "edit-tier-upgrade-offer",
            intent_result=intent_result, tier=tier,
            fallback_chain=["edit_tier_upgrade_offer"],
        )

    # 4) P7-D self-bug short-circuit — the user reporting ORA's OWN
    # UI/reply/panel as broken short-circuits before tier routing,
    # deterministic + zero LLM spend.
    if result is None and not ora_panel:
        from services.user_report_classifier import is_user_reporting_ora_bug
        if is_user_reporting_ora_bug(body.prompt or ""):
            from services.self_bug import emit as _emit_self_bug
            from services.self_bug_reply_guard import compose_self_bug_reply
            await _emit_self_bug(
                "user_reported", (body.prompt or "")[:300],
                {"session_id": body.session_id, "user_id": user.get("user_id")},
                source="user_report_classifier",
            )
            result = _shape_short_circuit(
                compose_self_bug_reply("user_reported"), "self-bug-reply",
                intent_result=intent_result, tier=tier,
                fallback_chain=["self_bug_user_reported"],
            )

    # 5) casual/clarify — a bare confirmation with NOTHING pending
    # never hits the free-form casual LLM (P0 Task 2 — it could
    # improvise a false "Approved!"/"Shipped!"); otherwise a direct,
    # no-tools LLM reply, falling through to the caller's own
    # chat_with_tools() on any failure (never blank-screens).
    if result is None and tier in ("casual", "clarify") and not ora_panel:
        from services.response_confidence import is_confirmation_reply, NO_PENDING_FIX_MESSAGE
        if is_confirmation_reply(body.prompt or "") and not prior_fix_signal:
            result = _shape_short_circuit(
                NO_PENDING_FIX_MESSAGE, "intent-gateway-no-pending-fix",
                intent_result=intent_result, tier=tier,
                fallback_chain=["intent_casual_no_pending_fix"],
            )
        else:
            try:
                from services.intent_gateway_casual_reply import casual_direct_reply
                from services.response_confidence import apply_no_false_success_guard
                from services.business_voice_filter import apply_business_owner_guards
                _casual_reply_text = await casual_direct_reply(
                    body.prompt, prior_assistant_text=prior_turn_text,
                    session_summary=session_summary,
                )
                _casual_reply_text = apply_no_false_success_guard(
                    body.prompt or "", _casual_reply_text, prior_fix_signal,
                )
                _casual_reply_text = await apply_business_owner_guards(
                    ora_panel, _casual_reply_text, body.prompt or "",
                    session_id=body.session_id, user_id=user.get("user_id"),
                )
                result = _shape_short_circuit(
                    _casual_reply_text, "intent-gateway-casual",
                    intent_result=intent_result, tier=tier,
                    fallback_chain=["intent_casual"], iterations=1,
                )
            except Exception as _ce:                      # noqa: BLE001
                logger.warning(
                    "intent_gateway %s path failed (%r) — falling through "
                    "to chat_with_tools", tier, _ce,
                )

    return PreLLMOutcome(result=result, intent_result=intent_result, tier=tier, mode=mode)
