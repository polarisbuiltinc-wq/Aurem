"""services/slo_alert_cron.py — 2026-08-26, approved Stage-1 follow-up.

Periodic breach check on top of the SLO dashboard already built
(`services/slo_metrics.py` + `GET /admin/insights/slo`) — closes the
gap between "the dashboard SHOWS a breach if you look" and "the
founder gets told about a breach without having to look." Reuses:
  - `services/slo_metrics.py::compute_slo()` — same targets/queries
    the admin dashboard card already uses, no new metric logic.
  - `services/founder_alerts.py::send_founder_alert()` (the existing
    G10 Resend channel) — same 6h dedup-per-source_key, same
    silent-if-RESEND_API_KEY-or-FOUNDER_ALERT_EMAIL-missing behavior
    as every other founder alert in the app. No new email infra.
  - `main.py`'s existing `_supervise()` wrapper for long-lived cron
    tasks (same pattern as `integration_health_cron` / `daily_digest`)
    — a silent death here opens a Guard 20 incident like any other
    supervised task, instead of quietly never running again.

A "breach" only fires when `met is False` (never on `met is None` —
not enough samples yet to mean anything) AND `sample_size` clears a
minimum floor, so a single slow request right after a quiet period
can't trigger a false alert.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

MIN_SAMPLE_SIZE = 5


def _interval_seconds() -> int:
    raw = (os.environ.get("SLO_ALERT_INTERVAL_SEC") or "1800").strip()
    try:
        return max(300, int(raw))  # never faster than every 5 min
    except ValueError:
        return 1800


def _is_enabled() -> bool:
    v = (os.environ.get("ENABLE_SLO_ALERT_CRON") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


async def _check_and_alert_once(db) -> None:
    from services.slo_metrics import compute_slo
    from services.founder_alerts import send_founder_alert

    snap = await compute_slo(db, period_days=7)
    slos = snap.get("slos") or {}
    for key, s in slos.items():
        if s.get("met") is not False:
            continue  # None (no data) or True (healthy) — nothing to do
        if (s.get("sample_size") or 0) < MIN_SAMPLE_SIZE:
            continue
        p95 = s.get("p95_ms", s.get("p95_s"))
        unit = "ms" if "p95_ms" in s else "s"
        target = s.get("target_good_ms", s.get("target_good_s"))
        await send_founder_alert(
            db,
            source_key=f"slo_breach_{key}",
            title=f"SLO breach — {s.get('label', key)}",
            detail=(
                f"p95 is {p95}{unit} against a target of {target}{unit} "
                f"(sample_size={s.get('sample_size')}, last 7 days)."
            ),
            level="critical",
            guard="slo_breach",
        )
        logger.warning(
            "🚨 slo_alert_cron · breach on %s (p95=%s%s, target=%s%s, n=%s)",
            key, p95, unit, target, unit, s.get("sample_size"),
        )


async def schedule_slo_alert_cron() -> None:
    """Background scheduler — kicked off from main.py startup, same
    shape as schedule_integration_health_cron / schedule_daily_digest."""
    if not _is_enabled():
        logger.info("slo_alert cron disabled via env")
        return

    interval = _interval_seconds()
    logger.info("📈 slo_alert cron ON · every %ds", interval)
    await asyncio.sleep(180)  # let the app finish booting first

    while True:
        try:
            from cto_services.db import get_db
            db = get_db()
            if db is not None:
                await _check_and_alert_once(db)
        except Exception as e:                              # noqa: BLE001
            logger.warning("slo_alert cron tick failed: %r", e)
        await asyncio.sleep(interval)
