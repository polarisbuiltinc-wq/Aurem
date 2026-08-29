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
