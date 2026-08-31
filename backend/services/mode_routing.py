"""
services/mode_routing.py — 2026-09-02

Auto-escalation: a genuine code-edit (agentic tier) on Swift
transparently routes to the reliable model (Pro/Claude) so real edits
land correctly; a quick chat/query turn stays on the fast/cheap Swift
model (GLM). No manual mode switch, no jargon shown to the user — this
is silent, transparent routing, not a "please switch to Pro mode" ask.

Founder's explicit call (2026-09-02, connect-flow-refinement round):
do NOT make Pro the blanket default (breaks the free/paid Swift-vs-Pro
pricing split and wastes the expensive model on quick questions) and
do NOT ask the user to manually switch modes (that's the same jargon
dead-end already being removed elsewhere). Escalate ONLY the specific
turns that are real code edits.
"""
from __future__ import annotations


def resolve_model_mode(tier: str, req_mode: str) -> str:
    """`tier` is the intent-gateway classification for THIS turn
    ("casual" / "clarify" / "query" / "agentic"). `req_mode` is the
    user's selected mode pill ("swift" / "pro" / "maxx"). Returns the
    mode to actually pass to the model call — escalated to "pro" only
    when a real code-edit (agentic) lands on the fast/cheap Swift
    tier; every other combination is untouched."""
    if tier == "agentic" and req_mode == "swift":
        return "pro"
    return req_mode
