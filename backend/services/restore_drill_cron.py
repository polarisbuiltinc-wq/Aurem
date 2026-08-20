"""
services/restore_drill_cron.py — 2026-08-20

Recurring, automated backup-restore drill.

Founder's own audit flagged: "DB restore drill sirf ek baar tested hua
hai" — `POST /admin/backups/test-restore` (routers/backups_admin.py)
already did a real restore-and-diff against a scratch DB, but it was
manual-trigger-only, so restore-ability was proven exactly once, ever.

This closes that gap: reuses the SAME `db_restore.restore_to_scratch()`
+ diff logic on a recurring schedule with zero manual action, writes
every run to `restore_drill_history`, and emails the founder
(services/founder_alerts) the moment a drill fails or the restored
archive looks suspiciously truncated.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os

from services import db_restore
from services.founder_alerts import send_founder_alert

logger = logging.getLogger("restore_drill")

DRILL_INTERVAL_SECONDS = int(os.environ.get("RESTORE_DRILL_INTERVAL_SECONDS", str(7 * 86400)))
STARTUP_DELAY_SECONDS = int(os.environ.get("RESTORE_DRILL_STARTUP_DELAY_SECONDS", "600"))

# A drill "fails" if the restored archive covers less than this
# fraction of the live DB's CURRENT collection count. Doc counts
# legitimately drift between backup time and drill time, so we check
# collection coverage (did every collection round-trip?) rather than
# demanding exact doc-count equality.
MIN_COLLECTION_COVERAGE = 0.85


async def run_restore_drill(db) -> dict:
    """One drill: latest successful backup → scratch DB → diff → audit row."""
    started = dt.datetime.now(dt.timezone.utc)
    latest = await db.backup_history.find_one(
        {"status": "success"}, sort=[("created_at", -1)],
    )
    if not latest:
        row = {
            "ok": False, "error": "no successful backup in backup_history to drill against",
            "checked_at": started.isoformat(),
        }
        await db.restore_drill_history.insert_one(dict(row))
        await send_founder_alert(
            db, source_key="restore_drill_no_backup", level="critical",
            guard="restore_drill", title="Restore drill: no backup to test",
            detail=(
                "restore_drill_cron found zero successful rows in "
                "backup_history — the nightly backup cron may be "
                "failing silently."
            ),
        )
        return row

    r2_key = latest["r2_key"]
    source_counts = await db_restore.source_collection_counts()
    source_total_docs = sum(v for v in source_counts.values() if v >= 0)
    source_collections = len(source_counts)

    restore_result = await db_restore.restore_to_scratch(r2_key=r2_key, drop_scratch_after=True)
    restored_collections = restore_result.get("total_collections", 0)
    restored_docs = restore_result.get("total_docs", 0)

    coverage = (restored_collections / source_collections) if source_collections else 0.0
    passed = bool(
        restore_result.get("ok")
        and restored_docs > 0
        and coverage >= MIN_COLLECTION_COVERAGE
    )

    row = {
        "r2_key":               r2_key,
        "ok":                   passed,
        "restore_error":        restore_result.get("error"),
        "source_total_docs":    source_total_docs,
        "source_collections":   source_collections,
        "restored_total_docs":  restored_docs,
        "restored_collections": restored_collections,
        "collection_coverage":  round(coverage, 3),
        "duration_ms":          restore_result.get("duration_ms", 0),
        "checked_at":           started.isoformat(),
    }
    await db.restore_drill_history.insert_one(dict(row))

    if not passed:
        await send_founder_alert(
            db, source_key=f"restore_drill_fail_{r2_key}", level="critical",
            guard="restore_drill",
            title="Weekly restore drill FAILED",
            detail=(
                f"r2_key={r2_key}\nok={restore_result.get('ok')}\n"
                f"error={restore_result.get('error')}\n"
                f"restored {restored_docs} docs / {restored_collections} collections "
                f"vs live {source_total_docs} docs / {source_collections} collections "
                f"(coverage={coverage:.0%}, need >={MIN_COLLECTION_COVERAGE:.0%})."
            ),
        )
    else:
        logger.info(
            "restore drill OK — %s: %d docs / %d collections restored "
            "(coverage=%.0f%%, %dms)",
            r2_key, restored_docs, restored_collections, coverage * 100,
            row["duration_ms"],
        )
    return row


async def restore_drill_cron(db_getter=None) -> None:
    try:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
    except asyncio.CancelledError:
        raise
    while True:
        try:
            db = db_getter() if db_getter else None
            if db is None:
                logger.warning("restore_drill_cron: no db available — skipping this cycle")
            else:
                await run_restore_drill(db)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("restore_drill_cron loop error: %r", e)
        try:
            await asyncio.sleep(DRILL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise


__all__ = ["run_restore_drill", "restore_drill_cron", "DRILL_INTERVAL_SECONDS"]
