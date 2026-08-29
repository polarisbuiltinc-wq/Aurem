"""
services/identity.py — single source of truth for ORA's self-identity.

Both system prompts (orchestrator.py's chat persona AND loop_engine.py's
plan prompt) consume OR_IDENTITY instead of hand-writing their own
"You are ..." opening line. This is the root fix for the ORA-vs-AUREM
naming drift found in the 2026-08 audit (loop prompt said "You are ORA,
an AI CTO", chat prompt said "You are AUREM — a senior, proactive
engineering co-pilot") — two different, hand-maintained strings that
could silently drift apart again.

Founder-locked naming canon (2026-08):
  - Legal entity: Polaris Built Inc. (legal surfaces only — ToS, footer
    trademark notice, etc.)
  - Company trade name: AUREM
  - Product / in-app assistant voice: "ORA by Aurem" — the assistant
    ALWAYS refers to itself as "ORA" in conversation, never "AUREM".
"""
from __future__ import annotations

OR_IDENTITY = (
    "You are ORA, the AI engineer inside ORA by Aurem (by AUREM, Polaris "
    "Built Inc.). Refer to yourself as \"ORA\" — first mention in a fresh "
    "session: \"I'm ORA, by Aurem.\" Never call yourself \"AUREM\". "
    "Plain, direct English."
)

# M1a fix (2026-08-30, T2-T5 GO follow-up round) — root cause of the
# "wrong product description on a brand-new user's first message"
# finding (T3/B4 real-model window): OR_IDENTITY only pins the
# assistant's NAME, never WHAT IT DOES. The casual-tier reply path
# (services/intent_gateway_casual_reply.py) had ZERO product grounding
# at all, so on "what does this tool do?" the model had nothing to
# anchor on and GENERATED a description — and generated a wrong one
# ("audio data" tool). Fix: pin the truth in one exact sentence, reused
# verbatim by every first-contact surface (casual-tier reply +
# AUREM_CTO_PERSONA) instead of letting the model invent it. Sourced
# from the app's own landing-page copy (R12 reuse-before-build) so it
# can never drift from what the product actually, factually does.
PRODUCT_IDENTITY = (
    "What ORA does (state if asked, don't invent): reads your GitHub "
    "repo, fixes real issues, ships as a commit/PR — Loop Mode "
    "verifies first."
)
