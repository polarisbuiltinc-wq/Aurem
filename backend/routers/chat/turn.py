"""
routers/chat/turn.py — POST /chat/send (one non-streaming turn).
Split from the former routers/chat.py god-file (4184 lines) on
2026-09-08. See routers/chat/__init__.py for the split rationale.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, validator

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.orchestrator import chat_with_tools
from services.llm import call_llm_with_meta, call_emergent_watchdog, cap_for
from services.repo_context import get_repo_context
from services.usage import is_founder_email  # Iter 212m-169 — BINContext role check
from core.task_type import infer_task_type as _infer_task_type  # Iter 212m-177/178 P0-3
from services.chat_helpers import (
    _detect_mode, _deduct_tokens,
    is_fix_confirmation, _safe_provenance, detect_prompt_injection,
    _f12_has_real_signal, _is_transient_proxy_error, _TRANSIENT_PROXY_CODES,
    classify_intent,
    _TITLE_SYSTEM, _generate_title, _maybe_set_title,
    _regenerate_without_recall, _strip_council_block,
    _persist_turn,
    _build_failed_followup, _build_blocked_followup, _build_done_fallback,
    _FOLLOWUP_SYS, _generate_done_followup,
    retrieved_context_for_grounding, apply_output_guards,
)

from . import router
from .misc import PLAIN_ENGLISH_EXPLAIN_CONTRACT, BUSINESS_OWNER_VOICE_CONTRACT

logger = logging.getLogger(__name__)


class ChatBody(BaseModel):
    # Iter 44 — bounded length to prevent prompt-bomb DoS + match
    # downstream cap_for() context windows.
    prompt: str = Field(..., min_length=1, max_length=20000)
    session_id: Optional[str] = Field(None, max_length=128)
    max_tool_iters: int = Field(4, ge=0, le=12)
    maxx_mode: bool = False
    project_id: Optional[str] = Field(None, max_length=128)
    # Iter 38: agent selector. "auto" routes via existing model-routing
    # logic in orchestrator.py (DeepSeek/Claude). "ora" calls the founder's
    # own aurem.live ORA endpoint. Other values currently fall through to
    # "auto" so adding new agents later is backwards-compatible.
    agent: Optional[str] = Field("auto", max_length=32)
    # Iter 153 — review mode requested by the user (swift/pro/maxx).
    # Server clamps to whatever their tier allows; never trusted as-is.
    mode: Optional[str] = Field("swift", max_length=16)
    # Iter 212m-58 — execution_mode is ORTHOGONAL to `mode` (the model
    # selector). "prompt" = single-pass one-shot reply (default).
    # "loop" = 5-phase pipeline (Plan → Execute → Verify → Security →
    # Ship). The backend doesn't drive the loop itself — the frontend
    # orchestrates phase transitions via successive /chat/stream calls.
    # Our job here is to inject a system-prompt suffix telling the
    # model to (a) respond plan-only on the first turn and (b) emit
    # explicit `[STEP X/5: name]` markers at every phase boundary so
    # the LoopStepBar can light up.
    execution_mode: Optional[str] = Field("prompt", max_length=16)
    # Iter 159 — true when the request originates from the ASK ORA
    # side panel. Triggers the casual ASK-ORA tone override in the
    # system prompt for this turn only; the main coding chat never
    # sets this so its persona stays untouched.
    ora_panel: bool = False
    # Iter 212m-212 — Client-side screenshot attached to an advisor
    # turn.  Base64-encoded PNG bytes captured by the frontend via
    # `html2canvas`.  ONLY consumed when `ora_panel=true`; the main
    # coding chat never reads this.  Vision analysis is best-effort
    # and isolated (services/advisor_vision.py) — failure never
    # blocks the text response.
    #
    # We cap the raw base64 string at ~10 MB (roughly 7.5 MB decoded)
    # to prevent OOM.  Frontend downscales to 1280×720 before send,
    # so a typical capture is 200–600 KB base64.
    screenshot_b64: Optional[str] = Field(None, max_length=10 * 1024 * 1024)
    # Iter 42: structured payload of browser console/network/stack errors
    # captured by frontend/public/F12ErrorCapture.js. When present (and has
    # any errors), the request is auto-classified as Mode D (debug).
    f12_payload: Optional[dict] = None
    # Iter 212m-164 — optional task_type override for the council
    # TaskRouter (core/_TASK_TYPE_TO_COUNCIL in the council hub).
    # Letting the caller pin the council unlocks the V2 LLM swaps
    # for analysis (Council B → GLM-5.2) and writing (Council C →
    # DeepSeek) tasks without waiting for the post-launch
    # council-direct endpoint.  Accepted values mirror the router
    # map exactly; an unrecognised string falls through to the
    # existing keyword-based routing (safe — never escalates the
    # council).
    task_type: Optional[str] = Field(None, max_length=32)

    @validator("prompt")
    def _strip_prompt(cls, v: str) -> str:
        return (v or "").strip()

    @validator("task_type")
    def _validate_task_type(cls, v):
        # Iter 212m-164 — whitelist to the 12 router keys.  Anything
        # else is silently dropped to None so a typo doesn't accidentally
        # change routing semantics.
        if not v:
            return None
        ok = {
            "code_fix", "code_review", "security", "lint_heal",
            "analysis", "report", "insight", "summarize",
            "email", "copy", "write", "draft",
        }
        return v if v in ok else None


@router.post("/send")
async def chat_send(
    body: ChatBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Non-streaming chat — returns full response, persists turn.
    If maxx_mode=True, runs Emergent watchdog review after DeepSeek reply."""
    user = await current_dev(authorization)
    # Iter 364 · Phase 3 · Token hard-stop enforcement.
    # BEFORE any LLM provider call, refuse the request if the user has
    # exhausted their effective token budget OR blown their monthly
    # task cap. Founders / unlimited accounts short-circuit inside
    # assert_has_budget so this is a no-op for them. Doing this at the
    # very top of the handler ensures we NEVER pay OpenRouter/DeepSeek/
    # Claude for a request we then reject. Iter 364 RCA:
    #   Old code did an atomic $max: [0, remaining - cost] clamp AFTER
    #   the LLM call — wallet floored at zero but chat kept working,
    #   silently burning tokens with no server-side stop. The banner
    #   at 100% was purely cosmetic. This is the fix.
    from services.usage import assert_has_budget, assert_has_task_budget
    await assert_has_budget(user["user_id"])
    await assert_has_task_budget(user["user_id"])
    jwt_token = authorization.split(" ", 1)[1] if authorization else ""
    # Iter 212m-15 — parallelise the two pre-flight context fetches AND
    # log the cumulative timing for each stage so the next time a
    # founder reports "even 'hi' takes 20s", we can pinpoint whether
    # the cost is in repo_ctx, url_ctx, LLM dispatch, or persist. The
    # chat_stream endpoint was parallelised in iter 157 — chat_send
    # was left sequential, which is why founder's first message hit
    # 20s on prod (testing agent finding iter 212m-14).
    t_start = time.time()
    # Iter 212m-169/170 — ORAContext hardening.  Build the request-
    # scoped ORAContext at the entry point when the user is chatting
    # AGAINST a project.  Home-page casual chat (no project) still
    # works with bin_ctx=None; downstream repo tools will refuse cleanly.
    pid = (body.project_id or "").strip()
    _db = get_db()
    bin_ctx = None
    if pid and pid != "home":
        # build_ora_context does ALL of:
        #   • ownership check (find_one {project_id, user_id})     → 403
        #   • repo_owner / repo_name / branch pull                 → 400
        #   • PAT decrypt via services/vault HKDF                  → 403
        #   • OAuth fallback for legacy OAuth-only projects        → 403
        #   • wraps into ORAContext with ora_boundary_active=True  (170)
        from services.ora_context import build_ora_context
        bin_ctx = await build_ora_context(
            user_id=user["user_id"],
            project_id=pid,
            db=_db,
            is_founder=bool(
                user.get("is_admin") or user.get("is_unlimited")
                or (user.get("tier") == "founder")
            ),
        )
        try:
            repo_ctx = await asyncio.wait_for(
                get_repo_context(user["user_id"], pid),
                timeout=12.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "get_repo_context exceeded 12s for pid=%r user=%r — "
                "degrading to empty repo_ctx",
                pid, user["user_id"],
            )
            repo_ctx = ""
    else:
        repo_ctx = ""
    # 2026-08-30 — Issue C fix: single-surface-drift. /chat/send never
    # fetched project_brain context at all (chat_stream's equivalent
    # block below, ~line 1402, does) — confirmed via source read, not
    # a guess. Same brain-context fetch, mirrored for this surface.
    async def _safe_send(coro, label, timeout_s=12.0):
        try:
            return await asyncio.wait_for(coro, timeout=timeout_s)
        except Exception as e:
            logger.warning("chat_send-context: %s failed/timed out (%r) — degrading", label, e)
            return ""

    brain_ctx = ""
    if pid and pid != "home":
        try:
            _proj = await asyncio.wait_for(_db.cto_projects.find_one(
                {"project_id": pid, "user_id": user["user_id"]},
                {"_id": 0, "github_owner": 1, "github_repo": 1,
                 "github_token": 1, "auth_method": 1,
                 "installation_id": 1, "user_id": 1},
            ), timeout=10.0)
            owner = (_proj or {}).get("github_owner") or ""
            repo = (_proj or {}).get("github_repo") or ""
            repo_full = f"{owner}/{repo}" if owner and repo else pid
            from services.project_brain import get_brain_context
            _pat = None
            try:
                from services.pat_vault import get_repo_token_or_error
                async def _pat_lookup():
                    t, _e, _d = await get_repo_token_or_error(_proj or {})
                    return t
                _pat = await asyncio.wait_for(_pat_lookup(), timeout=10.0)
            except Exception:
                _pat = None
            brain_ctx = await _safe_send(
                get_brain_context(_db, pid, repo_full, github_token=_pat),
                "brain_context",
            )
            if brain_ctx:
                brain_ctx = "[PROJECT MEMORY]\n" + brain_ctx
        except Exception:
            logger.exception("chat_send: brain context fetch failed (continuing)")
            brain_ctx = ""
    # Iter 212m-23 — URL context is NO LONGER eagerly stuffed here.
    # The orchestrator force-invokes the `fetch_url` tool when the
    # prompt contains an http(s) URL, which surfaces a proper step
    # card + web_sources chip in the UI and logs into tool_invocations.
    t_preflight = time.time()
    extra_sys = "\n\n".join(s for s in (repo_ctx, brain_ctx) if s)
    # Iter 212m-77/78 — ORA Council self-learning ACTIVATED.
    # Returns (block, recalled_count). Surface count back to caller so
    # the FE can render "📚 ORA recalled N similar past answers".
    _council_recalled = 0
    _council_block = ""
    _recall_mode_send = None
    try:
        from cto_services.db import get_db as _get_db
        from services.ora_council_retriever import get_council_few_shot
        _db_ref = _get_db()
        if _db_ref is not None:
            # 2026-08-27 · mode-taxonomy fix — get_council_few_shot's
            # candidate index is keyed by the real council modes
            # ("A"/"B"/"D"/"E", from classify_intent()); _detect_mode()
            # only ever returns "code"/"chat", which is NEVER a key in
            # that index — so this call was returning 0 candidates on
            # every real request, unconditionally. classify_intent()
            # is a cheap heuristic (no LLM call), safe here.
            _recall_mode_send = classify_intent(body.prompt or "", body.f12_payload)
            _council_block, _council_recalled = await get_council_few_shot(
                _db_ref, body.prompt or "",
                mode=_recall_mode_send,
                user_id=user.get("user_id"),
                project_id=body.project_id,
                k=2,
            )
            if _council_block:
                extra_sys = (_council_block
                             + ("\n\n" + extra_sys if extra_sys else ""))
    except Exception as _cre:
        logger.debug("council retrieval skipped (chat/send): %r", _cre)

    # 2026-08-27 · Plain-English Output Contract (Phase 1, flag-gated).
    # Only for explain/advisory turns (council mode "A") on the main
    # chat surface (never Ask Advisor, never a mutation-shaped B/C/D/E/F
    # turn) — see PLAIN_ENGLISH_EXPLAIN_CONTRACT for the full rule set.
    _plain_english_active = False
    try:
        if not body.ora_panel and _recall_mode_send == "A":
            from services.feature_flags import is_enabled as _pee_enabled
            if await _pee_enabled(
                "explain_plain_english_v1",
                user_id=user.get("user_id"), tier=user.get("tier"),
            ):
                extra_sys = (extra_sys + "\n\n" + PLAIN_ENGLISH_EXPLAIN_CONTRACT).strip()
                _plain_english_active = True
    except Exception as _pee_exc:
        logger.debug("plain_english_contract skipped (chat/send): %r", _pee_exc)

    # 2026-08-31 · Business-Owner Voice Contract — every non-ora_panel
    # turn, not flag-gated (see BUSINESS_OWNER_VOICE_CONTRACT docstring).
    if not body.ora_panel:
        extra_sys = (extra_sys + "\n\n" + BUSINESS_OWNER_VOICE_CONTRACT).strip()
    # Iter 212m-24 — Admin House Rules (HIGHEST PRIORITY).
    # If the admin has enabled house rules for `chat` + the requested
    # mode, prepend the rules block at the very top of extra_sys so it
    # arrives BEFORE the orchestrator's persona stack and OVERRIDES
    # every other instruction the model sees.
    try:
        from services.house_rules import (
            get_active_house_rules, format_house_rules_block,
            get_active_chat_prompt,
        )
        _hr_prompt = await get_active_house_rules(
            "chat", (body.mode or "swift").lower(),
        )
        if _hr_prompt:
            extra_sys = (
                format_house_rules_block(_hr_prompt)
                + ("\n\n" + extra_sys if extra_sys else "")
            )
        # Iter 212m-171 — dedicated CHAT prompt slot (independent of the
        # combined `prompt` field).  Injected AFTER the boundary block
        # applied by the orchestrator, before the AUREM persona.
        _chat_extra = await get_active_chat_prompt()
        if _chat_extra:
            extra_sys = (
                "=== ADMIN CHAT PROMPT (Iter 212m-171) ===\n"
                f"{_chat_extra}\n"
                "=== END ADMIN CHAT PROMPT ===\n\n"
                + (extra_sys or "")
            )
    except Exception as _hre:
        logger.debug("house_rules injection skipped (chat/send): %r", _hre)
    # Iter 153 — clamp the requested review mode to whatever the user's
    # tier allows. Falls back to the BEST mode they have access to so
    # the request never errors out from a missing entitlement.
    from services.subscription_tiers import allowed_modes_for_tier
    _allowed = allowed_modes_for_tier((user or {}).get("tier") or "free")
    req_mode = body.mode if (body.mode in _allowed) else _allowed[-1]
    # Iter 364 · Phase-3 — Maxx daily cap. Even Team tier users are
    # capped at MAXX_DAILY_TASK_CAP (default 10) Maxx-mode invocations
    # per rolling 24h. Prevents a single user from burning $200+ of
    # DeepSeek+Claude cost in a single day. Applies to both the legacy
    # `maxx_mode=True` boolean and the new `mode="maxx"` pill.
    _wants_maxx = bool(body.maxx_mode) or (req_mode == "maxx")
    if _wants_maxx:
        from services.loop_beta import assert_maxx_daily_budget
        await assert_maxx_daily_budget(_db, user["user_id"])
    # Iter 212m-168 — surface founder/admin role to the orchestrator so
    # only these accounts see `execute_bash` (local pod filesystem).
    from services.usage import is_founder_email as _is_fnd_email
    _is_fnd = bool(
        user.get("is_admin") or user.get("is_unlimited")
        or (user.get("tier") == "founder")
        or _is_fnd_email(user.get("email"))
    )
    # 2026-09-06 · Phase 1 chat.py refactor (the "load-bearing dedup") —
    # this ONE call replaces what used to be 5 independently
    # hand-copied pre-LLM checks (confirm-boundary / intent-classify /
    # upgrade-offer / self-bug / casual-direct-reply-or-honest-no-op).
    # chat_stream calls the exact same function below — see
    # routers/chat_pre_llm.py's module docstring for the full
    # rationale and the 3 pre-existing, intentionally-preserved
    # asymmetries this refactor does NOT silently fix.
    from services.response_confidence import (
        prior_turn_had_fix_signal as _ptfs_send,
        prior_turn_context_text as _ptct_send,
        get_session_summary as _gss_send,
    )
    _prior_fix_signal = await _ptfs_send(_db, body.session_id, user["user_id"])
    _prior_turn_text = await _ptct_send(_db, body.session_id, user["user_id"])
    _session_summary = await _gss_send(_db, body.session_id, user["user_id"])
    from routers.chat_pre_llm import resolve_pre_llm
    _pre = await resolve_pre_llm(
        db=_db, user=user, body=body, bin_ctx=bin_ctx,
        prior_fix_signal=_prior_fix_signal, prior_turn_text=_prior_turn_text,
        session_summary=_session_summary,
        allowed_modes=_allowed, req_mode=req_mode,
        ora_panel=bool(body.ora_panel),
    )
    result = _pre.result
    _intent_result = _pre.intent_result
    _tier = _pre.tier
    req_mode = _pre.mode
    if result is None:
        _max_iters_eff = 3 if _tier == "query" else min(body.max_tool_iters, 4)
        # 2026-09-04 — FIX for the "promise-then-silence" hang
        # (diagnosis in services/local_tools.py's module-level
        # 2026-09-04 comment): unlike chat_stream (which has a real
        # 180s/48s watchdog via its worker+ticker+queue pattern),
        # chat_send had NO overarching wall-clock ceiling around this
        # call at all — if the orchestrator ever stalled (a slow LLM
        # round-trip, a CPU-starved to_thread call, anything), this
        # endpoint would hang with zero server-side protection,
        # indefinitely, for any request type on any mode (this is the
        # plain JSON endpoint, not stream-specific). Reuses the exact
        # same CHAT_HARD_TIMEOUT_S env var and build_timeout_message
        # helper chat_stream already uses, for a consistent, honest
        # timeout reply instead of an unbounded hang.
        _hard_timeout_s = float(os.getenv("CHAT_HARD_TIMEOUT_S", "180"))
        try:
            result = await asyncio.wait_for(
                chat_with_tools(
                    prompt=body.prompt,
                    jwt_token=jwt_token,
                    system=(extra_sys + "\n\n" if extra_sys else None),
                    max_iters=_max_iters_eff,
                    session_id=body.session_id,
                    mongo_client=None,
                    user_id=user["user_id"],
                    project_id=body.project_id,
                    mode=req_mode,
                    task_type=body.task_type or _infer_task_type(body.prompt),
                    is_founder=_is_fnd,
                    bin_ctx=bin_ctx,
                ),
                timeout=_hard_timeout_s,
            )
        except asyncio.TimeoutError:
            from services.orchestrator import build_timeout_message
            _timeout_content, _slow_api = build_timeout_message(
                0, _hard_timeout_s, "",
            )
            logger.warning(
                "chat_send hard-timeout fired after %.0fs — see "
                "local_tools.py 2026-09-04 comment for the orphan-"
                "process diagnosis this guards against",
                _hard_timeout_s,
            )
            result = {
                "ok":               True,
                "content":          _timeout_content,
                "provider":         "aurem-timeout-guard",
                "iterations":       0,
                "tool_calls_run":   0,
                "meta":             {"timed_out": True, "slow_api": _slow_api},
                "council":          None,
                "task_type":        None,
                "findings_saved_this_turn": [],
            }
    if isinstance(result, dict):
        # Pre-existing gap fix (2026-08-31, confirmed via
        # test_intent_gateway_casual_boundary_2026_01.py) — chat_stream
        # always stamps result["tier"]/["intent"] (see ~line 3010); the
        # sync /chat/send endpoint never did, for ANY branch, so every
        # response silently lost tier/intent info. Mirrors chat_stream's
        # own convention exactly.
        result.setdefault("intent", _intent_result)
        result.setdefault("tier", _tier)
    t_llm = time.time()
    content = result.get("content", "") or ""
    provider = result.get("provider", "") or ""
    mode = _detect_mode(body.prompt)
    from services.llm import temperature_for
    temperature = temperature_for(mode)

    # 2026-08-21 — cold-start / recall-mismatch mitigation. See
    # services/response_confidence.py. A response that proposes an
    # unsolicited code-ship for a message with zero fix/bug intent is
    # swapped for a friendly fallback BEFORE the user ever sees it —
    # this also strips the aurem-handoff fence so Approve the fix can
    # never render for it. 2026-08-22 — hardened with a quiet auto-
    # retry (layer d) and verbose real-log observation (founder ask)
    # before falling back to the canned message.
    _low_confidence = False
    _ship_suppressed = False
    _bail_reason = None
    try:
        from services.response_confidence import (
            response_seems_mismatched, has_ship_suggestion, FALLBACK_MESSAGE,
        )
        from services.bail_reason import classify_bail
        _mismatch = response_seems_mismatched(body.prompt or "", content, _prior_fix_signal)
        logger.info(
            "chat.confidence_check surface=chat_send turn=1 prompt=%r "
            "council_recalled=%s mismatch=%s content_preview=%r",
            (body.prompt or "")[:160], _council_recalled, _mismatch,
            (content or "")[:220],
        )
        from services.response_confidence import persist_confidence_check
        await persist_confidence_check(
            get_db(), surface="chat_send", turn=1,
            prompt_preview=(body.prompt or "")[:160],
            content_preview=(content or "")[:220],
            council_recalled=_council_recalled, mismatch=_mismatch,
            user_id=user.get("user_id"), session_id=body.session_id,
            project_id=body.project_id,
        )
        if _mismatch:
            logger.warning(
                "chat_send: mismatch detected on first response — retrying "
                "once without the ORA-Council recall block before showing "
                "anything to the user",
            )
            _retry_content, _retry_provider = await _regenerate_without_recall(
                prompt=body.prompt, jwt_token=jwt_token,
                extra_sys_no_council=_strip_council_block(extra_sys, _council_block),
                max_iters=min(body.max_tool_iters, 4),
                session_id=body.session_id, user_id=user["user_id"],
                project_id=body.project_id, mode=req_mode,
                task_type=body.task_type or _infer_task_type(body.prompt),
                is_founder=_is_fnd, bin_ctx=bin_ctx,
            )
            _retry_mismatch = response_seems_mismatched(body.prompt or "", _retry_content, _prior_fix_signal)
            logger.info(
                "chat.confidence_check surface=chat_send turn=2(retry) "
                "prompt=%r mismatch=%s content_preview=%r",
                (body.prompt or "")[:160], _retry_mismatch,
                (_retry_content or "")[:220],
            )
            await persist_confidence_check(
                get_db(), surface="chat_send", turn=2,
                prompt_preview=(body.prompt or "")[:160],
                content_preview=(_retry_content or "")[:220],
                mismatch=_retry_mismatch,
                user_id=user.get("user_id"), session_id=body.session_id,
                project_id=body.project_id,
            )
            if _retry_content.strip() and not _retry_mismatch:
                content = _retry_content
                provider = _retry_provider or provider
                logger.info(
                    "chat_send: retry resolved the mismatch — user never "
                    "saw the bad first draft",
                )
            else:
                _ship_suppressed = (
                    has_ship_suggestion(content) or has_ship_suggestion(_retry_content)
                )
                # R2 (2026-08-31) — never the useless generic
                # FALLBACK_MESSAGE ("try rephrasing, or ask again") to a
                # business owner. classify_bail() is deterministic (no
                # LLM) and always returns a CONCRETE next step:
                # missing_data -> ask for that value in plain words,
                # out_of_scope -> say what ORA can do instead,
                # low_confidence -> ONE specific clarifying question.
                _bail = classify_bail(body.prompt or "")
                content = _bail["message"]
                _bail_reason = _bail["reason"]
                _low_confidence = True
                logger.warning(
                    "chat_send: retry ALSO mismatched (or came back empty) "
                    "— showing reason-carrying bail (reason=%s), never the "
                    "generic 'try rephrasing' fallback", _bail_reason,
                )
    except Exception as _rce:
        logger.debug("response_confidence gate skipped (chat_send): %r", _rce)

    # 2026-08-28 · NEW P0 Task 2 — final defense-in-depth: no reply to
    # a bare confirmation may claim a ship/approve action already
    # happened unless it also carries a real aurem-handoff fence.
    # 2026-09-02/03 — plus the no-edit-deadend, no-orphan-confirm, and
    # fabricated-content guards. 2026-09-04 — consolidated into ONE
    # shared helper (chat_helpers.apply_output_guards); `skip=True`
    # when this turn's result came from confirm_execution's
    # deterministic real-execution path (see that module's docstring
    # for why the false-success guard's premise no longer applies
    # there).
    content = apply_output_guards(
        body.prompt or "", content, _prior_fix_signal,
        retrieved_context_for_grounding(extra_sys, result),
        skip=bool(result.get("_skip_output_guards")),
        tool_calls_run=result.get("tool_calls_run", 0) if isinstance(result, dict) else 0,
    )

    # 2026-08-27 · Output Guard (Phase 1 net, "Show the Outcome, Never
    # the Engine"). Runs AFTER the mismatch/retry/fallback resolution
    # above (nets whatever text is actually about to be returned).
    # Never touches ship/confirm content.
    #
    # P5 (Journey/Intent-Grounding build round) — leak-STRIPPING now
    # runs for EVERY user, not just the plain_english_contract_active
    # allowlist (founder call: these are real bugs — raw booleans,
    # internal jargon like "Iter 286"/"Mode D" — not part of the
    # explain-mode verbosity/tone enhancement). Length-CAPPING (the
    # re-summarize-if-too-long net) stays flag-gated — that's a real
    # behavior/tone change, not a leak fix.
    _leak_stripped = False
    _length_capped = False
    _output_guard_ref_id = None
    if content and "aurem-handoff" not in content:
        try:
            from services.output_guard import strip_machinery_leak, enforce_length_cap, extract_named_files
            from core.errors import new_ref_id
            # universal_only=True unless the explain-mode contract is
            # active this turn — otherwise the explain-only tier (file
            # paths, DB collection names, framework jargon) would strip
            # legitimate scan/dev content for every user (the P6 regression).
            # M3 (2026-08-30) — a file the user named THIS turn (e.g. "ship
            # a fix to README.md") is exempted from the bare-file-path
            # redaction; every other path is still redacted, unchanged.
            content, _leak_stripped = strip_machinery_leak(
                content, universal_only=not _plain_english_active,
                user_named_files=extract_named_files(body.prompt),
            )
            if _plain_english_active:
                content, _length_capped = await enforce_length_cap(content)
            if _leak_stripped or _length_capped:
                _output_guard_ref_id = new_ref_id()
        except Exception as _og_exc:
            logger.debug("output_guard skipped (chat/send): %r", _og_exc)

    # R1 (2026-08-31) — business-owner voice filter, applied LAST (after
    # every other content-mutating guard, INCLUDING output_guard above)
    # so nothing downstream can re-introduce a raw filename/dev term.
    # Same "aurem-handoff" exemption as output_guard just above: that
    # fence is structured ship-pipeline machinery the frontend parses
    # for the real file path (Approve/ShipDialog) — NOT prose. Rewriting
    # tokens inside it would risk breaking the exact ship flow this
    # rework must not touch (see R5a — the K1 approve-button history).
    if content and "aurem-handoff" not in content:
        try:
            from services.business_voice_filter import apply_business_owner_guards
            content = await apply_business_owner_guards(
                getattr(body, "ora_panel", False), content, body.prompt or "",
                session_id=body.session_id, user_id=user["user_id"],
            )
        except Exception as _bvf_e:
            logger.debug("business_voice filter skipped (chat_send): %r", _bvf_e)

    # Maxx mode: watchdog review (only if we have non-empty content)
    # Iter 161 — same legacy-only gating as the streaming path: skip
    # when the new mode="maxx" pill is set because Claude already wrote
    # the code via use_code_model.
    watchdog = None
    is_new_maxx_pill = (body.mode or "").lower() == "maxx"
    if body.maxx_mode and content.strip() and not is_new_maxx_pill:
        watchdog = await call_emergent_watchdog(content)
        provider = (provider or "deepseek") + "+emergent-watchdog"
    # Iter 364 · Phase-3 — persist Maxx cost row. Best-effort accounting:
    # LLM meta usually surfaces per-provider $ estimates; if not, we
    # still log a 0-cost row so counts + daily-cap enforcement have
    # ground truth. Any legacy or new Maxx invocation lands here.
    if _wants_maxx and content.strip():
        try:
            from services.loop_beta import log_maxx_cost
            meta = result.get("meta") or {}
            deepseek_cost = float(meta.get("deepseek_cost_usd") or 0.0)
            claude_cost   = float(meta.get("claude_cost_usd")   or 0.0)
            await log_maxx_cost(
                _db,
                user_id=user["user_id"],
                loop_id=None,
                deepseek_cost_usd=deepseek_cost,
                claude_cost_usd=claude_cost,
                model_meta={
                    "provider":         provider,
                    "req_mode":         req_mode,
                    "maxx_mode_legacy": bool(body.maxx_mode),
                    "watchdog":         bool(watchdog),
                    "surface":          "chat_send",
                },
            )
        except Exception as _mce:
            logger.debug("maxx_cost_log write failed (chat_send): %r", _mce)

    await _persist_turn(user["user_id"], body.session_id or "",
                        body.prompt, content, provider, watchdog=watchdog,
                        project_id=body.project_id,
                        low_confidence=_low_confidence,
                        ship_suppressed=_ship_suppressed,
                        bin_ctx=bin_ctx)
    if body.session_id:
        asyncio.create_task(
            _maybe_set_title(user["user_id"], body.session_id, body.prompt)
        )
    tokens_remaining = await _deduct_tokens(user["user_id"], content)
    # 2026-08-19 P0 fix — this was the main customer chat path with
    # ZERO cost tracking anywhere (confirmed: 0 of 2,739 real turns in
    # a preview audit had a cost row). Best-effort, never blocks reply.
    try:
        from services.customer_cost_tracker import log_customer_chat_cost
        await log_customer_chat_cost(
            user_id=user["user_id"], session_id=body.session_id or "",
            project_id=body.project_id, route="chat_send",
            provider=provider, prompt_text=body.prompt,
            system_text=extra_sys or "", output_text=content,
        )
    except Exception as e:
        logger.warning("customer chat cost log skipped (chat_send): %r", e)
    # Iter 365 · Phase 3 — funnel event: first_chat_sent (idempotent
    # via one-shot flag on dev_users). Best-effort, non-blocking.
    try:
        from cto_services.db import get_db as _fn_db
        from services.signup_guards import emit_funnel_event
        _fdb = _fn_db()
        if _fdb is not None:
            _stamped = await _fdb.dev_users.find_one_and_update(
                {"user_id": user["user_id"], "first_chat_at": {"$exists": False}},
                {"$set": {"first_chat_at": time.time()}},
                projection={"_id": 0, "user_id": 1},
            )
            if _stamped:
                await emit_funnel_event(
                    _fdb, user_id=user["user_id"],
                    event_type="first_chat_sent",
                    metadata={"provider": provider, "mode": req_mode},
                )
    except Exception as _fne:
        logger.debug("first_chat_sent funnel emit failed: %r", _fne)
    # Iter 212m-15 — stage timing instrumentation. Lets us spot whether
    # a slow turn was the LLM (normal — 5-15s on cold OpenRouter), or
    # one of the cheap pre-flight steps stalling (which would be a
    # real bug). Format: chat_send.timing pre=<s> llm=<s> persist=<s>
    # total=<s> prompt_len=<n> project_id=<pid|none>
    try:
        t_done = time.time()
        logger.info(
            "chat_send.timing pre=%.3f llm=%.3f persist=%.3f total=%.3f "
            "prompt_len=%d project_id=%s",
            t_preflight - t_start,
            t_llm - t_preflight,
            t_done - t_llm,
            t_done - t_start,
            len(body.prompt or ""),
            pid or "none",
        )
    except Exception:
        pass
    return {
        "ok": result.get("ok", True),
        "content": content,
        "provider": provider,
        "watchdog": watchdog,
        "mode": mode,
        "temperature": temperature,
        "iterations": result.get("iterations", 0),
        "session_id": body.session_id,
        "user_id": user.get("user_id"),
        "tokens_remaining": tokens_remaining,
        "low_confidence": _low_confidence,
        "bail_reason": _bail_reason,
        "ship_suppressed": _ship_suppressed,
        # Iter 212m-78 — Council self-learning indicator. FE renders
        # "📚 ORA recalled N similar past answers" above the bubble
        # when this is > 0.
        "council_recalled": _council_recalled,
        # 2026-08-27 — true when the founder plain-English explanation
        # contract was injected this turn (flag-gated, explain-only).
        "plain_english_contract_active": _plain_english_active,
        # 2026-08-27 — Output Guard net results (Phase 1). See
        # services/output_guard.py.
        "leak_stripped": _leak_stripped,
        "length_capped": _length_capped,
        "output_guard_ref_id": _output_guard_ref_id,
        # Iter 212m-164 — surface the council letter + task_type that
        # drove this turn's LLM pick so callers can verify V2 routing
        # without scraping Mongo / Langfuse.
        "council":   result.get("council"),
        "task_type": result.get("task_type"),
        # 2026-08-31 — fix confirmed via
        # test_intent_gateway_casual_boundary_2026_01.py: chat_stream
        # always echoes tier/intent (see ~line 3011); chat_send never
        # did, for any branch, so callers could never verify routing.
        "tier":      result.get("tier", _tier),
        "intent":    result.get("intent", _intent_result),
        # Iter 212m-171 — Scope Badge echo.  FE stamps this on the
        # assistant bubble so the user sees which repo they were
        # scoped to.  Zero PII beyond the repo slug (already public
        # to the user).
        "repo_owner": getattr(bin_ctx, "repo_owner", None) if bin_ctx else None,
        "repo_name":  getattr(bin_ctx, "repo_name", None)  if bin_ctx else None,
        "branch":     getattr(bin_ctx, "branch", None)     if bin_ctx else None,
        "findings_saved": result.get("findings_saved_this_turn") or [],
        # Item 2 (2026-08-31) — palette nudges made this turn (before/after
        # swatches rendered by PaletteNudgeBubble.jsx, no jargon).
        "palette_nudges": result.get("palette_nudges") or [],
    }



