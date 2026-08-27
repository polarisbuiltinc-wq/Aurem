"""services/leak_alert_cron.py — 2026-08-27, P2 audit-spine alert.

"Show the Outcome, Never the Engine" P2 item 4: closes the "you'd
only know if you looked" gap for the output_guard leak-strip net.
Reuses existing infra ONLY — no new collection, no new endpoint:

  - `db.ora_audit` — the existing Core 5 audit sink
    (`services/audit_log.py::record_turn`), which now carries
    `extra.leak_stripped` on every chat_stream turn (P2 item 4 wiring).
  - `services/founder_alerts.py::send_founder_alert()` — same G10
    Resend channel, same 6h dedup-per-source_key, silent if
    RESEND_API_KEY/FOUNDER_ALERT_EMAIL are unset (dev/preview default).
  - `main.py`'s existing `_supervise()` wrapper — same pattern as
    `slo_alert_cron` / `cost_revenue_alert_cron`.

Threshold: alerts when more than `LEAK_ALERT_THRESHOLD` (default 5)
turns had `leak_stripped=true` in the trailing 24h. Default of 5 was
chosen because the leak-strip net is a SAFETY NET, not the primary
defense (the plain-English prompt contract is) — under normal
operation it should almost never fire; more than 5 real strips in a
day means the prompt-layer contract is failing regularly and needs
founder attention, not just a silent catch.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def _interval_seconds() -> int:
    raw = (os.environ.get("LEAK_ALERT_INTERVAL_SEC") or "1800").strip()
    try:
        return max(300, int(raw))  # never faster than every 5 min
    except ValueError:
        return 1800


def _threshold() -> int:
    raw = (os.environ.get("LEAK_ALERT_THRESHOLD") or "5").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 5


def _is_enabled() -> bool:
    v = (os.environ.get("ENABLE_LEAK_ALERT_CRON") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


async def count_leak_stripped_last_24h(db) -> int:
    """Real count from the existing `ora_audit` collection — no new
    collection, just a filter on the `extra.leak_stripped` field this
    same change now writes on every chat_stream turn."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    since_iso = since.isoformat()
    try:
        return await db.ora_audit.count_documents({
            "extra.leak_stripped": True,
            "timestamp": {"$gte": since_iso},
        })
    except Exception as e:                                   # noqa: BLE001
        logger.warning("leak_alert_cron: count query failed: %r", e)
        return 0


async def count_internal_faults_last_24h(db) -> int:
    """Companion count from the existing `loop_run_log` collection
    (`services/loop_audit_log.py`) — surfaced alongside the leak count
    in the alert body for context, not itself a separate alert."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        return await db.loop_run_log.count_documents({
            "kind": "internal_fault_not_user",
            "created_at": {"$gte": since},
        })
    except Exception as e:                                   # noqa: BLE001
        logger.warning("leak_alert_cron: internal-fault count failed: %r", e)
        return 0


async def _check_and_alert_once(db) -> None:
    from services.founder_alerts import send_founder_alert

    threshold = _threshold()
    leak_count = await count_leak_stripped_last_24h(db)
    if leak_count <= threshold:
        return
    fault_count = await count_internal_faults_last_24h(db)
    await send_founder_alert(
        db,
        source_key="leak_stripped_24h",
        title=f"Machinery-leak net fired {leak_count}x in 24h",
        detail=(
            f"output_guard.strip_machinery_leak() actually removed a "
            f"leaked internal token from {leak_count} chat replies in "
            f"the last 24h (threshold={threshold}). This is the "
            f"safety NET catching a leak the plain-English prompt "
            f"contract should have prevented — repeated hits mean the "
            f"prompt layer is failing regularly, not just an isolated "
            f"miss. (Context: {fault_count} internal-fault ship "
            f"failure(s) also logged in the same window.)"
        ),
        level="warning",
        guard="leak_alert_cron",
    )
    logger.warning(
        "🚨 leak_alert_cron · %d leak-stripped turns in 24h (threshold=%d)",
        leak_count, threshold,
    )


async def schedule_leak_alert_cron() -> None:
    """Background scheduler — kicked off from main.py startup, same
    shape as schedule_slo_alert_cron / schedule_cost_revenue_alert_cron."""
    if not _is_enabled():
        logger.info("leak_alert_cron disabled (ENABLE_LEAK_ALERT_CRON=0)")
        return
    interval = _interval_seconds()
    while True:
        try:
            from cto_services.db import get_db
            db = get_db()
            if db is not None:
                await _check_and_alert_once(db)
        except Exception as e:                               # noqa: BLE001
            logger.warning("leak_alert_cron tick failed: %r", e)
        await asyncio.sleep(interval)
