"""
services/trust_surface_events.py — Trust Surfaces Round (S0-S5),
2026-08-29.

No existing generic user-telemetry event sink was found in this
codebase to extend (checked: audit.py's log_event is admin-audit only,
scoped to founder actions). This is the ONE new lightweight collection
for the S1/S3 events this round's spec explicitly names, feeding the
S4 admin monitor tile and the S5 30-day meter line. Deterministic,
0-LLM, fire-and-forget (never blocks the caller's real work on a
write failure).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Exhaustive, named per the S3 spec — kept as a closed set so a typo
# in a call site fails loudly in tests instead of silently drifting.
EVENT_KINDS = {
    "preview_session",
    "deploy_form_shown", "deploy_started", "deploy_succeeded", "deploy_failed",
    "receipt_captured", "rollback_clicked", "rollback_succeeded",
    # X1 hardening (2026-08-30, overnight-loop-2 P0) — a durable, queryable
    # trail of every live request served by MOCK_LLM, across every LLM
    # entry point (ora_chat_v2 chat_stream + services/llm/_meta.py's
    # orchestrator/loop/council gateway). Feeds the admin "Live Model
    # Mode" tile's 24h counter.
    "mock_detected_in_live",
    # H3 hardening (2026-08-30, overnight-loop-2 P0) — fired when a
    # paused-for-user ship's live GitHub binding no longer matches what
    # was pinned when the ship was staged; the loop aborts instead of
    # writing to a repo/branch/installation the user never approved.
    "loop_pin_mismatch",
    # T2 (2026-08-30, R10 rollback-gap fix) — fired whenever a rollback
    # cannot be honestly reported as "done": either the live PR merge
    # state itself could not be confirmed, or a revert commit was
    # pushed but its landing on the base branch could not be verified
    # within the bounded poll window. Never fired on a real success.
    "ship_rollback_failed",
    # V1d (2026-08-30) — deploy-verify (V1) run lifecycle. verify_passed/
    # failed carry the deterministic engine's verdict; never fired for
    # the pre-existing shallow httpx-reachability check alone (that one
    # keeps its own verified/verify_note fields, unchanged by V1).
    "verify_started", "verify_passed", "verify_failed",
}


async def log_trust_event(db, kind: str, *, user_id: str,
                           project_id: str | None = None, **fields) -> None:
    if kind not in EVENT_KINDS:
        logger.warning("log_trust_event: unknown kind %r — not logged", kind)
        return
    try:
        await db.trust_surface_events.insert_one({
            "kind": kind,
            "user_id": user_id,
            "project_id": project_id,
            "at": datetime.now(timezone.utc).isoformat(),
            **fields,
        })
    except Exception as e:
        logger.warning("log_trust_event(%s) failed: %r", kind, e)
