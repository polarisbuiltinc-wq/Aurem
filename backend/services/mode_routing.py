"""
services/mode_routing.py — 2026-09-02, extended 2026-09-03 (Root 4,
core-flow round)

Auto-escalation: a genuine code/content edit (agentic tier) on Swift
should never land on the fast/cheap Swift model and produce a raw or
unreliable result; it should go to the reliable model (Pro/Claude).
A quick chat/query turn stays on the fast/cheap Swift model (GLM).

TWO config-gated paths, both fully implemented (founder flips via the
`EDIT_TIER_MODE` env var — no code change needed to switch):

  Path A — "transparent" — a real edit on Swift silently uses the
    reliable model regardless of the account's plan. No manual mode
    switch, no jargon shown to the user. The business absorbs the
    extra model cost. This was the ONLY path before 2026-09-03
    (founder's original connect-flow-refinement call: do NOT make Pro
    the blanket default, do NOT ask the user to manually switch
    modes — but that call predates the free/paid Swift-vs-Pro split
    being enforced here).

  Path B — "gated" (DEFAULT, 2026-09-03) — Swift is genuinely the
    free/starter tier's only mode (`subscription_tiers.TIER_LIMITS`:
    free and starter both have `modes: ["swift"]` only; pro/team/
    founder include "pro"). A free/starter account attempting a real
    edit gets an HONEST upgrade offer (`UPGRADE_OFFER_MESSAGE`) —
    never silently escalated (no surprise cost absorbed on their
    behalf), and never the "nothing pending" dead end either (a real,
    concrete next step is always offered). An account that ALREADY
    has Pro access (`account_has_pro=True` — they're paying for it,
    just left the Swift pill selected) is NEVER shown an upgrade
    offer in either path — they already own the capability, so this
    silently escalates exactly like Path A for them.

Both paths guarantee the same thing: NEVER a false "would you like me
to..." confirm with no real backing (Root 1), and NEVER a silent
"nothing happened" for a real edit request.
"""
from __future__ import annotations

import os

EDIT_TIER_MODE_TRANSPARENT = "transparent"  # Path A
EDIT_TIER_MODE_GATED = "gated"              # Path B (default)

UPGRADE_OFFER_MESSAGE = (
    "That's a real change to make — to get it applied correctly I "
    "need our more reliable editing model, which is part of the Pro "
    "plan. Want me to set up that upgrade, or would you like to keep "
    "browsing on the free plan for now?"
)


def edit_tier_mode() -> str:
    """Reads `EDIT_TIER_MODE` env var — "transparent" (Path A) or
    "gated" (Path B, default when unset/unrecognized)."""
    v = (os.environ.get("EDIT_TIER_MODE") or EDIT_TIER_MODE_GATED).strip().lower()
    if v not in (EDIT_TIER_MODE_TRANSPARENT, EDIT_TIER_MODE_GATED):
        return EDIT_TIER_MODE_GATED
    return v


def resolve_model_mode(tier: str, req_mode: str, *, account_has_pro: bool = True) -> str:
    """`tier` is the intent-gateway classification for THIS turn
    ("casual" / "clarify" / "query" / "agentic"). `req_mode` is the
    user's selected mode pill ("swift" / "pro" / "maxx").
    `account_has_pro` — True iff the caller's ACCOUNT tier already
    includes "pro" in `subscription_tiers.allowed_modes_for_tier`
    (i.e. not being asked to upgrade to something they don't already
    have). Defaults to True so any caller that doesn't pass it keeps
    the original unconditional transparent-escalation behavior.

    Returns the mode to actually pass to the model call — escalated
    to "pro" for a real edit (agentic tier) landing on Swift, EXCEPT
    when the account has no Pro access AND gated mode is active — in
    that case the mode stays "swift" and the caller must show
    `UPGRADE_OFFER_MESSAGE` instead of running the edit
    (`needs_edit_upgrade_offer` tells the caller when to do that)."""
    if tier != "agentic" or req_mode != "swift":
        return req_mode
    if account_has_pro or edit_tier_mode() == EDIT_TIER_MODE_TRANSPARENT:
        return "pro"
    return req_mode


def needs_edit_upgrade_offer(tier: str, req_mode: str, *, account_has_pro: bool = True) -> bool:
    """True iff this turn is a real edit (agentic) on the Swift pill,
    the account has no Pro access, AND gated mode (Path B) is active —
    the caller should show `UPGRADE_OFFER_MESSAGE` instead of running
    the edit attempt on Swift."""
    if account_has_pro:
        return False
    if tier != "agentic" or req_mode != "swift":
        return False
    return edit_tier_mode() == EDIT_TIER_MODE_GATED
