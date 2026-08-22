"""
services/founder_summary.py — 2026-08-24 (Pillar 6, Production-Readiness)

Dedicated founder-language translation layer. A SEPARATE LLM call
(never the main coding-agent's own response) turns a raw technical
event into a strictly-templated, jargon-free summary with exactly
three fields: what_changed / what_to_verify / risk.

Two-view split: `technical_view` is built directly from the input
event data (no LLM involved — always available, full detail).
`founder_view` is the LLM's plain-language translation of the SAME
event. Both are persisted together under one `event_summaries`
document so either view can be read back from the same event without
re-summarizing.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid

logger = logging.getLogger("founder_summary")

_SYSTEM_PROMPT = (
    "You translate raw software-engineering events into plain language "
    "for a non-technical founder. Rules:\n"
    "- Output ONLY valid JSON with exactly these three keys: "
    "\"what_changed\", \"what_to_verify\", \"risk\".\n"
    "- Each value is 1-2 short sentences, plain everyday English.\n"
    "- NEVER include file paths, function/variable names, stack traces, "
    "commit hashes, library names, HTTP status codes, or code syntax.\n"
    "- \"what_changed\": what actually happened, in outcome terms "
    "(e.g. \"the sign-in page was fixed\" not \"api.js baseURL fallback\").\n"
    "- \"what_to_verify\": one concrete, non-technical thing the founder "
    "can personally check (e.g. \"try logging in on the live site\").\n"
    "- \"risk\": honest plain-language risk level and why — say \"low "
    "risk\" if genuinely low, don't manufacture concern that isn't real."
)


def _fallback_summary(reason: str) -> dict:
    return {
        "what_changed": "A technical update was made — a detailed summary "
                         "couldn't be generated automatically this time.",
        "what_to_verify": "Ask your engineer to walk you through this change directly.",
        "risk": "Unknown — the automatic summary failed, so this hasn't been assessed.",
        "generation_error": reason,
    }


async def _call_llm(technical_event: dict) -> dict:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    api_key = os.environ["EMERGENT_LLM_KEY"]
    chat = (
        LlmChat(
            api_key=api_key,
            session_id=f"founder-summary-{uuid.uuid4().hex[:12]}",
            system_message=_SYSTEM_PROMPT,
        )
        .with_model("anthropic", "claude-sonnet-4-6")
    )
    prompt = ("Technical event data (JSON):\n" +
              json.dumps(technical_event, default=str)[:6000] +
              "\n\nRespond with ONLY the JSON object described in your instructions.")
    raw = await chat.send_message(UserMessage(text=prompt))
    text = raw if isinstance(raw, str) else getattr(raw, "text", str(raw))
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    parsed = json.loads(text)
    for key in ("what_changed", "what_to_verify", "risk"):
        if key not in parsed or not isinstance(parsed[key], str):
            raise ValueError(f"missing/invalid key: {key}")
    return {k: parsed[k] for k in ("what_changed", "what_to_verify", "risk")}


def _build_technical_view(technical_event: dict) -> dict:
    """No LLM — a direct, full-detail projection of the raw event.
    This is what the developer/technical log view always shows."""
    return {k: v for k, v in technical_event.items()}


async def generate_founder_summary(db, *, event_id: str | None, source: str,
                                    technical_event: dict) -> dict:
    """Runs the dedicated summarization call, persists both views under
    one event_summaries document, returns the combined record."""
    event_id = event_id or uuid.uuid4().hex
    try:
        founder_view = await _call_llm(technical_event)
    except Exception as e:
        logger.warning("[founder_summary %s] LLM call failed: %r", event_id, e)
        founder_view = _fallback_summary(str(e)[:200])

    technical_view = _build_technical_view(technical_event)
    doc = {
        "event_id": event_id,
        "source": source,
        "created_at": time.time(),
        "founder_view": founder_view,
        "technical_view": technical_view,
    }
    if db is not None:
        try:
            await db.event_summaries.update_one(
                {"event_id": event_id}, {"$set": doc}, upsert=True,
            )
        except Exception as e:
            logger.warning("[founder_summary %s] persist failed: %r", event_id, e)
    return doc
