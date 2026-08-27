"""services/leak_digest.py — 2026-08-27, P3b "Quiet Leak Digest".

"Show the Outcome, Never the Engine" P3b: a weekly (NOT daily) plain-
English roll-up of the P2 audit-spine counters. Reuses EXISTING infra
only — no new email system, no new collection, no new endpoint:

  - Same collections `leak_alert_cron.py` already reads:
    `db.ora_audit` (`extra.leak_stripped` / `extra.recall_candidate`,
    written by `routers/chat.py`'s existing `record_turn()` call) and
    `db.loop_run_log` (`kind="internal_fault_not_user"`, written by
    `loop_engine.py::_fail_ship()`).
  - `services/daily_digest.py::_send_via_resend()` — the SAME Resend
    call, SAME `ADMIN_EMAIL` env var, SAME sender — imported, not
    duplicated.
  - `main.py`'s existing `_supervise()` wrapper, same pattern as
    every other cron in this file.

Tone: plain, 3-5 lines, no jargon (see `_render_text`). A >3x
week-over-week jump on any counter is flagged with one calm line —
NOT urgency, since a rising leak-strip count in the net's early weeks
is the net WORKING (catching what the prompt-layer instruction missed
while it settles), not a regression.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_SPIKE_MULTIPLIER = 3.0


def _is_enabled() -> bool:
    v = (os.environ.get("ENABLE_LEAK_DIGEST_CRON") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


async def _count_window(db, *, kind: str, since: datetime, until: datetime) -> int:
    since_iso, until_iso = since.isoformat(), until.isoformat()
    if kind == "leak_stripped":
        return await db.ora_audit.count_documents({
            "extra.leak_stripped": True,
            "timestamp": {"$gte": since_iso, "$lt": until_iso},
        })
    if kind == "recall_candidate":
        return await db.ora_audit.count_documents({
            "extra.recall_candidate": True,
            "timestamp": {"$gte": since_iso, "$lt": until_iso},
        })
    if kind == "internal_fault":
        return await db.loop_run_log.count_documents({
            "kind": "internal_fault_not_user",
            "created_at": {"$gte": since, "$lt": until},
        })
    raise ValueError(f"unknown kind: {kind}")


def _delta_label(this_week: int, last_week: int) -> str | None:
    """Returns a one-line spike flag, or None if not a real spike.
    A spike needs a non-trivial baseline (last_week >= 1) — going
    from 0 to 1 is not a 'spike', it's the first occurrence."""
    if last_week < 1:
        return None
    ratio = this_week / last_week
    if ratio >= _SPIKE_MULTIPLIER:
        return f"leak-strips up {ratio:.0f}x — checking"
    return None


async def build_leak_digest(db) -> dict:
    now = datetime.now(timezone.utc)
    this_week_start = now - timedelta(days=7)
    last_week_start = now - timedelta(days=14)

    leak_this = await _count_window(db, kind="leak_stripped", since=this_week_start, until=now)
    leak_last = await _count_window(db, kind="leak_stripped", since=last_week_start, until=this_week_start)
    fault_this = await _count_window(db, kind="internal_fault", since=this_week_start, until=now)
    fault_last = await _count_window(db, kind="internal_fault", since=last_week_start, until=this_week_start)
    recall_this = await _count_window(db, kind="recall_candidate", since=this_week_start, until=now)
    recall_last = await _count_window(db, kind="recall_candidate", since=last_week_start, until=this_week_start)

    return {
        "generated_at": now.isoformat(),
        "window_days": 7,
        "leak_stripped":     {"this_week": leak_this,   "last_week": leak_last},
        "internal_fault":    {"this_week": fault_this,  "last_week": fault_last},
        "recall_candidate":  {"this_week": recall_this, "last_week": recall_last},
        "spike_flag": _delta_label(leak_this, leak_last),
    }


def _render_text(d: dict) -> str:
    """Plain, 3-5 lines — no jargon, matches the founder's own example
    tone ("This week: the system caught N places where engine
    internals were about to leak (all stripped). 0 mis-blames. N
    recall events. All quiet.")."""
    leak = d["leak_stripped"]["this_week"]
    fault = d["internal_fault"]["this_week"]
    recall = d["recall_candidate"]["this_week"]
    leak_word = "place" if leak == 1 else "places"
    fault_word = "mis-blame" if fault == 1 else "mis-blames"
    recall_word = "recall event" if recall == 1 else "recall events"
    lines = [
        "AUREM — Weekly Quiet Leak Digest",
        d["generated_at"],
        (
            f"This week: the system caught {leak} {leak_word} where engine "
            f"internals were about to leak (all stripped). {fault} "
            f"{fault_word}. {recall} {recall_word}. "
            + ("All quiet." if not d.get("spike_flag") else "")
        ),
    ]
    if d.get("spike_flag"):
        lines.append(d["spike_flag"])
    return "\n".join(lines)


async def _run_once(db) -> None:
    from services.daily_digest import _send_via_resend

    digest = await build_leak_digest(db)
    body = _render_text(digest)
    logger.info(f"🤫 QUIET LEAK DIGEST\n{body}")
    admin_email = os.environ.get("ADMIN_EMAIL", "").strip()
    if admin_email:
        sent = await _send_via_resend(admin_email, "AUREM — Weekly Quiet Leak Digest", body)
        if not sent:
            logger.info("RESEND_API_KEY not set — leak digest only logged.")


async def schedule_leak_digest_cron() -> None:
    """Background loop — fires once a week on the configured weekday/
    hour (default Monday 07:00 UTC, one hour after the daily digest's
    default 06:00 so they don't compete for the same Resend burst)."""
    if not _is_enabled():
        logger.info("leak_digest_cron disabled (ENABLE_LEAK_DIGEST_CRON=0)")
        return
    target_weekday = int(os.environ.get("LEAK_DIGEST_WEEKDAY_UTC", "0"))  # 0=Monday
    target_hour = int(os.environ.get("LEAK_DIGEST_HOUR_UTC", "7"))
    while True:
        now = datetime.now(timezone.utc)
        days_ahead = (target_weekday - now.weekday()) % 7
        next_run = (now + timedelta(days=days_ahead)).replace(
            hour=target_hour, minute=0, second=0, microsecond=0,
        )
        if next_run <= now:
            next_run += timedelta(days=7)
        wait_s = (next_run - now).total_seconds()
        logger.info(f"leak digest sleeping {int(wait_s/3600)}h until {next_run.isoformat()}")
        try:
            await asyncio.sleep(wait_s)
            from cto_services.db import get_db
            db = get_db()
            if db is not None:
                await _run_once(db)
        except asyncio.CancelledError:
            raise
        except Exception as e:                              # noqa: BLE001
            logger.exception(f"leak digest scheduler crash: {e!r}")
            await asyncio.sleep(3600)
