"""
services/intent_gateway_casual_reply.py — 2026-08-25

Single shared implementation of the intent-gateway "casual/clarify"
direct-answer reply (no tool calls). Extracted after a testing_agent
code-review flag on the 2026-08-25 casual/query boundary fix: chat_send
and chat_stream each carried their OWN copy of this exact block —
precisely the single-surface-drift risk the founder called
zero-tolerance on (a future fix landing on one chat surface but not
its sibling). Both call sites now share this one function instead.
"""
from __future__ import annotations


async def casual_direct_reply(prompt: str) -> str:
    """Direct, no-tool LLM reply for casual/clarify tier messages.
    Raises on failure — callers decide the fallback behavior."""
    from services.llm import call_llm
    system = (
        "You are ORA — AUREM's developer co-pilot.\n"
        "For this casual message, respond naturally and briefly.\n"
        "Be confident, warm, and direct. Do NOT mention\n"
        "pipelines, agents, or technical systems. Keep your\n"
        "reply under 2 sentences."
    )
    reply = await call_llm(
        [{"role": "user", "content": prompt or ""}],
        system=system, max_tokens=200, temperature=0.6,
    )
    return (reply or "").strip() or "Hey! How can I help you ship today?"
