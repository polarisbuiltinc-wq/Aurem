"""
services/rollback_drill_cron.py — 2026-08-24, Guard 22

Recurring, automated rollback drill. `services/rollback_drill.py`'s
full snapshot -> ship-bad-commit -> rollback -> byte-exact-verify
harness already exists and is the most thoroughly validated piece of
infrastructure in this codebase (25/25 tests + a live drill this
session) — but it was manual-trigger-only (`POST
/admin/rollback/drill`), meaning "rollback works" was proven exactly
once, ever. This closes that gap the same way restore_drill_cron.py
closed it for backups: run the SAME harness on a recurring schedule
with zero manual action, and alert the founder the moment a real
drill fails.

Reuses AUREM_DRILL_REPO / AUREM_DRILL_BRANCH already configured in
backend/.env — no new config, no new infra.
"""
from __future__ import annotations

import asyncio
import logging
import os

from services.founder_alerts import send_founder_alert
from services.rollback_drill import run_drill

logger = logging.getLogger("aurem.rollback_drill_cron")

DRILL_INTERVAL_SECONDS = int(os.environ.get("ROLLBACK_DRILL_INTERVAL_SECONDS", str(7 * 86400)))
STARTUP_DELAY_SECONDS = int(os.environ.get("ROLLBACK_DRILL_STARTUP_DELAY_SECONDS", "900"))


async def rollback_drill_cron(db_getter=None) -> None:
    try:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
    except asyncio.CancelledError:
        raise
    while True:
        try:
            db = db_getter() if db_getter else None
            if db is None:
                logger.warning("rollback_drill_cron: no db available — skipping this cycle")
            else:
                row = await run_drill(db, initiated_by="cron")
                result = row.get("result")
                if result != "success":
                    await send_founder_alert(
                        db, source_key=f"rollback_drill_fail_{row.get('drill_id','')}",
                        level="critical", guard="rollback_drill_cron",
                        title="Scheduled rollback drill FAILED",
                        detail=(
                            f"drill_id={row.get('drill_id')} result={result} "
                            f"steps={row.get('steps')}"
                        ),
                    )
                else:
                    logger.info(
                        "rollback drill OK — drill_id=%s duration=%ss",
                        row.get("drill_id"), row.get("duration_s"),
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("rollback_drill_cron loop error: %r", e)
        try:
            await asyncio.sleep(DRILL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise


__all__ = ["rollback_drill_cron", "DRILL_INTERVAL_SECONDS"]
