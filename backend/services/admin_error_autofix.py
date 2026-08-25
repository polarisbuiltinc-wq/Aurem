"""
services/admin_error_autofix.py — background autofix runner for the
Admin "Errors" tab. Fires `chat_with_tools` to investigate + fix a
reported frontend error, then updates the error doc's status.

Extracted from routers/admin_support.py (2026-08-27, mechanical split
— no behaviour change) to keep that router under the platform's
file-size guard.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("admin")


async def run_error_autofix(db, *, admin_user_id, error_id: str, oid, prompt: str) -> None:
    """Run the fix in the background, then flip `autofix_status` on
    the `frontend_errors` doc identified by `oid` to done|failed."""
    try:
        from services.orchestrator import chat_with_tools
        result = await chat_with_tools(
            prompt=prompt,
            user_id=admin_user_id,
            project_id=None,
            history_lines=[],
            live_invocations_ref=None,
            mode="maxx",
        )
        ok = bool(result and result.get("ok"))
        await db.frontend_errors.update_one(
            {"_id": oid},
            {"$set": {
                "autofix_status": "done" if ok else "failed",
                "autofix_finished": datetime.now(timezone.utc).isoformat(),
                "autofix_response": (result.get("content") or "")[:5_000]
                                      if isinstance(result, dict) else "",
            }},
        )
    except Exception as e:                       # noqa: BLE001
        logger.warning("autofix_error %s failed: %r", error_id, e)
        await db.frontend_errors.update_one(
            {"_id": oid},
            {"$set": {"autofix_status": "failed",
                      "autofix_error": str(e)[:1_000]}},
        )
