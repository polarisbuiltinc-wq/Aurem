"""
routers/chat/worker.py — the /chat/stream mode-dispatch cascade,
extracted out of chat_stream()'s gen()._worker() closure (2026-09-08
StreamState refactor). Mechanical move — each function below is the
EXACT original code block, now reading/writing StreamState instead
of closure variables. See stream_state.py for the field mapping.

Cohesion note (per founder review): this is genuinely one dispatch
chain — Mode-D-fast-path → mode broadcast → Mode D/E → Mode B →
Mode F → ORA-agent-cascade → pre-LLM prep → Ask-Advisor-panel OR
orchestrator fallback — with each stage able to short-circuit
(return a result dict) or fall through to the next. `run_worker()`
mirrors that EXACT original if/elif/return chain; each stage is its
own function so no single function exceeds ~340 lines (the
Ask-Advisor-panel, kept as one cohesive blob per the 600-800 line
allowance for a unit that resists further splitting without
threading its own ~10 locals across yet another module boundary).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from services.orchestrator import chat_with_tools
from services.llm import call_llm_with_meta, call_emergent_watchdog
from services.chat_helpers import (
    _detect_mode, is_fix_confirmation, _f12_has_real_signal,
    _is_transient_proxy_error, _TRANSIENT_PROXY_CODES, classify_intent,
)
from core.task_type import infer_task_type as _infer_task_type
from cto_services.db import get_db

from .misc import BUSINESS_OWNER_VOICE_CONTRACT, ORA_PANEL_TONE
from .stream_state import StreamState

logger = logging.getLogger(__name__)


def _step(q: asyncio.Queue, text: str, done: bool = False) -> None:
    try:
        q.put_nowait({"type": "step", "text": text, "done": bool(done)})
    except Exception:
        pass


async def _mode_d_fast_path(state: StreamState) -> Optional[dict]:
    """Iter 212m-46 — KILL auto-ship on Mode D fix-confirm. The
    previous behaviour auto-enqueued a real CTO task when the user
    typed any "yes / ok / fix it" reply after a Mode D diagnosis.
    That bypassed the manual "🚀 Ship via CTO" button on the
    diagnosis bubble and the user reported commits firing without
    their consent.

    HARD RULE: never auto-ship. The Mode D diagnosis bubble now
    carries its own aurem-handoff fence (added in mode_d_debugger.py
    at iter 212m-46), so the Ship button already lives on that
    bubble. We just clear the legacy pending_fix_task flag and
    politely redirect the user to click the button — NO
    _enqueue_cto_task call here."""
    body, user_id, q = state.body, state.user_id, state.q
    if body.session_id and is_fix_confirmation(body.prompt or ""):
        _db = get_db()
        if _db is not None:
            # Iter 388-aa (2026-02-14): tightened IDOR — require
            # user_id in BOTH filter and update filter so a caller
            # who knows another user's session_id can't $unset
            # their pending_fix_task flag. Removed the legacy
            # "row has no user_id → allow" branch; any legacy
            # row that would trip it is a schema-drift bug that
            # should be backfilled, not silently mutable.
            _sess = await _db.chat_sessions.find_one(
                {"session_id": body.session_id, "user_id": user_id},
                {"_id": 0, "pending_fix_task": 1},
            )
            if _sess and _sess.get("pending_fix_task"):
                # Clear the legacy pending flag (we no longer
                # act on it; it's kept on the schema only so
                # we don't break older deployments mid-roll).
                await _db.chat_sessions.update_one(
                    {"session_id": body.session_id, "user_id": user_id},
                    {"$unset": {"pending_fix_task": ""}},
                )
                await q.put({"type": "mode", "mode": "D"})
                reply = (
                    "👆 Scroll up to my diagnosis bubble and click "
                    "the **🚀 Approve the fix** button — that's the "
                    "only path that commits the fix. I never "
                    "auto-ship; every commit needs your explicit "
                    "click so you stay in control."
                )
                return {
                    "ok": True, "content": reply,
                    "provider": "mode-d-redirect",
                    "fallback_chain": ["mode_d_redirect"],
                    "iterations": 1, "tool_calls_run": 0,
                    "tool_invocations": [],
                    "mode": "D",
                }
    return None


async def _mode_broadcast(state: StreamState) -> str:
    """Decide A/B/C/D/E/F once and broadcast to frontend so the UI
    can show the live pill before tokens stream. 2026-08-27 · reuse
    the mode already computed for the council-recall call above
    (`_recall_mode`) instead of classifying the same prompt twice;
    recompute only if that block was skipped (ora_panel=true) or
    didn't run."""
    body, q = state.body, state.q
    _mode = state.recall_mode if state.recall_mode is not None else classify_intent(body.prompt or "", body.f12_payload)
    # Confidence scoring — surfaces a `mode_confirm` event when the
    # message is ambiguous so the UI can ask the user before burning
    # an LLM call on the wrong mode. Honoured only when the user has
    # NOT explicitly overridden via body.mode_override (mode_override
    # skips confirm).
    try:
        from services.mode_classifier import classify_intent_v2
        _conf = classify_intent_v2(body.prompt or "", body.f12_payload)
    except Exception:
        _conf = None
    if _conf:
        await q.put({
            "type": "mode",
            "mode": _mode,
            "confidence": _conf["confidence"],
            "scores": _conf["scores"],
            "needs_confirm": _conf["needs_confirm"]
                and not getattr(body, "mode_override", None),
        })
        # Fire-and-forget telemetry — keeps a rolling window of the
        # last 100 classifications so we can tune the vocabulary
        # against real-world ambiguity. Failures MUST NOT block the
        # chat path.
        try:
            from services.mode_classifier import log_classification
            _ = asyncio.create_task(
                log_classification(get_db(), _conf, body.prompt or "")
            )
        except Exception:
            pass
    else:
        await q.put({"type": "mode", "mode": _mode})

    # Ops-intent signal — surfaces a deep-link to /admin/ops when the
    # user asks for a server operation AUREM can't execute on their
    # infra (e.g. "restart supervisor", "free disk space"). Avoids
    # ORA fabricating bash.
    try:
        from services.mode_classifier import looks_like_ops_request
        if looks_like_ops_request(body.prompt or ""):
            await q.put({"type": "ops_redirect",
                         "url": "/admin/ops",
                         "reason": "This is a server operation. "
                                   "AUREM can't run commands on "
                                   "your infrastructure — open "
                                   "the Ops Recipes for copy-paste "
                                   "runbooks."})
    except Exception:
        pass
    return _mode


async def _mode_d_e(state: StreamState, _mode: str) -> Optional[dict]:
    """Mode D — debug session (READ → DIAGNOSE → CONFIRM → fix).
    Mode E — full repo audit (REPORT only, no commit). Kept as one
    cohesive unit (shared repo/PAT resolution setup)."""
    import time
    if _mode not in ("D", "E"):
        return None
    from services.mode_d_debugger import run_debug_session
    from services.mode_e_auditor  import run_audit

    body, user_id, q, activity = state.body, state.user_id, state.q, state.activity

    db_h     = get_db()
    repo_own = ""
    repo_nm  = ""
    branch_h = "main"
    project  = None
    if db_h is not None and body.project_id and body.project_id != "home":
        project = await db_h.cto_projects.find_one(
            {"project_id": body.project_id, "user_id": user_id}
        )
        if project:
            repo_own = project.get("github_owner", "")
            repo_nm  = project.get("github_repo", "")
            branch_h = project.get("branch", "main")
    pat = None
    try:
        # 2026-02-11 · Phase 3b (Bug 2 fix) — get_repo_token
        # dispatches on project.auth_method so App-installed
        # projects mint a fresh installation token; PAT rows
        # decrypt normally (Iter 204 fix retained inside the
        # helper).
        # 2026-06 PAT-removal — App-only, no fallback.
        from services.pat_vault import (
            get_repo_token_or_error,
        )
        pat, _auth_err, _auth_detail = (
            await get_repo_token_or_error(project or {})
        )
        if _auth_err:
            logger.warning(
                "mode D/E: App auth failed (%s)", _auth_err)
    except Exception:
        pat = None

    if _mode == "D":
        activity["label"] = "diagnosing error…"
        try:
            d_result = await run_debug_session(
                db=db_h,
                user_message=body.prompt or "",
                repo_owner=repo_own,
                repo_name=repo_nm,
                repo_ctx=f"{repo_own}/{repo_nm}" if repo_own else "no-repo",
                user_id=user_id,
                project_id=body.project_id,
                f12_payload=body.f12_payload,
                github_pat=pat,
            )
        except Exception as _de:
            # 2026-08-25 — same root-cause fix as the
            # cto_projects.py task pipeline: never embed a
            # raw exception string in a reply the user sees.
            from services.error_classifier import classify_error
            d_result = {
                "ora_reply": f"Couldn't diagnose: {classify_error(_de)['user_message']}",
                "can_auto_fix": False, "commit_task": "",
                "severity": "unknown", "fast_path_used": False,
            }
        # Persist pending fix (so a "yes fix it" reply triggers Mode C)
        if d_result.get("can_auto_fix") and body.session_id and db_h is not None:
            try:
                # SEC-002 fix (audit 2026-01-22): scope to the
                # authenticated owner and drop upsert=True so a
                # caller can't write into / create another
                # user's session doc by supplying their id.
                await db_h.chat_sessions.update_one(
                    {"session_id": body.session_id, "user_id": user_id},
                    {"$set": {"pending_fix_task": d_result["commit_task"],
                              "pending_fix_set_at": time.time()}},
                )
            except Exception:
                pass
        return {
            "ok": True,
            "content":  d_result.get("ora_reply", ""),
            "provider": "mode-d-debugger",
            "fallback_chain": ["mode_d"],
            "iterations": 1, "tool_calls_run": 0,
            "tool_invocations": [], "mode": "D",
            "can_auto_fix": d_result.get("can_auto_fix", False),
            "severity": d_result.get("severity", "medium"),
            "fast_path_used": d_result.get("fast_path_used", False),
        }

    # Mode E — audit
    activity["label"] = "scanning repo…"
    file_blocks: dict = {}
    file_tree:   list = []
    if pat and repo_own and repo_nm:
        try:
            from services.github_api_writer import fetch_file as _gh_fetch
            import httpx as _httpx
            # Pull file tree directly from the git tree endpoint
            # (one round-trip — much lighter than full repo_context).
            async with _httpx.AsyncClient(timeout=20.0) as _gc:
                _r = await _gc.get(
                    f"https://api.github.com/repos/{repo_own}/{repo_nm}/git/trees/{branch_h}?recursive=1",
                    headers={"Authorization": f"Bearer {pat}",
                             "Accept": "application/vnd.github+json"},
                )
                if _r.status_code == 200:
                    _tree = (_r.json() or {}).get("tree", []) or []
                    file_tree = [
                        t.get("path", "")
                        for t in _tree
                        if t.get("type") == "blob" and t.get("path")
                    ][:400]
            # Read the top ~8 most-relevant files for the audit
            _prio = [
                p for p in file_tree
                if any(p.endswith(ext) for ext in
                       (".py", ".js", ".jsx", ".ts", ".tsx"))
                and ("router" in p or "service" in p
                     or "model" in p or "main" in p
                     or "App" in p or "index" in p)
            ][:8] or file_tree[:8]
            async with _httpx.AsyncClient(timeout=20.0) as _gc:
                for _p in _prio:
                    _content = await _gh_fetch(
                        _gc, repo_own, repo_nm, _p, branch_h, pat,
                    )
                    if _content:
                        file_blocks[_p] = _content
        except Exception:
            pass
    try:
        e_result = await run_audit(
            db=db_h,
            repo_ctx=f"{repo_own}/{repo_nm}" if repo_own else "no-repo",
            file_blocks=file_blocks,
            file_tree=file_tree,
            user_message=body.prompt or "",
            user_id=user_id,
            project_id=body.project_id,
        )
    except Exception as _ee:
        e_result = {"report": f"Couldn't audit: {_ee}",
                    "critical_count": 0, "high_count": 0,
                    "fixable_tasks": []}
    # 2026-08-27 · P1/P2 (Journey/Intent-Grounding build round) —
    # persist this scan's concrete findings onto the chat session so
    # a later bare "yes"/"ship it" reply can be resolved to THIS
    # proposal's exact scope instead of being judged ambiguous on its
    # own words, and so the plan the loop generates from it can be
    # validated against real file+line citations instead of a
    # re-derivation from prose. See services/intent_grounding.py +
    # plan_scan_contract.py.
    if db_h is not None and body.session_id:
        _findings = [
            {"filepath": i.get("filepath"), "line": i.get("line"),
             "description": i.get("description") or i.get("message", ""),
             "severity": i.get("severity", "low"),
             "fix": i.get("fix", "")}
            for i in (e_result.get("all_issues") or [])
            if i.get("filepath")
        ]
        if _findings:
            try:
                # 2026-08-27 · P6 live-drive fix — a scan is almost
                # always the session's FIRST-EVER turn; the
                # `chat_sessions` doc doesn't exist yet at this point
                # (it's only created afterwards by `_persist_turn`'s
                # own upsert). Without `upsert=True` here, this
                # update silently no-ops on a nonexistent doc —
                # pending_scan was NEVER actually written, so every
                # "yes" after a first-turn scan hit the ambiguity
                # gate instead of resolving. Confirmed live via a
                # real GitHub-App-installed drill repo.
                await db_h.chat_sessions.update_one(
                    {"session_id": body.session_id, "user_id": user_id},
                    {"$set": {"pending_scan": {
                        "findings":   _findings,
                        "project_id": body.project_id,
                        "created_at": time.time(),
                    }},
                     "$setOnInsert": {
                        "session_id": body.session_id,
                        "user_id":    user_id,
                        "created_at": time.time(),
                        "project_id": body.project_id,
                     }},
                    upsert=True,
                )
            except Exception:
                pass
    return {
        "ok": True,
        "content":  e_result.get("report", ""),
        "provider": "mode-e-auditor",
        "fallback_chain": ["mode_e"],
        "iterations": 1, "tool_calls_run": 0,
        "tool_invocations": [], "mode": "E",
        "critical_count": e_result.get("critical_count", 0),
        "high_count":     e_result.get("high_count", 0),
        "fixable_tasks":  e_result.get("fixable_tasks", []),
    }


async def _mode_b(state: StreamState, _mode: str) -> Optional[dict]:
    """Iter 81 — Mode B auto-upgrade: Decision Council. Only fires
    when classifier picked Mode B AND the message has genuine
    stuck-decision signals. Regular Mode B advice (e.g. "should I
    add caching") falls through to the orchestrator below."""
    if _mode != "B":
        return None
    from services.mode_b_council import is_council_request, run_council
    body, activity = state.body, state.activity
    if not is_council_request(body.prompt or "", _mode):
        return None
    activity["label"] = "convening the council…"
    try:
        council_md = await run_council(
            prompt=body.prompt or "",
            repo_ctx=state.repo_ctx or "",
            brain_ctx=state.brain_ctx or "",
        )
    except Exception as _ce:
        logger.exception("mode B council failed")
        council_md = (
            f"_(Council failed: {_ce}. Try again or "
            "rephrase the decision more concretely.)_"
        )
    return {
        "ok": True,
        "content":  council_md,
        "provider": "mode-b-council",
        "fallback_chain": ["mode_b_council"],
        "iterations": 1, "tool_calls_run": 0,
        "tool_invocations": [], "mode": "B",
        # 2026-08-27 · P5 — was `True` (a bare boolean), which
        # MessageBubble.jsx's scope badge interpolates directly into
        # visible copy (`Council ${m.council}`), rendering the
        # literal text "via Council true". A real label fixes it at
        # the source.
        "council": "B",
    }


async def _mode_f(state: StreamState, _mode: str) -> Optional[dict]:
    """Iter 60 — Mode F (Engage / Market). Token-cheap single LLM
    call routed through mode_f_engage. We pass the already-built
    repo + brain context so the LLM can ground market advice in what
    the user is actually shipping. No tool loop, no max-iters budget
    burn."""
    if _mode != "F":
        return None
    body, activity = state.body, state.activity
    activity["label"] = "thinking about positioning…"
    from services.mode_f_engage import run_engage
    try:
        engage_content = await run_engage(
            prompt=body.prompt or "",
            repo_ctx=state.repo_ctx or "",
            brain_ctx=state.brain_ctx or "",
        )
    except Exception as _fe:
        logger.exception("mode F engage failed")
        engage_content = (
            f"_(Engage mode failed: {_fe}. Try again, or "
            "ask the question more directly.)_"
        )
    return {
        "ok": True,
        "content":  engage_content,
        "provider": "mode-f-engage",
        "fallback_chain": ["mode_f"],
        "iterations": 1, "tool_calls_run": 0,
        "tool_invocations": [], "mode": "F",
    }


async def _ora_agent_cascade(state: StreamState) -> Optional[dict]:
    """Iter 38 / Iter 212m-21 — ORA branch (Ask Advisor). Was:
    aurem.live's hosted ORA model (call_ora upstream). Now: routed
    through the locally-hosted GLM-5.2 (`z-ai/glm-5.2`) via
    OpenRouter — same model as Swift mode (see
    services/llm.py::_call_glm, Iter 212m-18) so there's a single
    source of truth for the primary LLM and we don't pay for the
    aurem.live indirection. Returns None if the whole cascade is
    exhausted (fall through to the orchestrator path)."""
    body, activity, q = state.body, state.activity, state.q
    if (body.agent or "auto").lower() != "ora":
        return None
    from services.llm import (
        _call_glm, _call_claude, _call_deepseek,
        _call_deepseek_direct, _call_groq, _GLM_MODEL,
        cap_for, temperature_for,
    )
    # Iter 212m-53 — Ask Advisor dedicated config (admin-set). Read
    # the dedicated prompt + LLM choice; fall back to the legacy
    # ORA_PANEL_TONE + GLM-5.2 when unset.
    try:
        from services.house_rules import (
            get_active_advisor_prompt,
            get_active_advisor_llm,
            format_house_rules_block,
        )
        _adv_prompt = await get_active_advisor_prompt()
        _adv_llm    = await get_active_advisor_llm()
    except Exception as _hr_e:
        logger.debug("advisor house_rules read failed: %r", _hr_e)
        _adv_prompt, _adv_llm = "", "glm-5.2"
    activity["label"] = f"asking {_adv_llm}…"
    # Ask Advisor voice / verification rules go on top of the
    # project context (`extra_sys`) so the model has the same
    # persona discipline the upstream had plus full repo/brain
    # awareness. When the admin has set a dedicated advisor prompt,
    # inject it as the FIRST block (highest priority).
    extra_sys = state.extra_sys
    _adv_header = (
        format_house_rules_block(_adv_prompt) + "\n\n"
        if _adv_prompt else ""
    )
    ora_system = (
        _adv_header
        + (extra_sys + "\n\n" if extra_sys else "")
        + ORA_PANEL_TONE
    ).strip()

    # Iter 212m-161 — Ask Advisor multi-model cascade. Primary =
    # admin-selected LLM. On error / empty:
    #   Groq llama-3.3-70b (FREE) rescue → DeepSeek V3 (cheap)
    #   last-resort.  Whichever primary the admin picked, we never
    #   re-call it as its own rescue — `_seen` short-circuits
    #   self-rescue.
    # Cost-tier rationale per founder: Claude is too expensive for
    # advisor rescue; Groq is free, DeepSeek is cheap.
    _adv_max_tokens  = cap_for("advisor")
    _adv_temperature = temperature_for("advisor")
    _adv_call_kwargs = dict(
        system=ora_system,
        user=body.prompt,
        max_tokens=_adv_max_tokens,
        temperature=_adv_temperature,
    )

    async def _adv_primary(llm_id: str) -> tuple:
        """Returns (content, provider_tag).  Raises on upstream
        errors so the outer cascade can rescue."""
        if llm_id == "claude-sonnet-4.5":
            return await _call_claude(**_adv_call_kwargs), "claude-sonnet-4.5"
        if llm_id == "deepseek-chat":
            return await _call_deepseek(
                messages=[{"role": "user", "content": body.prompt}],
                system=ora_system,
                max_tokens=_adv_max_tokens,
                temperature=_adv_temperature,
            ), "deepseek-chat"
        if llm_id == "deepseek-direct":
            return await _call_deepseek_direct(
                messages=[{"role": "user", "content": body.prompt}],
                system=ora_system,
                max_tokens=_adv_max_tokens,
                temperature=_adv_temperature,
            ), "deepseek-direct"
        if llm_id == "groq-llama-3.3-70b":
            return await _call_groq(
                messages=[{"role": "user", "content": body.prompt}],
                system=ora_system,
                max_tokens=_adv_max_tokens,
                temperature=_adv_temperature,
            ), "groq-llama-3.3-70b"
        # "glm-5.2" (default) or any unrecognised value.
        return await _call_glm(**_adv_call_kwargs), "glm-5.2"

    glm_text = ""
    _adv_model_tag = ""
    _adv_chain: list = []
    _adv_fatal_err: Optional[Exception] = None
    try:
        _step(q, "🤔 Thinking…")
        # ── Step 1: PRIMARY (admin-selected) ──
        try:
            glm_text, _adv_model_tag = await _adv_primary(_adv_llm)
            _adv_chain.append(_adv_model_tag)
        except Exception as _p_err:
            logger.warning(
                "advisor primary %s failed: %r — trying Groq rescue",
                _adv_llm, _p_err,
            )
            _adv_chain.append(f"{_adv_llm}-error")
            glm_text = ""
        # ── Step 2: GROQ FREE RESCUE ──
        # Fire when primary returned empty OR raised, except when
        # primary IS Groq (no self-rescue).
        if not (glm_text or "").strip() and _adv_llm != "groq-llama-3.3-70b":
            try:
                activity["label"] = "trying backup model…"
                _step(q, "⚙️ Switching to backup model…")
                glm_text = await _call_groq(
                    messages=[{"role": "user", "content": body.prompt}],
                    system=ora_system,
                    max_tokens=_adv_max_tokens,
                    temperature=_adv_temperature,
                )
                if (glm_text or "").strip():
                    _adv_model_tag = "groq-llama-3.3-70b-rescue"
                    _adv_chain.append("groq-llama-3.3-70b")
            except Exception as _g_err:
                logger.warning(
                    "advisor Groq rescue failed: %r — trying DeepSeek",
                    _g_err,
                )
                _adv_chain.append("groq-error")
                glm_text = ""
        # ── Step 3: DEEPSEEK V3 LAST-RESORT ──
        if not (glm_text or "").strip() and _adv_llm not in (
            "deepseek-chat", "deepseek-direct",
        ):
            try:
                activity["label"] = "trying backup model…"
                _step(q, "⚙️ Switching to backup model…")
                glm_text = await _call_deepseek(
                    messages=[{"role": "user", "content": body.prompt}],
                    system=ora_system,
                    max_tokens=_adv_max_tokens,
                    temperature=_adv_temperature,
                )
                if (glm_text or "").strip():
                    _adv_model_tag = "deepseek-v3-rescue"
                    _adv_chain.append("deepseek-v3")
            except Exception as _d_err:
                logger.error(
                    "advisor DeepSeek last-resort failed: %r — "
                    "falling through to orchestrator",
                    _d_err,
                )
                _adv_fatal_err = _d_err
                _adv_chain.append("deepseek-error")

        if not (glm_text or "").strip():
            # All three real models exhausted — fall through to the
            # orchestrator path (legacy safety net) so the user
            # never sees a blank reply.
            raise _adv_fatal_err or RuntimeError(
                "advisor full cascade returned empty"
            )
        _step(q, "✅ Done", True)
        return {
            "ok":              True,
            "content":         glm_text,
            "provider":        _adv_model_tag,
            "model":           _adv_model_tag,
            "fallback_chain":  _adv_chain,
            "iterations":      1,
            "tool_calls_run":  0,
            "tool_invocations": [],
            "mode":            "ora",
        }
    except Exception as glm_err:
        # Full advisor cascade exhausted — fall through to the
        # orchestrator path below as a last-of-last resort so the
        # user still gets a reply.
        logger.info(
            "ora advisor cascade exhausted (chain=%s, last=%r) — "
            "falling back to orchestrator", _adv_chain, glm_err,
        )
        activity["label"] = (
            "advisor models unavailable — switching to AUREM…"
        )
        # Fall through to the AUREM/orchestrator path below.
        return None


async def _prep_pre_llm(state: StreamState) -> dict:
    """2026-09-06 · Phase 1 chat.py refactor (the "load-bearing
    dedup") — this ONE call replaces what used to be 5 independently
    hand-copied pre-LLM checks (confirm-boundary / intent-classify /
    upgrade-offer / self-bug / casual-direct-reply-or-honest-no-op).
    chat_send calls the exact same function — see
    routers/chat_pre_llm.py's module docstring for the full
    rationale and the pre-existing asymmetries this refactor
    intentionally preserves rather than silently "fixing".

    Returns a dict: {"short_circuit": Optional[dict], "intent_result",
    "tier", "req_mode_stream", "max_iters_eff", "published"} — a
    non-None "short_circuit" means the caller must `q.put` it as the
    result and return immediately, same as the original code."""
    body, user, user_id, bin_ctx, activity, q = (
        state.body, state.user, state.user_id, state.bin_ctx,
        state.activity, state.q,
    )
    activity["label"] = "thinking…"
    from services.subscription_tiers import allowed_modes_for_tier as _allowed_modes
    _allowed_s = _allowed_modes((user or {}).get("tier") or "free")
    req_mode_stream = body.mode if (body.mode in _allowed_s) else _allowed_s[-1]
    # Hook to publish tool invocations live so the timeout guard can
    # summarise what we managed to inspect.
    _published: list = []
    activity["invocations"] = _published
    # Iter 212m-18 — Steps queue. Initial 🤔 frame so the UI
    # immediately moves off the generic "thinking…" tick.
    _step(q, "🤔 Thinking…")

    # 2026-08-24 — Guard 22 fix: strip the internal `LOOP_PHASE:plan\n`
    # marker before classification — see routers/chat_pre_llm.py's
    # `intent_probe_text` param (this asymmetry with chat_send is
    # pre-existing and intentionally preserved, not silently unified).
    _intent_probe_text = re.sub(
        r"^LOOP_PHASE:\w+\s*\n", "", body.prompt or "", count=1,
    )
    from services.response_confidence import prior_turn_had_fix_signal as _ptfs_stream
    from services.response_confidence import prior_turn_context_text as _ptct_stream
    from services.response_confidence import get_session_summary as _gss_stream
    _prior_fix_signal = await _ptfs_stream(get_db(), body.session_id, user_id)
    _prior_turn_text = await _ptct_stream(get_db(), body.session_id, user_id)
    _session_summary = await _gss_stream(get_db(), body.session_id, user_id)
    from routers.chat_pre_llm import resolve_pre_llm
    _pre = await resolve_pre_llm(
        db=get_db(), user=user, body=body, bin_ctx=bin_ctx,
        prior_fix_signal=_prior_fix_signal, prior_turn_text=_prior_turn_text,
        session_summary=_session_summary,
        allowed_modes=_allowed_s, req_mode=req_mode_stream,
        run_confirm_boundary=not body.ora_panel,
        ora_panel=bool(body.ora_panel),
        intent_probe_text=_intent_probe_text,
        log_ctx={"db": get_db(), "user_id": user_id, "project_id": body.project_id},
    )
    _intent_result = _pre.intent_result
    _tier = _pre.tier
    req_mode_stream = _pre.mode
    # Emit an SSE `intent` frame so the chat UI can render the tier
    # dot + clarifying probe inline — same position (right after
    # classification, before any short-circuit result) as before
    # this refactor.
    await q.put({
        "type":   "intent",
        "intent": _intent_result,
    })
    if _pre.result is not None:
        return {"short_circuit": _pre.result}

    if _tier == "query":
        # Iter 388k — Bug 12 fix. Bumped from 2 → 3.  At 2 iters a
        # simple "read this file and show me lines 1-50" got
        # EXHAUSTED whenever the model made a 2nd exploratory tool
        # call (list_repo_files after read_repo_file), and the
        # founder saw the "send the same prompt again" loop template
        # with no actual content.  3 iters + the last-round
        # `final_answer_now` directive below gives the model one
        # guaranteed round to summarise.
        _max_iters_eff = 3
    else:
        # Agentic, or casual/clarify falling back here only because
        # the cheap direct-LLM call above raised — full pipeline as
        # a fail-open safety net so the user is never left with
        # nothing.
        _max_iters_eff = min(max(body.max_tool_iters, 4), 6)

    return {
        "short_circuit": None,
        "intent_result": _intent_result,
        "tier": _tier,
        "req_mode_stream": req_mode_stream,
        "max_iters_eff": _max_iters_eff,
        "published": _published,
    }


async def _advisor_panel(state: StreamState, intent_result: dict, tier: str) -> dict:
    """Ask-Advisor-panel (`body.ora_panel=true`) direct-LLM path —
    bypasses `chat_with_tools` entirely (Iter 212m-211 root-cause
    fix for tool_call fence leakage / "same prompt phir bhejo").
    ALWAYS returns a result dict (success or a graceful, self-
    contained fallback) — never raises, never falls through to the
    orchestrator. Kept as one cohesive ~340-line unit per the
    founder-approved 600-800 line allowance (module docstring):
    the ADVISOR CONTEXT block, screenshot-vision block and directive
    are all threaded into ONE `_sys_for_advisor` string that only
    makes sense read top-to-bottom; splitting it further would mean
    passing ~10 more locals across yet another module boundary for
    no behavioural gain."""
    body = state.body
    _ctx_block = ""
    if body.project_id:
        try:
            from routers.advisor_context import get_advisor_context
            _ctx = await get_advisor_context(
                project_id=body.project_id,
                authorization=state.authorization,
            )
            _role = _ctx.get("role") or "user"
            _is_founder_view = (_role == "founder")

            # Base block — same for everyone.
            _lines = [
                "\n\n=== ADVISOR CONTEXT (READ-ONLY, LIVE) ===",
                f"Project: {_ctx.get('project_name')}  (id={_ctx.get('project_id')})",
                f"Viewer role: {_role}",
                (
                    f"Findings: total={_ctx['findings'].get('total')}  "
                    f"P0={_ctx['findings'].get('p0')}  P1={_ctx['findings'].get('p1')}  P2={_ctx['findings'].get('p2')}"
                    + (f"  (err: {_ctx['findings'].get('error')})" if _ctx['findings'].get('error') else "")
                ),
                (
                    f"Quota: used={_ctx['quota'].get('tokens_used')}  "
                    f"limit={_ctx['quota'].get('tokens_limit')}  "
                    f"period={_ctx['quota'].get('period')}"
                ),
            ]
            # Iter 388j — Recent tasks block (Bug 3 fix).
            _rt = (_ctx.get("recent_tasks") or {})
            _items = _rt.get("items") or []
            if _items:
                _lines.append("Recent tasks (last 5, newest first):")
                for _it in _items:
                    _st = _it.get("status") or "?"
                    _sh = (_it.get("sha") or "")[:7]
                    _sum = (_it.get("summary") or "")[:80]
                    _err = (_it.get("error") or "")[:80]
                    _tail = (
                        f"  sha={_sh}" if _sh else ""
                    ) + (
                        f"  err={_err}" if _err else ""
                    )
                    _lines.append(f"  - [{_st}] {_sum}{_tail}")
            elif _rt.get("error"):
                _lines.append(f"Recent tasks: (fetch error: {_rt['error']})")
            else:
                _lines.append("Recent tasks: none in the last 5 rows")
            # Iter 388j — Open PRs block (Bug 4 fix).
            _pr = (_ctx.get("open_prs") or {})
            _pr_items = _pr.get("items") or []
            if _pr_items:
                _lines.append(f"Open PRs on {_ctx.get('project_name')} (count={_pr.get('count')}):")
                for _p in _pr_items:
                    _lines.append(
                        f"  - #{_p.get('number')} "
                        f"{'[draft] ' if _p.get('draft') else ''}"
                        f"{(_p.get('title') or '')[:120]} "
                        f"— @{_p.get('author')}"
                    )
            elif _pr.get("error"):
                _lines.append(f"Open PRs: (fetch error: {_pr['error']})")
            else:
                _lines.append("Open PRs: 0 open on this repo")
            # Iter 388j — Token breakdown block (Bug 5 fix).
            _tb = (_ctx.get("token_breakdown") or {})
            _rtok = _tb.get("recent_task_tokens") or []
            if _rtok or _tb.get("project_month_total"):
                _lines.append(
                    f"Tokens (this month): project={_tb.get('project_month_total')}, "
                    f"user_all_projects={_tb.get('user_month_total')}"
                )
                if _rtok:
                    _lines.append("Tokens per recent task:")
                    for _t in _rtok:
                        _lines.append(
                            f"  - {_t.get('task_id')} [{_t.get('status')}] "
                            f"{(_t.get('summary') or '')[:60]} → "
                            f"{_t.get('tokens_used')} tokens"
                        )
            elif _tb.get("error"):
                _lines.append(f"Token breakdown: (fetch error: {_tb['error']})")
            else:
                _lines.append("Token breakdown: no completed tasks this month")
            # Tier-2 — founders only. Non-founders never see
            # commit SHAs or council router state in the
            # prompt at all.
            if _is_founder_view and _ctx.get("council"):
                _lines.append(
                    f"Council A: live={_ctx['council'].get('live')}  "
                    f"primary_actual={_ctx['council'].get('primary_actual')}  "
                    f"primary_intended={_ctx['council'].get('primary_intended')}  "
                    f"last_probe={_ctx['council'].get('last_probe')}"
                )
            if _is_founder_view and _ctx.get("deploy_sync"):
                _lines.append(
                    f"Deploy sync: self={_ctx['deploy_sync'].get('self_sha')}  "
                    f"prod={_ctx['deploy_sync'].get('prod_sha')}  "
                    f"in_sync={_ctx['deploy_sync'].get('in_sync')}"
                )
            _lines.append("=========================================\n")

            # Role-shaped rules.  For non-founders we
            # explicitly forbid disclosing infra state —
            # they simply don't have that data in-context
            # and must not fabricate it.
            _rules = [
                "RULES:",
                f"1. EVERY response MUST mention the project name '{_ctx.get('project_name')}' explicitly.",
                "2. If any field above is null/None/error, respond 'yeh data abhi available nahi hai' — NEVER guess.",
                "3. Only cite numbers that appear in this block. Do not extrapolate.",
            ]
            if not _is_founder_view:
                _rules.append(
                    "4. INFRA GUARD: If the user asks about council routing, LLM provider, "
                    "deploy sync, commit SHA, production vs preview, or any system-health "
                    "internals, reply generically: 'systems abhi normally operating hain — "
                    "infra-level details Ask Advisor par exposed nahi hote. Founder access "
                    "chahiye toh admin se contact karo.'  Do NOT invent numbers, SHAs, or "
                    "provider names.  Do NOT admit whether council is live or degraded."
                )
            _ctx_block = "\n".join(_lines) + "\n" + "\n".join(_rules)
        except Exception as _cxe:
            _ctx_block = f"\n\n[Advisor context fetch failed: {str(_cxe)[:80]}] — reply with 'yeh data abhi available nahi hai' for any data-dependent question."

    # Iter 212m-212 — Optional client-side screenshot.
    # Isolated from the main text path: any failure here
    # (decode, oversize, vision-API down, key missing)
    # falls through with a small honest note but the
    # text response continues.  We NEVER re-raise from
    # this block; the pattern mirrors the Suggestion
    # Box's Groq sidecar.
    #
    # Iter 388j — DATA-CHIP CARVEOUT.  Three Advisor
    # chips ("Diagnose failed run", "Summarize open
    # PRs", "Token breakdown") are DATA questions, not
    # UI questions.  For those we deliberately SKIP
    # the screenshot vision block — otherwise the
    # vision-derived text (which often shows stale
    # scrollback) hijacks the LLM and overrides the
    # real structured data now in ADVISOR CONTEXT.
    _prompt_head = ((body.prompt or "").strip().lower())[:64]
    _is_data_chip = _prompt_head in (
        "diagnose failed run",
        "summarize open prs",
        "token breakdown",
    )
    _vision_block = ""
    _vision_status = "not_requested"
    if body.screenshot_b64 and not _is_data_chip:
        import base64 as _b64
        try:
            # Accept both raw base64 and data-URI prefixes.
            _raw = body.screenshot_b64
            if _raw.startswith("data:"):
                _raw = _raw.split(",", 1)[-1]
            _png = _b64.b64decode(_raw, validate=False)
            if len(_png) < 1024:
                raise ValueError("decoded image too small")
            if len(_png) > 8 * 1024 * 1024:
                raise ValueError("decoded image over 8MB cap")
            from services.advisor_vision import (
                analyze_screenshot,
            )
            _desc = await asyncio.wait_for(
                analyze_screenshot(_png, body.prompt or ""),
                timeout=14.0,
            )
            if _desc:
                _vision_block = (
                    "\n\n=== SCREENSHOT ANALYSIS "
                    "(user's current screen, vision "
                    "model) ===\n"
                    + _desc.strip()
                    + "\n"
                    "=========================================\n"
                    "When answering, ground concrete UI "
                    "observations in the SCREENSHOT "
                    "ANALYSIS above.  Do not describe "
                    "elements it did not mention."
                )
                _vision_status = "ok"
            else:
                _vision_status = "vision_null"
        except asyncio.TimeoutError:
            _vision_status = "vision_timeout"
        except Exception as _vex:
            _vision_status = f"vision_err_{type(_vex).__name__}"
    if _vision_status not in ("not_requested", "ok"):
        # ERROR-level so founder monitoring picks it
        # up; user sees ZERO indication of the failure
        # (silent fallback per Iter 212m-213 directive).
        logger.error(
            "advisor_vision_failed: status=%s (user=%s, "
            "project=%s, prompt_head=%s)",
            _vision_status, state.user_id, body.project_id,
            (body.prompt or "")[:60],
        )
        # _vision_block stays empty — advisor just
        # answers text-only, no note about the missing
        # visual.  Same UX as if the user never sent
        # a screenshot in the first place.
    logger.info(
        "advisor_vision: status=%s (user=%s, project=%s)",
        _vision_status, state.user_id, body.project_id,
    )
    _adv_directive = (
        "\n\nYOU ARE THE ASK ADVISOR PANEL. "
        "Answer the user's question directly from what "
        "you already know about this workspace and the "
        "ADVISOR CONTEXT block below.  You have NO "
        "tools this turn — do not attempt to call "
        "`list_repo_files`, `read_repo_file`, "
        "`search_repo`, or any other tool; those "
        "requests will be dropped.  Do not ask the "
        "user to narrow their question.  Do not say "
        "you ran out of time or ask them to resend "
        "the prompt.  If the question is ambiguous, "
        "answer the most likely interpretation and "
        "note the assumption in one line.  Reply in "
        "plain prose only — no ```tool_call``` fences, "
        "no JSON blocks."
        # Iter 388j — HARD DATA-HONESTY RULE.  Applies
        # to Bug 3+4+5 fabrication where the LLM was
        # treating SCREENSHOT ANALYSIS as authoritative
        # for run/PR/token state.  This rule wins
        # against the visual-context rule below and
        # against any admin house-rule that says
        # otherwise.  Structured data > vision text,
        # always, for DATA questions.
        "\n\nDATA HONESTY (highest priority):"
        "\n1. For run status, task history, open PRs, "
        "and token usage: ONLY cite values from the "
        "ADVISOR CONTEXT block above (Recent tasks / "
        "Open PRs / Tokens fields).  NEVER derive "
        "these facts from SCREENSHOT ANALYSIS — "
        "screenshot text is often stale scrollback "
        "from earlier failed loops."
        "\n2. If the required structured field is "
        "empty, null, or carries an `error:` note, "
        "reply exactly: 'yeh data abhi available "
        "nahi hai — <field name>' and stop.  Do NOT "
        "extrapolate."
        "\n3. When you name the project, use the "
        "'Project:' line from ADVISOR CONTEXT verbatim.  "
        "Do not use any other name (especially not one "
        "that appears only in the screenshot)."
        "\n4. Numbers you cite (counts, SHAs, PR "
        "numbers, token totals) MUST appear verbatim "
        "in ADVISOR CONTEXT.  If a number would help "
        "your answer but isn't in ADVISOR CONTEXT, "
        "say 'don't have that number' — don't invent."
        # Iter 212m-213 — Visual grounding rule.
        "\n\nVISUAL CONTEXT RULE: If a SCREENSHOT "
        "ANALYSIS block appears below, the user IS "
        "currently looking at that screen.  For UI-"
        "layout questions ('where is X?', 'what does "
        "this button do?', 'why is this looking "
        "weird?'), answer with SPATIAL SPECIFICITY "
        "grounded in the analysis — e.g. 'the orange "
        "button in the top-right corner labelled Start "
        "Free' — NEVER reply with 'mujhe nahi pata' "
        "or 'I can't see your screen' when the "
        "analysis block IS present.  If the analysis "
        "block is MISSING, answer the text question "
        "normally without mentioning screenshots at "
        "all.  IMPORTANT: the DATA HONESTY rule above "
        "takes precedence — do not describe screenshot "
        "text as if it were run status, PR state, or "
        "token data."
    )
    _sys_for_advisor = (state.extra_sys or "") + _adv_directive + _ctx_block + _vision_block
    state.sys_for_advisor = _sys_for_advisor

    # Direct LLM call — bypass the tool loop entirely.
    # Mirrors the intent-gateway `casual` path above so
    # the SSE result envelope stays identical to every
    # other mode (worker downstream reads
    # `result["content"]`).
    try:
        from services.llm import call_llm as _call_llm_adv
        _adv_reply = await _call_llm_adv(
            [{"role": "user", "content": body.prompt or ""}],
            system=_sys_for_advisor,
            max_tokens=800,
            temperature=0.4,
        )
        # Belt-and-braces: even a direct call can rarely
        # emit a fence if the model has been heavily
        # RLHF'd toward tool use.  Strip once here so
        # nothing raw can ever reach the UI bubble.
        from services.orchestrator import strip_tool_calls
        _adv_reply_clean = strip_tool_calls(_adv_reply or "").strip()
        if not _adv_reply_clean:
            _adv_reply_clean = (
                "Hmm — I couldn't put together a useful reply "
                "from what I have loaded right now.  Try "
                "asking a more specific question (e.g. "
                "'what open findings do I have?' or "
                "'am I close to my token limit?')."
            )
        return {
            "ok":               True,
            "content":          _adv_reply_clean,
            "provider":         "advisor-direct",
            "fallback_chain":   ["advisor_direct"],
            "iterations":       1,
            "tool_calls_run":   0,
            "tool_invocations": [],
            "intent":           intent_result,
            "tier":             tier,
            "mode":             "chat",
            "ora_panel":        True,
        }
    except Exception as _adv_e:
        # On direct-call failure DO NOT fall through to
        # `chat_with_tools` — that's the exact path we
        # want to keep the advisor away from.  Return a
        # graceful, self-contained message instead.
        logger.warning(
            "advisor direct-LLM path failed (%r) — "
            "returning graceful fallback (NOT falling "
            "through to orchestrator)", _adv_e,
        )
        return {
            "ok":               True,
            "content":          (
                "Advisor abhi thoda slow hai — ek moment "
                "mein retry karo. (No infra was touched.)"
            ),
            "provider":         "advisor-direct-fallback",
            "fallback_chain":   ["advisor_direct", "graceful"],
            "iterations":       0,
            "tool_calls_run":   0,
            "tool_invocations": [],
            "intent":           intent_result,
            "tier":             tier,
            "mode":             "chat",
            "ora_panel":        True,
        }


async def _run_orchestrator(state: StreamState, req_mode_stream: str, max_iters_eff: int,
                             _published: list, intent_result: dict, tier: str) -> dict:
    """Default path — full `chat_with_tools` tool-loop cascade,
    plus the ora_panel leak-guard scrub for the (should-never-happen)
    case where a body.ora_panel=true turn reached here instead of
    returning straight from `_advisor_panel()`."""
    body, user_id, jwt_token, bin_ctx, q = (
        state.body, state.user_id, state.jwt_token, state.bin_ctx, state.q,
    )

    def _activity(label: str):
        state.activity["label"] = label

    def _step_hook(text, done=False):
        _step(q, text, done)

    result = await chat_with_tools(
        prompt=body.prompt,
        jwt_token=jwt_token,
        system=(state.sys_for_advisor + "\n\n" if state.sys_for_advisor else None),
        max_iters=max_iters_eff,
        session_id=body.session_id,
        mongo_client=None,
        user_id=user_id,
        project_id=body.project_id,
        activity_hook=_activity,
        live_invocations_ref=_published,
        mode=req_mode_stream,
        step_hook=_step_hook,
        task_type=body.task_type or _infer_task_type(body.prompt),
        is_founder=state.is_founder,
        bin_ctx=bin_ctx,
    )
    # Snapshot final invocations so a late timeout still has data.
    if isinstance(result, dict):
        _published[:] = result.get("tool_invocations") or []
        result["intent"] = intent_result
        result["tier"]   = tier

        # Iter 212m-211 — CODE-LEVEL GUARDRAIL for ora_panel.
        # If we somehow ended up here on an advisor turn
        # (`ora_panel=true`) that means the earlier
        # advisor-direct short-circuit did NOT run —
        # i.e. our restricted path leaked into the full
        # orchestrator path.  That is exactly the class of
        # regression Iter 212m-208/211 was written to
        # prevent.  We (a) log LOUDLY so it shows up in
        # prod monitoring, and (b) scrub the response
        # payload of any tool_call fences AND any
        # tool_invocations before it ships to the UI, so
        # even in the failure mode the user never sees
        # raw ```tool_call``` blocks.
        if body.ora_panel:
            logger.error(
                "advisor_leak_guard: ora_panel=true turn reached "
                "chat_with_tools (provider=%s, tool_calls_run=%s). "
                "Scrubbing response before send.",
                result.get("provider"), result.get("tool_calls_run"),
            )
            try:
                from services.orchestrator import strip_tool_calls
                _scrubbed = strip_tool_calls(
                    result.get("content") or ""
                ).strip()
            except Exception:
                _scrubbed = ""
            result["content"]          = _scrubbed or (
                "Advisor abhi thoda slow hai — ek moment mein retry "
                "karo. (No infra was touched.)"
            )
            result["tool_invocations"] = []
            result["tool_calls_run"]   = 0
            result["provider"]         = "advisor-leak-guard"
    return result


async def run_worker(state: StreamState) -> None:
    """Dispatch coordinator — mirrors the ORIGINAL `_worker()`
    closure's exact if/elif/return cascade (see module docstring for
    the stage list), now driving StreamState instead of closures.
    Every stage below either short-circuits with a result dict (put
    on `state.q` as a `result` event) or falls through to the next
    stage. Always sets `state.stop_event` on the way out so
    watchdog.py's queue consumption loop can't wait forever."""
    q = state.q
    try:
        r = await _mode_d_fast_path(state)
        if r is not None:
            await q.put({"type": "result", "result": r})
            return

        _mode = await _mode_broadcast(state)

        r = await _mode_d_e(state, _mode)
        if r is not None:
            await q.put({"type": "result", "result": r})
            return

        r = await _mode_b(state, _mode)
        if r is not None:
            await q.put({"type": "result", "result": r})
            return

        r = await _mode_f(state, _mode)
        if r is not None:
            await q.put({"type": "result", "result": r})
            return

        r = await _ora_agent_cascade(state)
        if r is not None:
            await q.put({"type": "result", "result": r})
            return

        pre = await _prep_pre_llm(state)
        if pre["short_circuit"] is not None:
            await q.put({"type": "result", "result": pre["short_circuit"]})
            return
        intent_result   = pre["intent_result"]
        tier             = pre["tier"]
        req_mode_stream  = pre["req_mode_stream"]
        max_iters_eff    = pre["max_iters_eff"]
        _published       = pre["published"]

        if state.body.ora_panel:
            r = await _advisor_panel(state, intent_result, tier)
            await q.put({"type": "result", "result": r})
            return

        state.sys_for_advisor = state.extra_sys
        result = await _run_orchestrator(
            state, req_mode_stream, max_iters_eff, _published,
            intent_result, tier,
        )
        await q.put({"type": "result", "result": result})
    except Exception as e:
        logger.exception("chat_stream orchestrator failed")
        await q.put({"type": "error", "error": str(e)})
    finally:
        state.stop_event.set()

