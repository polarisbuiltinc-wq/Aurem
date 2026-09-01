"""
routers/chat/stream.py — POST /chat/stream (the SSE chat loop).
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
from .misc import (
    PLAIN_ENGLISH_EXPLAIN_CONTRACT, BUSINESS_OWNER_VOICE_CONTRACT,
    ORA_PANEL_TONE, _HANDOFF_FENCE_RE, _SHELL_COMMAND_TOKENS,
)
from .turn import ChatBody
# Cohesion follow-up (2026-09-08) — moved into their own module; still
# imported by NAME here so `routers.chat.stream._maybe_guard_shell_
# handoff_followup` / `._handoff_brief_is_shell_command` patch targets
# (used throughout the test suite) keep resolving unchanged.
from .handoff_guard import (
    _handoff_brief_is_shell_command, _maybe_guard_shell_handoff_followup,
)
# StreamState refactor (2026-09-08) — gen()'s former ~20-variable
# closure (shared with _worker()/_ticker()) is now one explicit
# object threaded through worker.py (mode dispatch) / watchdog.py
# (queue consumption + timeout race) / retries.py (confidence-
# mismatch gate). See stream_state.py's docstring for the field
# mapping. Mechanical extraction — no behavior change.
from .stream_state import StreamState
from .worker import run_worker
from .watchdog import consume_worker_queue
from .retries import run_confidence_gate

logger = logging.getLogger(__name__)


# ─── Iter 212m-149 — Intent Gateway live-classify endpoint ────────────
# Used by the chat composer to render the live tier-dot indicator
# (casual / query / agentic) as the user types.  Heuristic-only
# (escalate_to_llm=False) so it returns in <5 ms with no LLM cost.


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatBody,
    authorization: Optional[str] = Header(None),
):
    """SSE token-streaming chat. Iter 45: rate-limited to 30 req/min per IP.
    Iter 50.1: founders / unlimited accounts bypass the rate-limit."""
    user = await current_dev(authorization)
    # Overnight loop W2 Step 2 (2026-08-29) — MOCK_LLM short-circuit,
    # BEFORE any provider routing/tool/repo-context construction. The
    # live main-chat path (this function) was the one surface
    # MOCK_LLM=true never actually reached — services/ora_chat_v2's
    # admin advisor chat already honored it, this one always made a
    # real paid call. Deliberately NO aurem-handoff fence (never fake
    # a ship/approve signal) and zero downstream construction, so a
    # spy provider that raises on construction is never even touched.
    from services.ora_chat_v2.llm_client import is_mock as _mock_llm_on
    if _mock_llm_on():
        async def _mock_gen():
            yield f"data: {json.dumps({'meta': True, 'session_id': body.session_id, 'provider': 'mock', 'mode': 'A', 'temperature': 0.0, 'thinking_s': 0.0, 'tool_calls_run': 0})}\n\n"
            _mock_text = (
                "I'm ORA (mock mode). The live model isn't connected on "
                "this instance — no real LLM calls are being made. This "
                "is a placeholder for UX testing."
            )
            yield f"data: {json.dumps({'token': _mock_text})}\n\n"
            yield f"data: {json.dumps({'done': True, 'provider': 'mock', 'session_id': body.session_id, 'tokens_remaining': None})}\n\n"
        return StreamingResponse(
            _mock_gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "Connection": "keep-alive"},
        )
    # Iter 364 · Phase 3 · Token hard-stop enforcement (streaming path).
    # Same as /chat/send — refuse the request before spending any LLM
    # tokens when the wallet is empty or the monthly task cap is blown.
    from services.usage import assert_has_budget, assert_has_task_budget
    await assert_has_budget(user["user_id"])
    await assert_has_task_budget(user["user_id"])
    # Iter 212m-49 — clear stale LLM provenance from a previous turn
    # in the same worker so the SSE `done` frame for THIS turn only
    # reflects what we actually hit this request.
    try:
        from services.llm import reset_last_provider
        reset_last_provider()
    except Exception:
        pass
    # Iter 212m-48 — prompt-injection guard. Block known jailbreak
    # markers before any LLM call. We log ONLY the rule label, never
    # the offending content (per security spec). Founders / admins
    # are NOT exempted: the protection is for the model, not the user.
    _pi_hit = detect_prompt_injection(body.prompt or "")
    if _pi_hit:
        logger.warning(
            "prompt_injection_blocked user_id=%s rule=%s",
            user.get("user_id", "?"), _pi_hit,
        )
        raise HTTPException(400, "This message cannot be processed")
    if not (bool(user.get("is_unlimited")) or user.get("tier") == "founder"):
        from services.rate_limiter import check_rate_limit_async, client_ip_from_request
        if not await check_rate_limit_async(f"chat:{client_ip_from_request(request)}", 30):
            raise HTTPException(429, "Rate limit exceeded: 30 chats/min/IP")

    # Iter 212m-58 — Loop-mode prompt enrichment. When the frontend
    # opts in to Loop mode it sends `execution_mode="loop"` plus one
    # of two `loop_phase` hints prefixed to the prompt itself. The
    # backend doesn't drive the loop — the frontend orchestrates the
    # 5 phases across successive /chat/stream calls. Our only job
    # here is to wrap the prompt with a system-style instruction so
    # the model knows to (a) respond plan-only on phase 1 and (b)
    # emit `[STEP X/5: NAME]` markers at every phase boundary.
    #
    # Iter 212m-168 — align _is_founder with email allowlist so ORA
    # dogfood accounts (founders using their real email but not yet
    # promoted to tier=founder in DB) also get local-pod access.
    # NOTE: _is_founder below gates unrelated founder-only surfaces
    # (e.g. the execute_bash tool via _is_fnd_stream at is_founder=)
    # and is intentionally narrower than the Loop Mode tier policy.
    _is_founder = bool(
        user.get("is_admin") or user.get("is_unlimited")
        or (user.get("tier") == "founder")
        or is_founder_email(user.get("email"))
    )
    # Iter 212m-168 — alias for the orchestrator's execute_bash gate.
    _is_fnd_stream = _is_founder
    # Iter 212m-181 — Loop Mode gate. This used to hardcode "founder
    # only" here (stale since 2026-08-21's Pro/Team rollout — see
    # services/loop_beta.py), which silently downgraded execution_mode
    # to "prompt" for every real Pro/Team customer hitting this stream
    # path (continuation/fallback turns), even though the dedicated
    # /loop/start kick-off endpoint had already been unlocked for them.
    # loop_beta.is_user_allowed() is now the single source of truth for
    # tier eligibility on both entry points so they can't drift apart
    # again. Concurrency cap / wall-clock budget / stuck-loop auto-trip
    # live entirely in loop_engine and are unaffected either way. The
    # kill-switch is checked explicitly below (is_user_allowed() only
    # answers "is this tier eligible", not "is loop globally off") so
    # flipping it also stops the [STEP X/5] contract from being
    # injected into continuation turns, not just new /loop/start calls.
    if (body.execution_mode or "").lower() == "loop":
        from services import loop_beta as _lb_gate
        _loop_allowed, _loop_reject = _lb_gate.is_user_allowed(user)
        if _loop_allowed and await _lb_gate.is_kill_switch_on_async(get_db()):
            _loop_allowed, _loop_reject = False, "kill_switch"
        # Iter 212m-182 · Guard 21 — same gate-parity telemetry as
        # /loop/start (see log_gate_decision docstring). Best-effort,
        # never blocks the chat turn.
        await _lb_gate.log_gate_decision(
            get_db(), entry_point="chat_stream", user_id=user.get("user_id", ""),
            tier=user.get("tier"),
            decision="allowed" if _loop_allowed else "denied",
            reject_reason=None if _loop_allowed else _loop_reject,
        )
        if not _loop_allowed:
            body.execution_mode = "prompt"
    if (body.execution_mode or "").lower() == "loop":
        _loop_suffix = (
            "\n\n[LOOP MODE — 5-phase pipeline]\n"
            "Your reply must follow this contract:\n"
            "• If the user message begins with `LOOP_PHASE:plan`, respond "
            "with a NUMBERED PLAN ONLY (3-7 concrete bullets describing "
            "what files you will touch and in what order). Start the "
            "reply with the literal marker `[STEP 1/5: PLAN]` on its own "
            "line. Do NOT write any code, do NOT call any tools, and end "
            "with `[PLAN_READY]` on its own line so the UI knows to show "
            "the Approve button.\n"
            "• If the user message begins with `LOOP_PHASE:execute`, "
            "proceed with the plan you proposed earlier. Emit "
            "`[STEP 2/5: EXECUTE]` before file writes, "
            "`[STEP 3/5: VERIFY]` before linting/tests, "
            "`[STEP 4/5: SECURITY]` before the Vanguard scan, and "
            "`[STEP 5/5: SHIP]` before the final commit. If verification "
            "fails up to 3 times, stop and explain the error.\n"
            "• Otherwise (no loop phase hint), behave as normal."
        )
        body.prompt = (body.prompt or "") + _loop_suffix

    jwt_token = authorization.split(" ", 1)[1] if authorization else ""
    user_id = user.get("user_id", "")

    # Iter 212m-169/170 — Build ORAContext for the stream endpoint.
    # Iter 212m-176 — pre-gen phase timing. PROD showed intermittent
    # zero-frame streams killed by the proxy at ~125s on repo turns
    # ("Analyze the health…", Ask Advisor). These logs bracket every
    # pre-StreamingResponse await so the hanging phase is identifiable
    # from a single grep.
    import time as _pg_time
    _pg_t0 = _pg_time.monotonic()
    _db_bc = get_db()
    _pid_stream = (body.project_id or "").strip()
    bin_ctx = None
    if _pid_stream and _pid_stream != "home":
        from services.ora_context import build_ora_context
        try:
            # Iter 212m-177 P1-6 — hard cap (Mongo + GitHub /repos call).
            bin_ctx = await asyncio.wait_for(build_ora_context(
                user_id=user_id,
                project_id=_pid_stream,
                db=_db_bc,
                is_founder=bool(
                    user.get("is_admin") or user.get("is_unlimited")
                    or (user.get("tier") == "founder")
                    or is_founder_email(user.get("email"))
                ),
            ), timeout=10.0)
        except asyncio.TimeoutError:
            raise HTTPException(
                503,
                "Repo context timed out (GitHub slow?) — try again in a "
                "few seconds.",
            )

    # Iter 212m-139 — Ask Advisor "No repo connected" bug fix.
    # Iter 212m-141 — Hardened to filter by ACTUAL GitHub reachability.
    #
    # When the frontend hasn't yet stamped an active-project tab (e.g.
    # the user has exactly one connected repo and never had to click a
    # tab to switch), `body.project_id` arrives as null/empty. Every
    # downstream tool (`read_repo_files`, `get_repo_structure`, etc.)
    # then hits `_resolve_project(..., project_id=None)` and returns
    # "No project connected", which the LLM faithfully reports back as
    # "no repo is connected right now" — even though the user has one
    # Iter 212m-169 — BINContext hardening: the SILENT auto-infer at
    # this entry point is REMOVED.  When `body.project_id` is
    # blank/"home" we simply do NOT build a bin_ctx; the caller either
    # explicitly picked a project (bin_ctx built and validated below)
    # or they are on the Home casual-chat surface (no repo tools —
    # the tool dispatch layer refuses cleanly).
    #
    # No more silent "one project fits all" heuristic that could
    # mis-route a chat about Project 1 into Project 2's PAT.  The FE
    # is responsible for stamping the active project_id on every turn.

    # Iter 38: ORA is founder-only. The ORA API key is shared across all
    # founders, so we gate at the surface to avoid customer quota burn.
    # Iter 205 — The Ask Advisor side panel (ORASidePanel) hardcodes
    # `agent="ora"` for every user. Instead of 403'ing free-tier users
    # (breaking Ask Advisor entirely), silently downgrade to the default
    # orchestrator so they get Claude/DeepSeek from their own quota.
    if (body.agent or "").lower() == "ora":
        # Iter 339m — do NOT re-import is_founder_email here: a local
        # import anywhere in this function makes the name local for the
        # WHOLE function scope, so the earlier _is_founder check (line
        # ~1121) raised UnboundLocalError for every non-admin account →
        # 499 on ALL free-tier prompt chats. Module-level import is used.
        if not is_founder_email(user.get("email")):
            body.agent = "auto"

    # Iter 157 — COLD START FIX.
    # Three context-builders below used to run sequentially with NO
    # outer timeout:
    #   - get_repo_context()  → 5-15 GitHub API calls (worst case 15-45s)
    #   - get_brain_context() → Mongo read + optional GH PAT call (1-5s)
    #   - build_url_context() → external URL scrape (1-10s)
    #
    # On a fresh chat session against a real repo the wall-clock for
    # JUST the context build was hitting 30-60s BEFORE the LLM was
    # even invoked, which fed the 300s "thinking…" stalls users were
    # reporting on production.
    #
    # Fix:
    #   1. Run all three IN PARALLEL via asyncio.gather.
    #   2. Wrap each in asyncio.wait_for(timeout=12s). If a builder
    #      misses the budget we degrade with an empty string — the
    #      orchestrator still has the persona + local tools and the
    #      LLM can call read_repo_file itself.
    #   3. Total upper bound for the context phase: 12s (not 60s+).
    async def _safe(coro, label, timeout_s=12.0):
        try:
            return await asyncio.wait_for(coro, timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "chat-context: %s timed out after %ss — degrading",
                label, timeout_s,
            )
            return ""
        except Exception as e:
            logger.warning(
                "chat-context: %s failed (%r) — degrading",
                label, e,
            )
            return ""

    # Iter 212m-23 — URL fetching is NO LONGER done eagerly here.
    # The orchestrator force-invokes `fetch_url` tool when the prompt
    # contains an http(s) URL (see services/orchestrator.py forced
    # URL pre-fetch block). This routes URL content through the
    # standard tool-invocation pipeline so users see a 📖 Reading URL…
    # step card and a 🌐 web_sources chip — no more silent context
    # stuffing that bypassed the tool UI.
    repo_ctx = await _safe(get_repo_context(user_id, body.project_id or ""), "repo_context")
    url_ctx = ""

    # ── Iter 212m-26 — Auto-ship-shortcut REMOVED ─────────────────────
    # Previously this point invoked `_maybe_ship_shortcut(...)` which
    # auto-fired a CTO task whenever the user typed a short confirma-
    # tion ("yes", "ok", "fix", "go") after an assistant turn with an
    # aurem-handoff fence. That bypassed the user's manual click on
    # the "🚀 Approve the fix" button (MessageBubble.jsx → ShipDialog).
    #
    # The sibling `_maybe_clarify_short_fix` guard depended on the
    # same keyword detection and is also gone. Shipping now ONLY
    # happens when the user explicitly clicks the button. Short
    # conversational replies flow into the normal orchestrator.

    # Iter 172 — independent guard: if the most recent assistant
    # handoff was a shell command, intercept ANY short follow-up
    # before it stalls the orchestrator. (Still active — this is
    # orthogonal to the auto-ship behaviour.)
    # Iter 212m-177 — P1-6: hard 10s cap. This Mongo lookup ran
    # unbounded before the stream starts; any stall here = zero-frame
    # hang until the proxy kills the connection (~125s on PROD).
    try:
        _clarify_text = await asyncio.wait_for(
            _maybe_guard_shell_handoff_followup(body=body, user_id=user_id),
            timeout=10.0,
        )
    except (asyncio.TimeoutError, Exception) as _cge:
        logger.warning("chat_stream: shell-handoff guard skipped (%r)", _cge)
        _clarify_text = None

    if _clarify_text is not None:
        async def _clarify_stream():
            import time as _t, json as _j
            meta = {
                "meta": True,
                "session_id": body.session_id,
                "provider": "aurem-clarify-fix",
                "mode": "A",
                "temperature": 0.0,
                "thinking_s": 0.0,
                "tokens_in": 0,
                "tokens_out": 0,
                "t_started": _t.monotonic(),
            }
            yield f"data: {_j.dumps(meta)}\n\n"
            yield f"data: {_j.dumps({'delta': _clarify_text})}\n\n"
            yield f"data: {_j.dumps({'done': True, 'content': _clarify_text})}\n\n"
            await _persist_turn(
                user_id=user_id,
                session_id=body.session_id or "",
                user_prompt=body.prompt or "",
                assistant_reply=_clarify_text,
                provider="aurem-clarify-fix",
                project_id=body.project_id,
            )
        return StreamingResponse(
            _clarify_stream(), media_type="text/event-stream",
        )
    # Inject the project's persistent memory (recent commits, tech stack,
    # past decisions, rejected ideas, recurring bugs) so a fresh chat
    # turn knows what AUREM has already shipped on this repo. Previously
    # only the CTO worker used the brain — chat never read it, which is
    # why users kept getting "I don't know about that feature" replies
    # for things AUREM itself had committed minutes earlier.
    brain_ctx = ""
    if body.project_id and body.project_id != "home":
        try:
            _proj = await asyncio.wait_for(get_db().cto_projects.find_one(
                {"project_id": body.project_id, "user_id": user_id},
                {"_id": 0, "github_owner": 1, "github_repo": 1,
                 "github_token": 1, "auth_method": 1,
                 "installation_id": 1, "user_id": 1},
            ), timeout=10.0)   # Iter 212m-177 P1-6 hard cap
            owner = (_proj or {}).get("github_owner") or ""
            repo = (_proj or {}).get("github_repo") or ""
            repo_full = f"{owner}/{repo}" if owner and repo else body.project_id
            from services.project_brain import get_brain_context
            # Best-effort: surface the GitHub PAT so the brain can pull
            # the last 5 commits from the remote — covers commits made
            # outside AUREM (direct CLI pushes / other contributors).
            # 2026-02-11 · Phase 3b (Bug 2 fix) — dual-auth token resolver.
            _pat = None
            try:
                # 2026-06 PAT-removal — App-only, no OAuth fallback.
                from services.pat_vault import get_repo_token_or_error
                async def _pat_lookup():
                    t, _e, _d = await get_repo_token_or_error(_proj or {})
                    return t
                _pat = await asyncio.wait_for(_pat_lookup(), timeout=10.0)
            except Exception:
                _pat = None
            # Iter 157 — also wrap brain context in the same 12s budget;
            # this used to be the slowest of the three on first turn
            # because it pulls remote commit history.
            brain_ctx = await _safe(
                get_brain_context(
                    get_db(), body.project_id, repo_full,
                    github_token=_pat,
                ),
                "brain_context",
            )
            if brain_ctx:
                brain_ctx = "[PROJECT MEMORY]\n" + brain_ctx
        except Exception:
            logger.exception("chat: brain context fetch failed (continuing)")
            brain_ctx = ""
    extra_sys = "\n\n".join(
        s for s in (repo_ctx, brain_ctx, url_ctx) if s
    )

    # Iter 212m-77/78 — ORA Council self-learning (streaming path).
    # Same RAG retrieval as /chat/send. Skip for ora_panel=true. Count
    # is closed over by gen() and emitted as an SSE `council` frame
    # BEFORE token streaming begins so the FE can render the caption.
    _council_recalled = 0
    _council_block = ""
    _recall_mode = None
    if not body.ora_panel:
        try:
            from cto_services.db import get_db as _get_db
            from services.ora_council_retriever import get_council_few_shot
            _db_ref = _get_db()
            if _db_ref is not None:
                # Iter 212m-177 P1-6 — hard cap: the retriever's lazy
                # _rebuild_index() can walk a large ora_council_logs
                # collection; unbounded it stalls the whole stream.
                # 2026-08-27 · mode-taxonomy fix — same as chat/send:
                # the recall index is keyed "A"/"B"/"D"/"E" (classify_
                # intent's taxonomy), not "code"/"chat" (_detect_mode's).
                # Computed once here and reused below at the existing
                # A/B/C/D/E broadcast so we don't classify twice.
                _recall_mode = classify_intent(body.prompt or "", body.f12_payload)
                _council_block, _council_recalled = await asyncio.wait_for(
                    get_council_few_shot(
                        _db_ref, body.prompt or "",
                        mode=_recall_mode,
                        user_id=user.get("user_id"),
                        project_id=body.project_id,
                        k=2,
                    ), timeout=10.0)
                if _council_block:
                    extra_sys = (_council_block
                                 + ("\n\n" + extra_sys if extra_sys else ""))
        except Exception as _cre:
            logger.debug("council retrieval skipped (chat/stream): %r", _cre)

    # 2026-08-27 · Plain-English Output Contract (Phase 1, flag-gated).
    # Reuses `_recall_mode` computed above (None when ora_panel=True,
    # so this block naturally never fires for Ask Advisor).
    _plain_english_active = False
    try:
        if _recall_mode == "A":
            from services.feature_flags import is_enabled as _pee_enabled
            if await _pee_enabled(
                "explain_plain_english_v1",
                user_id=user.get("user_id"), tier=user.get("tier"),
            ):
                extra_sys = (extra_sys + "\n\n" + PLAIN_ENGLISH_EXPLAIN_CONTRACT).strip()
                _plain_english_active = True
    except Exception as _pee_exc:
        logger.debug("plain_english_contract skipped (chat/stream): %r", _pee_exc)

    # 2026-08-31 · Business-Owner Voice Contract — every non-ora_panel
    # turn, not flag-gated (see BUSINESS_OWNER_VOICE_CONTRACT docstring).
    if not body.ora_panel:
        extra_sys = (extra_sys + "\n\n" + BUSINESS_OWNER_VOICE_CONTRACT).strip()

    # Iter 212m-24 — Admin House Rules (HIGHEST PRIORITY).
    # For SSE chat (non-Advisor), scope is "chat" + the requested mode.
    # For Ask Advisor turns (body.ora_panel == True) we re-resolve
    # below with target="advisor" so the advisor toggle drives that
    # flow. Either way the rules block is PREPENDED to extra_sys so
    # ORA reads them BEFORE its own persona / tools / project ctx.
    if not body.ora_panel:
        try:
            from services.house_rules import (
                get_active_house_rules, format_house_rules_block,
                get_active_chat_prompt,
            )
            _hr_prompt = await asyncio.wait_for(get_active_house_rules(
                "chat", (body.mode or "swift").lower(),
            ), timeout=10.0)   # Iter 212m-177 P1-6
            if _hr_prompt:
                extra_sys = (
                    format_house_rules_block(_hr_prompt)
                    + ("\n\n" + extra_sys if extra_sys else "")
                )
            # Iter 212m-171 — dedicated CHAT prompt slot.
            _chat_extra = await asyncio.wait_for(
                get_active_chat_prompt(), timeout=10.0)  # Iter 212m-177 P1-6
            if _chat_extra:
                extra_sys = (
                    "=== ADMIN CHAT PROMPT (Iter 212m-171) ===\n"
                    f"{_chat_extra}\n"
                    "=== END ADMIN CHAT PROMPT ===\n\n"
                    + (extra_sys or "")
                )
        except Exception as _hre:
            logger.debug("house_rules injection skipped (chat/stream): %r", _hre)

    # Iter 159 — ASK ORA panel uses a deliberately CASUAL voice.
    # This block is injected ONLY when the caller sets ora_panel=true
    # (the floating right-side panel). The main coding chat is
    # untouched — it keeps the professional `AUREM_CTO_PERSONA` tone
    # from orchestrator.py. The block goes LAST in extra_sys so it
    # overrides the default TONE & FORMAT layer for this turn only.
    # Iter 185 — replaced the older Iter 160 TTS-only voice override
    # with the ORA_PANEL_TONE constant (defined at module top): a
    # two-mode Ask Advisor framework with a hard 150-word ceiling and
    # direct, project-context-aware answers. See ORA_PANEL_TONE for
    # the full prompt.
    if body.ora_panel:
        extra_sys = (extra_sys + "\n\n" + ORA_PANEL_TONE).strip()
        # Iter 212m-24 — House Rules for Ask Advisor (advisor toggle).
        # Iter 212m-211 — SENTINEL LOG so we can assert in tests +
        # monitor in prod that the advisor role is really seeing the
        # house-rules payload (not silently skipped by the try/except
        # on a DB hiccup).
        _hr_prompt_adv_seen = False
        try:
            from services.house_rules import (
                get_active_house_rules, format_house_rules_block,
            )
            _hr_prompt_adv = await asyncio.wait_for(
                get_active_house_rules("advisor", None),
                timeout=10.0)  # Iter 212m-177 P1-6
            if _hr_prompt_adv:
                extra_sys = (
                    format_house_rules_block(_hr_prompt_adv)
                    + ("\n\n" + extra_sys if extra_sys else "")
                )
                _hr_prompt_adv_seen = True
        except Exception as _hre:
            logger.debug("house_rules injection skipped (advisor): %r", _hre)
        logger.info(
            "advisor_house_rules: injected=%s (project_id=%s, user=%s)",
            _hr_prompt_adv_seen, body.project_id, user_id,
        )

    async def gen():
        import time as _t
        # Iter 36: hard wall-clock ceiling — if the worker doesn't return
        # within HARD_TIMEOUT_S we abort and emit a friendly error so the
        # UI can never "thinking…" for 15 minutes again. Iter 169 — 180s
        # wall + 150s orch budget gives a 30s reserve. See watchdog.py's
        # module docstring for the timeout-race mechanics.
        HARD_TIMEOUT_S = float(os.getenv("CHAT_HARD_TIMEOUT_S", "180"))
        # 2026-08-23 audit fix — proxy-safe soft budget, same graceful
        # timeout path, triggered earlier when there's been little/no
        # tool-call progress yet. See watchdog.py for the full rationale.
        SOFT_TIMEOUT_S = float(os.getenv("CHAT_SOFT_TIMEOUT_S", "48"))
        # StreamState refactor (2026-09-08) — replaces the ~20-var
        # closure formerly shared between gen()/_ticker()/_worker().
        # See stream_state.py for the field mapping.
        state = StreamState(
            request=request, body=body, authorization=authorization,
            user=user, user_id=user_id, jwt_token=jwt_token,
            pid_stream=_pid_stream,
            bin_ctx=bin_ctx, repo_ctx=repo_ctx, brain_ctx=brain_ctx,
            extra_sys=extra_sys, council_recalled=_council_recalled,
            council_block=_council_block, recall_mode=_recall_mode,
            plain_english_active=_plain_english_active, is_founder=_is_founder,
            hard_timeout_s=HARD_TIMEOUT_S, soft_timeout_s=SOFT_TIMEOUT_S,
        )
        state.t_start = _t.monotonic()
        # Iter 212m-78 — Council recall caption. Emit FIRST so the
        # FE renders "📚 ORA recalled N similar past answers" before
        # any tokens arrive. Skipped silently when count is 0.
        if state.council_recalled and state.council_recalled > 0:
            yield (
                "data: " + json.dumps({
                    "type":             "council",
                    "council_recalled": int(state.council_recalled),
                }) + "\n\n"
            )

        async def _ticker():
            while True:
                try:
                    await asyncio.wait_for(state.stop_event.wait(), timeout=0.6)
                    return
                except asyncio.TimeoutError:
                    elapsed = round(_t.monotonic() - state.t_start, 1)
                    # Iter 149 — also emit the LIVE tool invocations list so
                    # the UI can render chips ("read_repo_file ✓", "search_repo …")
                    # right below the thinking bar instead of only the label.
                    _inv = list(state.activity.get("invocations") or [])
                    await state.q.put({
                        "type": "tick",
                        "elapsed_s": elapsed,
                        "activity": state.activity["label"],
                        "invocations": _inv,
                    })

        ticker_t = asyncio.create_task(_ticker())
        worker_t = asyncio.create_task(run_worker(state))

        # Iter 141 — emit an immediate meta frame so the client gets
        # progress feedback inside 10 ms instead of waiting for the
        # orchestrator's first LLM round-trip (which can be 1-5 s on
        # OpenRouter cold-start). The frontend uses this to anchor the
        # real-progress bar at 15% the moment the request is accepted.
        yield (
            "data: " + json.dumps({
                "meta": True,
                "session_id": body.session_id,
                "provider": "aurem-cto",
                "thinking_s": 0.0,
                "tool_calls_run": 0,
            }) + "\n\n"
        )

        async for _chunk in consume_worker_queue(state, worker_t, ticker_t):
            yield _chunk
        if state.timed_out:
            return
        result = state.result
        collected_steps = state.collected_steps

        # Iter 331 — founder-reported prod crash on long pasted inputs:
        # "'NoneType' object has no attribute 'get'" surfaced as the
        # chat reply. Any pipeline branch that lands a non-dict (None)
        # result must degrade into the empty-content fallback below,
        # never crash the stream.
        if not isinstance(result, dict):
            logger.warning("chat_stream: pipeline returned %s instead of dict — "
                           "engaging fallback", type(result).__name__)
            result = {"content": "", "error": "pipeline returned no result"}

        # Iter 51 — SSE Task Progress Streamer.
        # When the worker auto-enqueued a Mode C task (Mode D→C handoff,
        # or any future flow that lands a `task_id` on the result), surface
        # it to the frontend BEFORE meta/content streaming so the chat
        # bubble can pin the live ShipStatusCard without waiting for the
        # full text reply.
        handoff_task_id = result.get("task_id") if isinstance(result, dict) else None
        handoff_project_id = result.get("project_id") if isinstance(result, dict) else None
        if handoff_task_id:
            yield (
                "data: " + json.dumps({
                    "type": "task_handoff",
                    "task_id": handoff_task_id,
                    "project_id": handoff_project_id,
                    "source": result.get("provider") or "auto_handoff",
                }) + "\n\n"
            )

        content = result.get("content", "") or ""
        provider = result.get("provider", "") or ""
        mode = _detect_mode(body.prompt)
        from services.llm import temperature_for
        temperature = temperature_for(mode)

        # Iter 212m-155 — SAFETY NET against silent SSE close.
        # On PROD, the agentic tier occasionally returned an empty
        # `content` (caused by upstream LLM throttle / mid-loop bail
        # / no-tools-needed branch that produced no text).  The
        # streaming loop below then yielded zero token frames and
        # the user was stuck on "thinking…" forever.  We now emit a
        # graceful fallback so the bubble always has something to
        # render — even when the upstream pipeline failed silently.
        if not content.strip():
            _fb_reasons = []
            _fb_err = result.get("error") or result.get("warning")
            if _fb_err:
                _fb_reasons.append(str(_fb_err)[:160])
            if result.get("tool_calls_run", 0) == 0 and (result.get("iterations", 0) or 0) > 0:
                _fb_reasons.append("the model decided no tools were needed")
            _tier_hint = result.get("tier") or (result.get("intent") or {}).get("tier") or "agentic"
            content = (
                f"_(I wasn't able to produce a reply for this {_tier_hint} request"
                + (f": {_fb_reasons[0]}" if _fb_reasons else ".")
                + " Please rephrase or try again — the chat itself is healthy.)_"
            )
            logger.warning(
                "chat_stream: empty content fallback used (tier=%s tool_calls_run=%s iters=%s)",
                _tier_hint, result.get("tool_calls_run"), result.get("iterations"),
            )

        # 2026-08-21 — cold-start / recall-mismatch mitigation. See
        # services/response_confidence.py. Must run BEFORE the token
        # stream loop below so the user never sees the mismatched
        # content stream in — swapping it after streaming has begun
        # is too late. 2026-08-22 — hardened with a quiet auto-retry
        # (layer d) and verbose real-log observation (founder ask)
        # before falling back to the canned message. Extracted to
        # retries.py (2026-09-08 StreamState refactor) — mechanical
        # move, same side effects now land on `state.*`.
        content, provider = await run_confidence_gate(state, content, provider)

        # 2026-08-28 · NEW P0 Task 2 — final defense-in-depth (see
        # chat_send's identical comment for the full rationale).
        # 2026-09-04 — consolidated into ONE shared helper.
        content = apply_output_guards(
            body.prompt or "", content, state.prior_fix_signal,
            retrieved_context_for_grounding(extra_sys, result),
            skip=bool(result.get("_skip_output_guards")),
        )

        # 2026-08-27 · Output Guard (Phase 1 net) — runs AFTER the
        # mismatch/fallback resolution above (so it nets whatever text
        # is actually about to stream to the user), BEFORE the token-
        # streaming loop below. Never touches ship/confirm content.
        # P5 — leak-stripping runs for EVERY user; length-capping stays
        # flag-gated (see chat/send's identical comment above).
        _leak_stripped = False
        _length_capped = False
        _output_guard_ref_id = None
        if content and "aurem-handoff" not in content:
            try:
                from services.output_guard import strip_machinery_leak, enforce_length_cap, extract_named_files
                from core.errors import new_ref_id
                # universal_only=True unless explain-mode is active this
                # turn — see identical comment on chat/send above.
                # M3 (2026-08-30): same user-named-file exemption.
                content, _leak_stripped = strip_machinery_leak(
                    content, universal_only=not _plain_english_active,
                    user_named_files=extract_named_files(body.prompt),
                )
                if _plain_english_active:
                    content, _length_capped = await enforce_length_cap(content)
                if _leak_stripped or _length_capped:
                    _output_guard_ref_id = new_ref_id()
            except Exception as _og_exc:
                logger.debug("output_guard skipped (chat/stream): %r", _og_exc)

        # R1 (2026-08-31) — business-owner voice filter, applied LAST
        # (after output_guard above) so nothing downstream can
        # re-introduce a raw filename/dev term. Same "aurem-handoff"
        # exemption as output_guard: that fence is structured
        # ship-pipeline machinery (Approve/ShipDialog parses the real
        # file path from it) — not prose. See chat_send's identical
        # comment for the full reasoning (R5a K1 approve-button risk).
        if content and "aurem-handoff" not in content:
            try:
                from services.business_voice_filter import apply_business_owner_guards
                content = await apply_business_owner_guards(
                    getattr(body, "ora_panel", False), content, body.prompt or "",
                    session_id=body.session_id, user_id=user.get("user_id"),
                )
            except Exception as _bvf_e:
                logger.debug("business_voice filter skipped (chat_stream): %r", _bvf_e)

        meta = {"meta": True, "session_id": body.session_id,
                "provider": provider, "mode": mode, "temperature": temperature,
                "thinking_s": round(_t.monotonic() - state.t_start, 1),
                "tool_calls_run": result.get("tool_calls_run", 0),
                "low_confidence": state.low_confidence,
                "bail_reason": state.bail_reason,
                "ship_suppressed": state.ship_suppressed}
        yield f"data: {json.dumps(meta)}\n\n"

        CHUNK = 6
        i = 0
        while i < len(content):
            chunk = content[i:i + CHUNK]
            yield f"data: {json.dumps({'token': chunk})}\n\n"
            i += CHUNK
            await asyncio.sleep(0.012)

        # Maxx mode: emit a stream marker, then run watchdog and emit result
        # Iter 161 — when `body.mode == "maxx"` Claude has ALREADY written
        # the code (use_code_model forces Claude inside orchestrator), so
        # running the Emergent watchdog on top is duplicate work and just
        # adds the misleading "Watchdog · passed 8/18" pill on a reply
        # that didn't need it. We keep the watchdog path alive ONLY for
        # legacy clients that still set maxx_mode without the new mode
        # pill — those exist in the wild from older cached bundles.
        watchdog = None
        is_new_maxx_pill = (body.mode or "").lower() == "maxx"
        if body.maxx_mode and content.strip() and not is_new_maxx_pill:
            yield f"data: {json.dumps({'watchdog_pending': True})}\n\n"
            watchdog = await call_emergent_watchdog(content)
            yield f"data: {json.dumps({'watchdog': watchdog})}\n\n"
            provider = (provider or "deepseek") + "+emergent-watchdog"

        # Iter 212m-154 — non-blocking quality scoring.  Fire-and-forget
        # AFTER the user has seen their reply.  Writes to quality_scores
        # collection and raises drift alerts when avg drops sharply.
        # Never propagates errors — wrapped in try/except inside.
        try:
            from core.quality_monitor import QualityMonitor as _QM
            asyncio.create_task(_QM(db=get_db()).score_async(
                response=content or "",
                user_message=body.prompt or "",
                tier=result.get("tier") or "agentic",
                session_id=body.session_id or "",
                tenant_id=user_id,
            ))
        except Exception:
            pass    # quality monitor never blocks the user flow

        # Iter 145 — ORA shadow-learning. For ALL users, detect
        # low-confidence AUREM replies and fire a background ORA call
        # whose output is logged (never shown) so ORA can learn
        # patterns from real weak-spots. NEVER replaces user reply.
        try:
            from services.ora_learning import maybe_log_ora_escalation
            asyncio.create_task(maybe_log_ora_escalation(
                db=get_db(),
                user_id=user_id,
                session_id=body.session_id or "",
                project_id=body.project_id,
                prompt=body.prompt or "",
                aurem_response=content or "",
                provider=provider,
            ))
        except Exception:
            pass

        # Iter 212m — Session Learning. Fire-and-forget extraction of
        # hot files + stack signals into `ora_patterns` so the next
        # turn's warm context can pre-load what this user/project
        # tends to touch. Never blocks the response path.
        try:
            from services.ora_learning import extract_session_patterns
            if body.session_id:
                asyncio.create_task(extract_session_patterns(
                    db=get_db(),
                    user_id=user_id,
                    project_id=body.project_id,
                    session_id=body.session_id,
                ))
        except Exception as e:
            # Iter 331 — fail-open stays, but silently no more.
            logger.warning(
                "ORA shadow-learning (session patterns) skipped — fail-open: %r", e)

        # ORA council log (Mode A/B only) + project brain update.
        # Fire-and-forget; never blocks user reply.
        # BUG 5 fix — Mode D (debug) and E (audit) replies were getting
        # logged as A or B which poisons the training data. Only
        # conversational modes (A/B) belong in ora_council_logs from this
        # path; Mode C uses log_code_task, Mode D/E aren't part of the
        # fine-tuning corpus.
        # Iter 331 · #3-b callsite reattach — the casual intent-gateway
        # and advisor paths label their result `mode: "chat"` (proven
        # via live Mongo before/after: council count froze at 89 while
        # extract_session_patterns kept writing). Those ARE
        # conversational turns — the label mismatch silently detached
        # this callsite for the platform's main chat path. "chat" is
        # now accepted; D/E stay excluded (they carry explicit "D"/"E").
        _classified_mode = result.get("mode") if isinstance(result, dict) else None
        if _classified_mode in (None, "A", "B", "chat"):
            try:
                from services.ora_council_logger import log_conversational
                from services.project_brain import update_brain_from_conversation
                council_mode = "B" if "aurem-handoff" in (content or "") else "A"
                _db = get_db()
                if _db is not None:
                    await log_conversational(
                        db=_db,
                        mode=council_mode,
                        user_message=body.prompt or "",
                        ora_reply=content or "",
                        user_id=user_id,
                        project_id=body.project_id,
                        low_confidence=state.low_confidence,
                    )
                    # Lightweight conversation → brain update (rejections, decisions, stack)
                    if body.project_id and body.project_id != "home":
                        asyncio.create_task(update_brain_from_conversation(
                            db=_db,
                            project_id=body.project_id,
                            user_message=body.prompt or "",
                            ora_reply=content or "",
                            mode=council_mode,
                        ))
            except Exception as e:
                # Iter 331 — fail-open stays, but silently no more.
                logger.warning(
                    "ORA shadow-learning (council log / brain update) skipped — fail-open: %r", e)

        if body.session_id:
            asyncio.create_task(
                _maybe_set_title(user_id, body.session_id, body.prompt)
            )
        tokens_remaining = await _deduct_tokens(user_id, content)
        # 2026-08-19 P0 fix — same cost-logging gap as /chat/send.
        try:
            from services.customer_cost_tracker import log_customer_chat_cost
            await log_customer_chat_cost(
                user_id=user_id, session_id=body.session_id or "",
                project_id=body.project_id, route="chat_stream",
                provider=(result.get("provider") if isinstance(result, dict) else "") or "",
                prompt_text=body.prompt,
                system_text=state.sys_for_advisor or "", output_text=content,
            )
        except Exception as e:
            logger.warning("customer chat cost log skipped (chat_stream): %r", e)

        # ─── Iter 209: Core verification foundation ──────────────────
        # CitationGuard runs as a HARD blocker on the final draft. If
        # the model referenced file paths it didn't read this turn, we
        # auto-fetch them and re-run once with the verified content
        # injected as system context. Audit log records the outcome.
        # System signals (Core 2) are forwarded to the frontend via the
        # `system_signals` field so SystemSignalBanner.jsx can render
        # typed banners — the LLM never has to describe tool errors.
        guard_triggered = False
        guard_unverified: list[str] = []
        guard_fetched:    list[str] = []
        system_signals = list(result.get("system_signals") or [])

        try:
            from services.citation_guard import CitationGuard
            _turn_tool_calls = result.get("tool_calls") or []
            # 2026-08-23 — P0 fix: this `_ctx` was missing `bin_ctx`,
            # which every repo tool (`read_repo_file` et al.) requires
            # via `_repo_ctx_from()` — see services/local_tools.py.
            # Without it, EVERY re-fetch attempt below returned
            # `_NO_BIN_CTX_ERROR` (ok=False), which enforce() then
            # reports as "FILE NOT FOUND" for paths that were, in
            # fact, correctly read earlier this same turn — causing a
            # detailed, accurate audit reply to be immediately
            # rewritten into "none of the referenced files were found
            # or accessible" for those SAME files. `bin_ctx` is
            # already built earlier in this function (see above) and
            # scoped to this user/project — reuse it here.
            _ctx = {
                "user_id":     user_id,
                "project_id":  body.project_id,
                "github_token": result.get("_github_token"),
                "bin_ctx":     bin_ctx,
            }

            async def _llm_retry(*, original_messages=None,
                                 additional_context=None,
                                 instruction=None):
                # Lightweight retry: ask the same provider for a rewrite
                # with the injection appended as a system note. Falls
                # back to returning the original draft if the call fails.
                #
                # 2026-08-19 · Customer Chat Regen fix — this called
                # `services.orchestrator.respond_text`, a function that
                # has NEVER existed anywhere in the codebase. Every real
                # retry silently hit the `except Exception: return
                # content` fallback below and returned the ORIGINAL
                # (fabricated) draft unchanged — the guard's `retried`
                # flag went True, a no-op "reset" frame fired, but the
                # customer never actually got a corrected answer.
                # `call_llm()` (services/llm) is the real, existing
                # plain single-turn completion used elsewhere in this
                # file (see `_call_groq`/`_call_deepseek` rescue chain).
                try:
                    from services.llm import call_llm  # type: ignore
                    return await call_llm(
                        messages=original_messages or [],
                        system=f"{instruction or ''}\n\n{additional_context or ''}".strip(),
                    )
                except Exception:
                    return content

            guard_out = await CitationGuard().enforce(
                response_text=content,
                tool_calls=_turn_tool_calls,
                ctx=_ctx,
                llm_caller=_llm_retry,
                original_messages=result.get("messages") or [],
            )
            if guard_out.get("retried"):
                guard_triggered  = True
                guard_unverified = guard_out["guard"]["unverified_paths"]
                guard_fetched    = list((guard_out.get("fetched") or {}).keys())
                # Re-emit the rewritten content as a single token frame
                # so the frontend overwrites the (hallucinated) draft.
                content = guard_out["text"]
                yield f"data: {json.dumps({'token': content, 'reset': True})}\n\n"

                # 2026-08 — Fabrication learning loop. Log every real
                # CitationGuard trigger (fire-and-forget, never blocks
                # the response) so recurring per-project patterns can
                # surface a caution on future turns.
                try:
                    from services.ora_fix_learning import record_fabrication_incident
                    _db_fab = get_db()
                    asyncio.create_task(record_fabrication_incident(
                        _db_fab, source="customer_chat",
                        project_id=body.project_id, route="chat_stream",
                        user_prompt=body.prompt, unverified_paths=guard_unverified,
                        corrected=True, user_id=user_id,
                    ))
                except Exception as _fabrec_err:
                    logger.warning("fabrication incident record skipped: %r", _fabrec_err)
        except Exception as _guard_err:
            logger.warning("citation_guard skipped: %r", _guard_err)

        # 2026-08-19 · Customer Chat Regen — `_persist_turn` used to run
        # BEFORE the CitationGuard block above, so a fabricated file path
        # that got auto-corrected still landed in Mongo verbatim. The
        # live viewer saw the fix (via the `reset: True` token frame
        # above); a page refresh (GET /chat/history) showed the
        # ORIGINAL, uncorrected draft. Persisting `content` here
        # (post-guard) closes that gap.
        await _persist_turn(user_id, body.session_id or "",
                            body.prompt, content, provider, watchdog=watchdog,
                            project_id=body.project_id,
                            shipped_task_id=handoff_task_id,
                            steps=collected_steps,
                            low_confidence=state.low_confidence,
                            ship_suppressed=state.ship_suppressed,
                            bin_ctx=bin_ctx)

        # Fire-and-forget audit row — never block the response.
        try:
            from services.audit_log import record_turn
            asyncio.create_task(record_turn(
                user_id=user_id,
                project_id=body.project_id,
                tools_called=[
                    f"{(tc.get('tool') or tc.get('name') or '?')}:" +
                    str((tc.get('args') or tc.get('arguments') or {}).get('path', ''))
                    for tc in (result.get("tool_calls") or [])
                ],
                citation_guard_triggered=guard_triggered,
                citation_guard_paths_fetched=guard_fetched,
                citation_guard_unverified=guard_unverified,
                system_signals_emitted=[s.get("signal") for s in system_signals if s.get("signal")],
                llm_model=provider or "",
                response_tokens=len((content or "").split()),
                was_retry=guard_triggered,
                # 2026-08-27 — P2 audit-spine: leak-stripped + recall-
                # candidate events, reusing this existing `ora_audit`
                # row (no new collection). `_leak_stripped` only fires
                # when the output_guard net actually removed a
                # machinery token this turn; `recall_candidate` mirrors
                # the A0 council-recall counter so an admin can query
                # "how many turns pulled in past-answer recall".
                extra={
                    "leak_stripped": bool(_leak_stripped),
                    "length_capped": bool(_length_capped),
                    "recall_candidate": bool(_council_recalled),
                    "council_recalled_count": int(_council_recalled or 0),
                },
            ))
        except Exception as _aud_err:
            logger.warning("audit_log skipped: %r", _aud_err)

        done_payload = {
            "done": True,
            "provider": provider,
            "session_id": body.session_id,
            "tokens_remaining": tokens_remaining,
            # 2026-08-27 · P5 — was `bool(result.get("council"))`,
            # which coerced a real council label (e.g. "B") into a
            # bare boolean the frontend then rendered verbatim as
            # "via Council true". Pass the real value (letter/label
            # or None) through unchanged.
            "council": result.get("council"),
            "low_confidence": state.low_confidence,
            "ship_suppressed": state.ship_suppressed,
            # Iter 212m-171 — Scope Badge echo (see /chat/send).
            "repo_owner": getattr(bin_ctx, "repo_owner", None) if bin_ctx else None,
            "repo_name":  getattr(bin_ctx, "repo_name", None)  if bin_ctx else None,
            "branch":     getattr(bin_ctx, "branch", None)     if bin_ctx else None,
            # Iter 212m-49 — provenance of the last LLM hop. The frontend
            # surfaces a "⚡ free mode" pill in the chat header when this
            # turn was served by the Groq emergency fallback (i.e. both
            # the primary OpenRouter call AND every free-tier candidate
            # failed). `is_emergency=False` means OpenRouter served it
            # normally and the pill stays hidden.
            "llm_provenance": _safe_provenance(),
            # Iter 85 — paths the model actually read this turn.
            # Frontend uses this to enforce ABSOLUTE NEGATIVE rule (d):
            # any path inside the ```aurem-handoff fence that is NOT in
            # this set is a fabricated citation, so the Ship button is
            # suppressed.
            "verified_paths": result.get("verified_paths") or [],
            # Iter 119 — web sources (URLs the model actually fetched via
            # Tavily / Firecrawl / fetch_url). Frontend renders these as
            # 🌐 citation chips below the assistant message so users can
            # one-click verify external claims.
            "web_sources": result.get("web_sources") or [],
            # Iter 209 — typed tool-failure signals + citation-guard meta.
            "system_signals":          system_signals,
            "citation_guard_triggered": guard_triggered,
            # Iter 212m-78 — duplicate the Council recall counter on
            # the done frame so refresh flows + retry handlers can
            # still surface the caption even if the client missed the
            # early `council` frame.
            "council_recalled":        int(_council_recalled or 0),
            # 2026-08-27 — mirrors chat/send's field; true when the
            # explain-only plain-English contract was injected.
            "plain_english_contract_active": bool(_plain_english_active),
            # 2026-08-27 — Output Guard net results (Phase 1).
            "leak_stripped": bool(_leak_stripped),
            "length_capped": bool(_length_capped),
            "output_guard_ref_id": _output_guard_ref_id,
            # 2026-08-23 — findings-to-fix bridge. Critical/high
            # `save_finding` calls made this turn — lets the frontend
            # show a reliable "N issues found" teaser instead of
            # depending on the aurem-handoff fence to bundle them.
            "findings_saved": result.get("findings_saved_this_turn") or [],
            # Item 2 (2026-08-31) — palette nudges made this turn (before/
            # after swatches rendered by PaletteNudgeBubble.jsx, no jargon).
            "palette_nudges": result.get("palette_nudges") or [],
        }
        yield f"data: {json.dumps(done_payload)}\n\n"

    _pg_elapsed = _pg_time.monotonic() - _pg_t0
    if _pg_elapsed > 15:
        logger.warning(
            "chat_stream PRE-GEN SLOW: %.1fs before StreamingResponse "
            "(user=%s project=%s prompt=%.60s)",
            _pg_elapsed, user_id, _pid_stream, (body.prompt or ""),
        )
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


