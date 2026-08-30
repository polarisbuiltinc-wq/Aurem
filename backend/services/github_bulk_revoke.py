"""services/github_bulk_revoke.py — 2026-08-30.

Admin bulk GitHub App revoke — the real-uninstall half of the batch.
Owns ONLY the "hit GitHub for N installations, in parallel, bounded,
never let one slow/failed call stop the rest" concern. DB sync,
the hard guard, the kill-switch, and audit logging live in the route
(routers/admin_bin.py) — this module is deliberately dumb so a
future single-install "revoke this one" call can reuse it too.

STANDING GATE — do not remove without founder sign-off:
This tool is built + mock-tested only. The live drill-repo verify
(U1-U6: real DELETE /app/installations/{id} behavior — success code,
token invalidation, one-way-ness, error cases) has NOT been run
against a real disposable installation (blocked today by a stale
preview GitHub App key + no spare installation to safely delete).
Real destructive use is gated server-side by the
`github_bulk_revoke_live_verified` feature flag (default OFF) — see
routers/admin_bin.py::github_bulk_revoke and
/app/memory/GITHUB_BULK_REVOKE_DRILL_VERIFY.md.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services import github_app as _ga

logger = logging.getLogger(__name__)

REVOKE_TIMEOUT_S = 10
REVOKE_CONCURRENCY = 5


async def _revoke_one(installation_id: int) -> dict[str, Any]:
    try:
        result = await asyncio.wait_for(
            _ga.revoke_installation_verbose(int(installation_id)),
            timeout=REVOKE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        result = {"outcome": "failed", "status_code": None,
                  "error": f"timed out after {REVOKE_TIMEOUT_S}s"}
    except Exception as e:  # noqa: BLE001 — one bad install must not sink the batch
        result = {"outcome": "failed", "status_code": None,
                  "error": f"{type(e).__name__}: {e}"}
    result["installation_id"] = installation_id
    return result


async def bulk_revoke(installation_ids: list[int]) -> list[dict[str, Any]]:
    """Process the batch in parallel, bounded to REVOKE_CONCURRENCY at
    a time. One failure/timeout never stops the rest — every id gets
    exactly one result dict:
    {installation_id, outcome: deleted|already_gone|failed,
     status_code, error}."""
    sem = asyncio.Semaphore(REVOKE_CONCURRENCY)

    async def _bounded(iid):
        async with sem:
            return await _revoke_one(iid)

    return list(await asyncio.gather(*[_bounded(i) for i in installation_ids]))


__all__ = ["bulk_revoke", "REVOKE_TIMEOUT_S", "REVOKE_CONCURRENCY"]
