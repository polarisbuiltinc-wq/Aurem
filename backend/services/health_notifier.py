"""
services/health_notifier.py — Cockpit Bell notification poller (Feb 2026)

Long-lived supervised task that polls `services.health_registry` every
30-60 s (env `HEALTH_NOTIFIER_INTERVAL_S`, default 45) and DIFFS the
current status set against the last-known state in Mongo. Fires a
real notification ONLY on:

    green → red   (new real failure)     ← PRIMARY case
    red   → green (recovery, calmer tone)

Explicit NON-firing rules (per founder spec):

    ✗ green → gray  (config change, not a failure)
    ✗ gray  → green (config finally set — good, but not an alert)
    ✗ red staying red — dedup, at most 1 fire per check per 30 min
                        while it stays red
    ✗ any transition on an ACKED check (acked_until in future)

Every real fire does three things:

    (a) write a row into `health_notifications` collection
    (b) call `founder_alerts.send_founder_alert(...)`  (G10 channel,
        already-built Resend wiring, dedup handled inside)
    (c) update `health_check_state.<id>.last_known` so the next tick
        can diff against a fresh baseline

The bell UI (frontend) then short-polls `/admin/notifications` every
10-15 s to keep the badge live.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_INTERVAL_S = int(os.environ.get("HEALTH_NOTIFIER_INTERVAL_S", "45"))
# Max 1 red-still-red re-alert per (check, 30 min).
_RE_ALERT_COOLDOWN_S = int(os.environ.get("HEALTH_RE_ALERT_COOLDOWN_S", "1800"))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ack_active(acked_until: Optional[str]) -> bool:
    if not acked_until:
        return False
    try:
        return datetime.fromisoformat(acked_until.replace("Z", "+00:00")) \
             > datetime.now(timezone.utc)
    except Exception:  # noqa: BLE001
        return False


async def _persist_last_known(db, check_id: str, status: str) -> None:
    await db.health_check_state.update_one(
        {"_id": check_id},
        {"$set": {"last_known": status,
                  "last_known_at": _iso_now()}},
        upsert=True,
    )


async def _fire_notification(db, check_id: str, name: str, category: str,
                              old: str, new: str, detail: str) -> None:
    """Do all three writes atomically (as much as Mongo affords):
    notification row + founder-alert + state upsert."""
    import uuid
    now_iso = _iso_now()
    row = {
        # Feb 2026 · Bell-1 fix — stable per-row identifier so the UI
        # can mark ONE specific notification read (previously the only
        # supported operation was "mark all read" — clicking a single
        # row had no target ID). Uuid4 hex (12 chars) is enough to
        # avoid collisions across the ~1k-rows-per-90d expected volume.
        "notif_id":   uuid.uuid4().hex[:12],
        "check_id":   check_id,
        "name":       name,
        "category":   category,
        "from_state": old,
        "to_state":   new,
        "detail":     detail[:400],
        "created_at": now_iso,
        "read":       False,
    }
    try:
        await db.health_notifications.insert_one(row)
    except Exception:  # noqa: BLE001
        logger.warning("[health-notifier] failed to write notification row: %r",
                       check_id, exc_info=True)

    # G10 channel (real Resend wiring, already built).
    try:
        from services.founder_alerts import send_founder_alert
        title = (f"🔴 {name} went RED" if new == "red"
                 else f"🟢 {name} recovered")
        await send_founder_alert(
            db,
            source_key=f"health:{check_id}:{new}",
            title=title,
            detail=detail[:600],
            level=("critical" if new == "red" else "info"),
            guard="G-Bell",
        )
    except Exception:  # noqa: BLE001
        logger.warning("[health-notifier] founder_alert send failed for %r",
                       check_id, exc_info=True)

    # Track last-alert time for the cooldown gate.
    await db.health_check_state.update_one(
        {"_id": check_id},
        {"$set": {"last_alert_at": now_iso,
                  "last_alert_to": new}},
        upsert=True,
    )


async def _should_fire(state_row: dict, new: str) -> bool:
    """Cooldown gate for red-staying-red re-alerts. green→red and
    red→green pass immediately; only red→red honours the cooldown.

    IMPORTANT: for a pre-existing red at pod boot (no prior alert
    ever recorded), we DO NOT fire — that's a baseline observation,
    not a state transition. Founder must not be spammed with "still
    red since forever" alerts every startup. First real fire on that
    check will happen if it recovers and later goes red again."""
    if new != "red":
        return True
    last_alert = (state_row or {}).get("last_alert_at")
    last_to    = (state_row or {}).get("last_alert_to")
    if not last_alert:
        # Never alerted on this check before. Baseline — silent.
        return False
    if last_to != "red":
        # Last alert was a recovery (red→green). If we're red again
        # now, that's a fresh red — fire once.
        return True
    try:
        prev = datetime.fromisoformat(last_alert.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return True
    return datetime.now(timezone.utc) - prev \
        > timedelta(seconds=_RE_ALERT_COOLDOWN_S)


async def _tick_once() -> None:
    """One diff-and-fire cycle. Runs every _INTERVAL_S."""
    from services.health_registry import all_checks, run_check_safely
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return  # DB not up yet at startup; try next tick

    checks = all_checks()
    if not checks:
        return

    # Run all check_fns in parallel.
    results = await asyncio.gather(
        *[run_check_safely(c) for c in checks],
        return_exceptions=False,
    )

    for check, res in zip(checks, results):
        new_status = res["status"]

        # Load prior state (last_known + acked_until).
        state_row = await db.health_check_state.find_one({"_id": check.id}) or {}
        last_known  = state_row.get("last_known")
        acked_until = state_row.get("acked_until")

        # Fire logic. See module docstring for the truth table.
        should_notify = False
        if _ack_active(acked_until):
            should_notify = False    # acked, stay silent
        elif last_known == "green" and new_status == "red":
            should_notify = True     # primary case
        elif last_known == "red" and new_status == "green":
            should_notify = True     # recovery
        elif last_known == "red" and new_status == "red":
            # Cooldown-gated re-alert while staying red.
            should_notify = await _should_fire(state_row, new_status)
        # green↔gray, gray↔green, gray↔red, red↔gray → NO fire

        if should_notify:
            await _fire_notification(
                db, check.id, check.name, check.category,
                old=last_known or "unknown",
                new=new_status,
                detail=res.get("detail", ""),
            )

        # Always update last_known so the next tick has fresh baseline.
        if last_known != new_status:
            await _persist_last_known(db, check.id, new_status)


async def notifier_loop() -> None:
    """Long-lived cron. Registered via supervise() so a crash lands
    as a G-F1 incident (see services/supervised_tasks.py)."""
    logger.info("[health-notifier] starting · interval=%ds", _INTERVAL_S)
    # Small warm-up delay so the aggregator cache is populated first.
    await asyncio.sleep(5.0)
    while True:
        try:
            await _tick_once()
        except Exception:  # noqa: BLE001 — never let a bug crash the cron
            logger.exception("[health-notifier] tick crashed")
        await asyncio.sleep(_INTERVAL_S)
