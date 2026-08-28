"""
services/ora_chat_v2/llm_client.py — Admin ORA Chat rebuild, P2.

Vendor-swappable OpenAI-compatible client. Resolution priority per
role ('chat'/'vision'):
  1. MOCK_LLM=true always wins (deterministic mock, no network).
  2. The admin's active `llm_configs` entry for that role (Settings ->
     Models & LLM — self-serve, no deploy/restart).
  3. Env fallback: LLM_BASE_URL/LLM_API_KEY/LLM_MODEL, or
     LLM_VISION_BASE_URL/LLM_VISION_API_KEY/LLM_VISION_MODEL.
No vendor name anywhere in code — the vendor lives entirely in #2/#3.
"""
from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "45.0"))


def is_mock() -> bool:
    return os.getenv("MOCK_LLM", "false").strip().lower() in ("1", "true", "yes", "on")


async def _resolve(db, role: str) -> dict:
    if role == "vision":
        env_base, env_key, env_model = "LLM_VISION_BASE_URL", "LLM_VISION_API_KEY", "LLM_VISION_MODEL"
    else:
        env_base, env_key, env_model = "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"
    if db is not None:
        try:
            from services import llm_config_store
            active = await llm_config_store.get_active_config(db, role)
            if active:
                return {"base_url": active["base_url"], "api_key": active["api_key"],
                        "model": active["model"], "label": active["label"], "source": "db"}
        except Exception as e:                                    # noqa: BLE001
            logger.warning("llm_client: active-config lookup failed, using env: %r", e)
    return {"base_url": os.environ.get(env_base, ""),
            "api_key": os.environ.get(env_key) or "",
            "model": os.environ.get(env_model, ""),
            "label": None, "source": "env"}


async def model_name(db=None, vision: bool = False) -> str:
    resolved = await _resolve(db, "vision" if vision else "chat")
    return resolved["model"]


async def _mock_stream(messages: list, tools: Optional[list]) -> AsyncIterator[dict]:
    """Deterministic mock responder — no network call. Echoes the last
    user turn + confirms tool/action visibility, so the full pipeline
    (tool loop, action-proposal parsing, streaming UI) is exercisable
    end-to-end before the real key lands. Flip MOCK_LLM=false + set an
    active config (or LLM_API_KEY) to go live — no other code change
    needed."""
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content") or ""
            break
    reply = (
        f"[MOCK_LLM \u2014 no active model yet] Saw your message: "
        f"\u201c{last_user[:200]}\u201d. {len(tools or [])} tool(s) visible "
        f"this turn. This is a deterministic mock reply so the pipeline "
        f"can be tested end-to-end before a real model is wired up. "
        f"Next actions: (1) add a provider in Settings -> Models & LLM, "
        f"(2) flip MOCK_LLM=false and smoke-test with a real call."
    )
    for i in range(0, len(reply), 24):
        yield {"type": "delta", "content": reply[i:i + 24]}
    yield {"type": "usage", "input_tokens": max(1, len(str(messages)) // 4),
           "output_tokens": max(1, len(reply) // 4)}
    yield {"type": "done"}


async def stream_chat(*, messages: list, tools: Optional[list] = None,
                       reasoning: bool = False, vision: bool = False,
                       max_tokens: int = 2000, db=None,
                       user_id: Optional[str] = None) -> AsyncIterator[dict]:
    """Yields {"type": "resolved"|"delta"|"tool_calls"|"usage"|"done"|"error", ...}.

    `resolved` is always the first event (model/label/source that will
    service this call) so the caller can log which config serviced
    each turn's cost, before any token spend happens.

    Tool-call deltas arrive from the provider in index-addressed
    fragments; they're accumulated internally and only surfaced once
    complete, as one `{"type": "tool_calls", "calls": [...]}` event
    right before `done`.

    `user_id` (R6, 2026-08-28): when provided (and MOCK_LLM is off),
    a real per-plan USD cap is checked HERE, right after resolution
    and BEFORE the provider is ever called — zero tokens are spent
    past the cap. Founder/admin accounts are unlimited (matches every
    other cap in this codebase). Real spend is logged after a
    successful call regardless, so the cap stays accurate turn over
    turn. See services/llm_usd_cap.py.
    """
    role = "vision" if vision else "chat"

    if is_mock():
        yield {"type": "resolved", "model": "mock", "label": "MOCK_LLM", "source": "mock"}
        async for evt in _mock_stream(messages, tools):
            yield evt
        return

    resolved = await _resolve(db, role)
    if not resolved["api_key"]:
        yield {"type": "error", "error": "llm_api_key_missing"}
        return
    yield {"type": "resolved", "model": resolved["model"],
           "label": resolved["label"] or resolved["model"], "source": resolved["source"]}

    rates = None
    if user_id and db is not None:
        from services.llm_rate_table import get_rate_table, cost_usd as _cost_usd
        from services.llm_usd_cap import assert_within_usd_cap, LLMUsdCapExceeded
        rates = await get_rate_table(db)
        est_input_tokens = max(1, len(str(messages)) // 4)
        est_cost = _cost_usd(rates, resolved["model"], est_input_tokens, max_tokens)
        try:
            await assert_within_usd_cap(db, user_id=user_id, est_cost_usd=est_cost)
        except LLMUsdCapExceeded as e:
            yield {"type": "error", "error": "monthly_limit_reached", "detail": e.message}
            return

    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=resolved["base_url"], api_key=resolved["api_key"],
                          timeout=_TIMEOUT_S)
    extra_body: dict = {}
    if reasoning:
        extra_body[os.getenv("LLM_REASONING_PARAM", "enable_thinking")] = True

    tool_accum: dict[int, dict] = {}
    usage: dict = {}
    try:
        stream = await client.chat.completions.create(
            model=resolved["model"],
            messages=messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
            stream=True,
            max_tokens=max_tokens,
            extra_body=extra_body or None,
        )
        async for chunk in stream:
            choice = (chunk.choices or [None])[0]
            if choice is None:
                continue
            delta = choice.delta
            if getattr(delta, "content", None):
                yield {"type": "delta", "content": delta.content}
            for tc in (getattr(delta, "tool_calls", None) or []):
                slot = tool_accum.setdefault(
                    tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
            u = getattr(chunk, "usage", None)
            if u:
                usage = {"input_tokens": u.prompt_tokens or 0,
                          "output_tokens": u.completion_tokens or 0}
    except Exception as e:  # noqa: BLE001
        logger.warning("ora_chat_v2 llm_client stream error: %r", e)
        yield {"type": "error", "error": type(e).__name__, "detail": str(e)[:200]}
        return

    if tool_accum:
        calls = []
        for slot in tool_accum.values():
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": slot["id"], "name": slot["name"], "arguments": args})
        yield {"type": "tool_calls", "calls": calls}
    if usage:
        if user_id and db is not None:
            from services.llm_rate_table import get_rate_table, cost_usd as _cost_usd
            from services.llm_usd_cap import record_usd_spend
            try:
                if rates is None:
                    rates = await get_rate_table(db)
                real_cost = _cost_usd(rates, resolved["model"],
                                       usage["input_tokens"], usage["output_tokens"])
                await record_usd_spend(
                    db, user_id=user_id, model=resolved["model"],
                    input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                    cost_usd=real_cost)
            except Exception as e:                              # noqa: BLE001
                logger.warning("llm_usd_cap: record_usd_spend failed: %r", e)
        yield {"type": "usage", **usage}
    yield {"type": "done"}
