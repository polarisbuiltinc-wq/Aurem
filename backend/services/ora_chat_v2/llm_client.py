"""
services/ora_chat_v2/llm_client.py — Admin ORA Chat rebuild, P2.

Vendor-swappable OpenAI-compatible client. The vendor (base_url / model /
key) comes ONLY from env vars (LLM_BASE_URL, LLM_MODEL, LLM_API_KEY) —
no vendor name anywhere in code. MOCK_LLM=true runs a deterministic
mock responder so the pipeline, UI, and tests can all proceed before a
real DASHSCOPE_API_KEY lands (founder is creating the account now).
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


def model_name() -> str:
    return os.environ.get("LLM_MODEL", "")


def _client(vision: bool = False):
    from openai import AsyncOpenAI
    base_url = os.environ.get("LLM_VISION_BASE_URL" if vision else "LLM_BASE_URL", "")
    api_key = os.environ.get("LLM_VISION_API_KEY" if vision else "LLM_API_KEY") or "mock-unused"
    return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=_TIMEOUT_S)


async def _mock_stream(messages: list, tools: Optional[list]) -> AsyncIterator[dict]:
    """Deterministic mock responder — no network call. Echoes the last
    user turn + confirms tool/action visibility, so the full pipeline
    (tool loop, action-proposal parsing, streaming UI) is exercisable
    end-to-end before the real key lands. Flip MOCK_LLM=false + set
    LLM_API_KEY to go live — no other code change needed."""
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content") or ""
            break
    reply = (
        f"[MOCK_LLM \u2014 no LLM_API_KEY yet] Saw your message: "
        f"\u201c{last_user[:200]}\u201d. {len(tools or [])} tool(s) visible "
        f"this turn. This is a deterministic mock reply so the pipeline "
        f"can be tested end-to-end before the real DashScope key lands. "
        f"Next actions: (1) hand over LLM_API_KEY, (2) I flip MOCK_LLM=false "
        f"and smoke-test with a real call."
    )
    for i in range(0, len(reply), 24):
        yield {"type": "delta", "content": reply[i:i + 24]}
    yield {"type": "usage", "input_tokens": max(1, len(str(messages)) // 4),
           "output_tokens": max(1, len(reply) // 4)}
    yield {"type": "done"}


async def stream_chat(*, messages: list, tools: Optional[list] = None,
                       reasoning: bool = False, vision: bool = False,
                       max_tokens: int = 2000) -> AsyncIterator[dict]:
    """Yields {"type": "delta"|"tool_calls"|"usage"|"done"|"error", ...}.

    Tool-call deltas arrive from the provider in index-addressed
    fragments; they're accumulated internally and only surfaced once
    complete, as one `{"type": "tool_calls", "calls": [...]}` event
    right before `done`.
    """
    if is_mock():
        async for evt in _mock_stream(messages, tools):
            yield evt
        return

    key_env = "LLM_VISION_API_KEY" if vision else "LLM_API_KEY"
    if not os.environ.get(key_env):
        yield {"type": "error", "error": "llm_api_key_missing"}
        return

    client = _client(vision=vision)
    extra_body: dict = {}
    if reasoning:
        extra_body[os.getenv("LLM_REASONING_PARAM", "enable_thinking")] = True

    tool_accum: dict[int, dict] = {}
    usage: dict = {}
    try:
        stream = await client.chat.completions.create(
            model=os.environ.get("LLM_VISION_MODEL" if vision else "LLM_MODEL", ""),
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
        yield {"type": "usage", **usage}
    yield {"type": "done"}
