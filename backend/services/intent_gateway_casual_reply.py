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
    Raises on failure — callers decide the fallback behavior.

    2026-08-25 — Point 4 (Engineering Gap #3, "Unified Mode"): reuses
    the SAME admin-configured advisor persona block Ask Advisor already
    uses (services/house_rules.py, target="advisor") — Rule 12, reuse
    before build — instead of writing a second, separate persona. This
    is the one existing, admin-editable "Ask Advisor" prompt block; a
    user typing a plain question into the main chat now gets the exact
    same underlying voice as opening the side panel, without knowing
    there are two agents behind it. Deliberately does NOT reuse
    ORA_PANEL_TONE (routers/chat.py) — that block's R1-R5 rules mandate
    a read_repo_file tool call for any file/version claim, which is
    incompatible with this deliberately tool-free path."""
    from services.llm import call_llm
    from services.identity import PRODUCT_IDENTITY
    system = (
        "You are ORA — AUREM's developer co-pilot.\n"
        f"{PRODUCT_IDENTITY}\n"
        "For this casual message, respond naturally and briefly.\n"
        "Be confident, warm, and direct. Do NOT mention\n"
        "pipelines, agents, or technical systems. Keep your\n"
        "reply under 2 sentences."
    )
    try:
        from services.house_rules import get_active_house_rules, format_house_rules_block
        _hr = await get_active_house_rules("advisor", None)
        if _hr:
            system = format_house_rules_block(_hr) + "\n\n" + system
    except Exception:
        pass  # admin rules are an enhancement here, never a hard dependency
    reply = await call_llm(
        [{"role": "user", "content": prompt or ""}],
        system=system, max_tokens=200, temperature=0.6,
    )
    return (reply or "").strip() or "Hey! How can I help you ship today?"
