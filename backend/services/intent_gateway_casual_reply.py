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


async def casual_direct_reply(prompt: str, prior_assistant_text: str | None = None,
                               session_summary: str | None = None) -> str:
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
    incompatible with this deliberately tool-free path.

    `session_summary` — 2026-08-30 Issue C fix. For sessions long
    enough to have a rolling summary (services/session_summary.py),
    this carries what happened BEFORE the single immediately-prior
    turn above — otherwise a recall question like "what did we find
    earlier?" (which the heuristic classifier routes here, same
    resource-noun gap as Issue B) still has no way to answer beyond
    the last exchange."""
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
    if prior_assistant_text:
        system += (
            "\n\nYour own immediately-prior message to this user (for "
            f"continuity only): \"{prior_assistant_text[:400]}\"\n"
            "If the user's new message is a short follow-up to that "
            "(e.g. \"did you find any?\", \"and?\", \"ok what next\"), "
            "answer it in-thread using that context — give a real "
            "status or answer. Do NOT ask them to clarify what they "
            "want when the prior message already made that clear."
        )
    if session_summary:
        system += (
            "\n\nRunning summary of this session so far (earlier turns "
            f"not shown verbatim): \"{session_summary[:400]}\"\n"
            "If the user references something from earlier in this "
            "conversation (e.g. \"what did we find/fix earlier?\", "
            "\"what did we decide?\"), answer FROM this summary. Do NOT "
            "say you don't recall or ask them to clarify when the "
            "summary already answers it."
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
