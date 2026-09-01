"""
routers/chat/watchdog.py — SSE queue-consumption + soft/hard timeout
race, extracted out of chat_stream()'s gen() (2026-09-08 StreamState
refactor). Mechanical move — the exact original while-loop body,
now reading/writing StreamState fields instead of closure variables.
See stream_state.py's docstring for the field mapping.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as _t

from .stream_state import StreamState

logger = logging.getLogger(__name__)


async def consume_worker_queue(state: StreamState, worker_t, ticker_t):
    """Async generator yielding SSE `data: ...\\n\\n` lines. On a
    clean `result`/`error` event it sets `state.result` /
    `state.collected_steps` and returns normally (caller continues
    post-processing). On a blown deadline it fully completes the SSE
    stream itself (meta → tokens → persist → done), sets
    `state.timed_out = True`, and returns — caller must check that
    flag and stop immediately without further processing.
    """
    from services.chat_helpers import _persist_turn

    q = state.q
    activity = state.activity
    t_start = state.t_start
    HARD_TIMEOUT_S = state.hard_timeout_s
    SOFT_TIMEOUT_S = state.soft_timeout_s
    body = state.body

    result = None
    collected_steps: list = []
    deadline_at = _t.monotonic() + HARD_TIMEOUT_S
    while True:
        try:
            ev = await asyncio.wait_for(
                q.get(), timeout=max(0.1, deadline_at - _t.monotonic()),
            )
        except asyncio.TimeoutError:
            ev = None  # synthetic timeout — handled below
        # Iter 136 — explicit deadline check.
        # The `_ticker()` task fires every 0.6s and feeds the queue, so
        # wait_for(q.get(), ...) almost always returns before the
        # configured timeout. Result: HARD_TIMEOUT_S was never being
        # enforced — users saw "thinking · 500s" past the 150s budget.
        # Now we treat ANY tick that arrives past the deadline as a
        # timeout, but DON'T throw away a real `result` / `mode` / `error`
        # event just because it raced past the cut-off by a few ms.
        _past_deadline = _t.monotonic() >= deadline_at
        _is_tick = isinstance(ev, dict) and ev.get("type") == "tick"
        # 2026-08-23 audit fix — see SOFT_TIMEOUT_S comment above.
        _tool_count_so_far = len(activity.get("invocations") or [])
        _past_soft_deadline = (
            _t.monotonic() >= (t_start + SOFT_TIMEOUT_S)
            and _tool_count_so_far <= 1
        )
        if ev is None or (_past_deadline and _is_tick) or (_past_soft_deadline and _is_tick):
            # Wall-clock blown. Cancel everything but emit a USEFUL
            # message instead of just an "error" payload — the
            # frontend used to render that red and the user saw
            # nothing actionable. We pull whatever tool history the
            # worker managed to record and stream a real summary.
            worker_t.cancel()
            ticker_t.cancel()
            partial_invocations = list(activity.get("invocations") or [])
            from services.orchestrator import _synthesise_max_iters_summary
            summary = _synthesise_max_iters_summary(
                body.prompt, partial_invocations,
            )
            # RECURRING_ISSUES.md Pattern #2 fix: distinguish slow-API
            # waiting from a genuine reasoning loop. If we made very
            # few tool calls, the time was likely spent waiting on
            # the model API (cold start / OpenRouter queue / network),
            # NOT looping. Telling the user "I cut myself off" in
            # that case is misleading and erodes trust.
            # 2026-08-19 — extracted to orchestrator.build_timeout_message
            # so this decision is directly unit-testable (the old
            # grep-lock test couldn't catch a swapped branch).
            tool_count = len(partial_invocations)
            from services.orchestrator import build_timeout_message
            # 2026-08-23 audit fix — report whichever budget actually
            # fired so the message says "48s" not a misleading "180s"
            # when the soft (proxy-safe) deadline is what triggered.
            _effective_budget = (
                SOFT_TIMEOUT_S if (_past_soft_deadline and not _past_deadline)
                else HARD_TIMEOUT_S
            )
            content, _slow_api = build_timeout_message(
                tool_count, _effective_budget, summary,
            )
            # Stream as a normal assistant turn (meta → tokens → done)
            # so the bubble renders properly instead of going red.
            meta_payload = {
                "meta": True,
                "session_id": body.session_id,
                "provider": "aurem-timeout-guard",
                "mode": "A",
                "temperature": 0.2,
                "thinking_s": round(_t.monotonic() - t_start, 1),
                "tool_calls_run": len(partial_invocations),
                "timed_out": True,
                "slow_api": _slow_api,
            }
            yield f"data: {json.dumps(meta_payload)}\n\n"
            CHUNK = 16
            for i in range(0, len(content), CHUNK):
                yield f"data: {json.dumps({'token': content[i:i+CHUNK]})}\n\n"
                await asyncio.sleep(0.005)
            # Persist the turn so refresh keeps it visible.
            try:
                await _persist_turn(
                    state.user_id, body.session_id or "",
                    body.prompt, content, "aurem-timeout-guard",
                    project_id=body.project_id,
                    steps=collected_steps,
                )
            except Exception:
                logger.exception("timeout persist_turn failed")
            yield (
                "data: " + json.dumps({
                    "done": True,
                    "provider": "aurem-timeout-guard",
                    "session_id": body.session_id,
                    "tokens_remaining": None,
                    "timed_out": True,
                }) + "\n\n"
            )
            state.timed_out = True
            return
        if ev["type"] == "tick":
            yield (
                "data: " + json.dumps({
                    "thinking":    True,
                    "elapsed_s":   ev["elapsed_s"],
                    "activity":    ev["activity"],
                    "invocations": ev.get("invocations") or [],
                }) + "\n\n"
            )
        elif ev["type"] == "mode":
            # Iter 42 — forward classified mode (A/B/C/D/E) to UI so
            # the pill renders BEFORE tokens stream.
            yield f"data: {json.dumps({'type': 'mode', 'mode': ev['mode']})}\n\n"
        elif ev["type"] == "step":
            # Iter 212m-18 — orchestrator phase event ("🤔 Thinking…",
            # "📖 Reading repo…", "✍️ Writing files…", "🚀 Committing…",
            # "✅ Done"). Streamed verbatim — frontend renders these
            # in the live progress strip.
            # Chat UX #4 (Tier 1) — also stash it so we can persist
            # the full sequence once the turn finishes.
            collected_steps.append({
                "text": ev.get("text", ""),
                "done": bool(ev.get("done", False)),
            })
            yield (
                "data: " + json.dumps({
                    "type": "step",
                    "text": ev.get("text", ""),
                    "done": bool(ev.get("done", False)),
                }) + "\n\n"
            )
        elif ev["type"] == "error":
            # Iter 339k — NEVER leak a raw Python exception string
            # into the chat bubble. Convert the error frame into an
            # empty result and fall through to the graceful
            # empty-content fallback below ("I wasn't able to
            # produce a reply…"), which includes a trimmed reason.
            logger.warning("chat_stream worker error frame: %s",
                           str(ev.get("error"))[:300])
            result = {"content": "", "error": str(ev.get("error") or "pipeline error")}
            break
        elif ev["type"] == "intent":
            # Iter 212m-149 — Intent Gateway frame.  UI uses this
            # to render the tier dot (casual/query/agentic) +
            # an optional clarifying probe when ambiguous.
            yield (
                "data: " + json.dumps({
                    "type":   "intent",
                    "intent": ev.get("intent") or {},
                }) + "\n\n"
            )
        elif ev["type"] == "result":
            result = ev["result"]
            break

    state.result = result
    state.collected_steps = collected_steps
    state.timed_out = False
