"""
services/founder_alerts.py — G10 · Founder alert channel (Iter 366)

Every CRITICAL alert from any guard (banner, RED/STALE, incident_log
open row) fires a single Resend email to the founder inbox.

Rules:
  - Dedup: same (source_key, level) max 1 email per 6h.
  - Silent if RESEND_API_KEY or FOUNDER_ALERT_EMAIL is missing (dev/
    preview default) — DEBUG log only, never raise.
  - Best-effort write to db.founder_alert_sends for audit trail +
    Guard 20 postmortem cross-ref.
  - Called from: services/topup_alerts.upsert_alerts_from_snapshot
    (via existing incident hook), services/incident_log.upsert_incident,
    and can be called directly from any guard-critical path.

Public API:
  send_founder_alert(db, *, source_key, title, detail, level="critical",
                     guard=None) -> dict
  send_daily_digest(db, hours=24) -> dict     (cron entry)
"""
from __future__ import annotations

import os
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("aurem.founder_alerts")


DEDUP_WINDOW_HOURS = int(os.environ.get("FOUNDER_ALERT_DEDUP_HOURS", "6"))


def _resend_conf() -> dict:
    return {
        "api_key":   os.environ.get("RESEND_API_KEY", ""),
        "to":        os.environ.get("FOUNDER_ALERT_EMAIL", ""),
        "from_":     os.environ.get("FOUNDER_ALERT_FROM",
                                     "AuremCTO Guardian <alerts@auremcto.com>"),
        "enabled":   bool(
            os.environ.get("RESEND_API_KEY")
            and os.environ.get("FOUNDER_ALERT_EMAIL")
        ),
    }


async def _recent_dedup_hit(db, source_key: str, level: str) -> bool:
    since = datetime.now(timezone.utc) - timedelta(hours=DEDUP_WINDOW_HOURS)
    try:
        row = await db.founder_alert_sends.find_one({
            "source_key": source_key,
            "level":      level,
            "sent_at":    {"$gte": since},
        })
        return row is not None
    except Exception:
        return False


def _send_via_resend(conf: dict, subject: str, html: str) -> dict:
    """Blocking HTTP call — kept small so a bad Resend response can't
    hang the caller's request. Any exception is trapped by the caller."""
    import json
    import urllib.request
    payload = json.dumps({
        "from":    conf["from_"],
        "to":      [conf["to"]],
        "subject": subject,
        "html":    html,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {conf['api_key']}",
            "Content-Type":  "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read().decode("utf-8", errors="replace")
        return {"status": r.status, "body": body[:400]}


async def send_founder_alert(
    db,
    *,
    source_key: str,
    title:      str,
    detail:     str,
    level:      str = "critical",
    guard:      Optional[str] = None,
) -> dict:
    """Fire-and-forget alert. Returns a dict describing whether the
    send happened (or was deduped / disabled)."""
    conf = _resend_conf()
    if not conf["enabled"]:
        logger.debug(
            "[G10] founder alert skipped — RESEND_API_KEY or "
            "FOUNDER_ALERT_EMAIL missing (source_key=%s)", source_key,
        )
        return {"sent": False, "reason": "config_missing", "enabled": False}

    if db is not None and await _recent_dedup_hit(db, source_key, level):
        return {"sent": False, "reason": "dedup",
                "window_hours": DEDUP_WINDOW_HOURS}

    subject = f"[{level.upper()}] {title[:80]}"
    if guard:
        subject += f"  ({guard})"
    html = (
        f"<h2 style='font-family: -apple-system, sans-serif; margin:0'>"
        f"{title}</h2>"
        f"<p style='color:#666; font-size:13px'>guard={guard or '?'} · "
        f"source={source_key} · level={level} · at="
        f"{datetime.now(timezone.utc).isoformat()}</p>"
        f"<pre style='background:#f4f4f4; padding:12px; border-radius:6px; "
        f"font-family: ui-monospace, monospace; font-size:12px; "
        f"white-space: pre-wrap'>{(detail or '')[:2000]}</pre>"
        f"<p style='color:#888; font-size:11px'>"
        f"Dedup window: {DEDUP_WINDOW_HOURS}h. Reply STOP to silence "
        f"this stream.</p>"
    )
    try:
        resp = _send_via_resend(conf, subject, html)
        ok = 200 <= int(resp.get("status", 0)) < 300
    except Exception as e:                              # noqa: BLE001
        logger.warning("[G10] resend send failed: %r", e)
        resp = {"error": str(e)[:200]}
        ok = False

    # Audit log — best effort.
    if db is not None:
        try:
            await db.founder_alert_sends.insert_one({
                "source_key": source_key,
                "level":      level,
                "guard":      guard,
                "title":      title[:200],
                "sent_at":    datetime.now(timezone.utc),
                "delivered":  ok,
                "resend_response": resp,
            })
        except Exception:
            pass
    return {"sent": ok, "delivered": ok, "resend_response": resp}


async def send_daily_digest(db, hours: int = 24) -> dict:
    """Optional daily roll-up. Reads incidents + banners in the last N
    hours, sends ONE summary email. Called by the 60s housekeeping tick
    once per calendar day (dedup key = f'digest:{YYYY-MM-DD}')."""
    conf = _resend_conf()
    if not conf["enabled"] or db is None:
        return {"sent": False, "reason": "config_missing"}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    src_key = f"digest:{today}"
    if await _recent_dedup_hit(db, src_key, "digest"):
        return {"sent": False, "reason": "already_sent_today"}

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        incidents = [d async for d in db.incidents.find(
            {"detected_at": {"$gte": since}},
            {"_id": 0, "title": 1, "status": 1, "severity": 1,
             "detected_at": 1, "resolved_at": 1},
        ).sort("detected_at", -1).limit(50)]
    except Exception:
        incidents = []

    if not incidents:
        return {"sent": False, "reason": "no_incidents_in_window"}
    rows = "".join(
        f"<tr><td>{d.get('title','?')[:80]}</td>"
        f"<td>{d.get('severity','?')}</td>"
        f"<td>{d.get('status','?')}</td></tr>"
        for d in incidents
    )
    html = (
        f"<h2>AuremCTO daily digest — {today}</h2>"
        f"<p>{len(incidents)} incident(s) in last {hours}h.</p>"
        f"<table border='1' cellpadding='6' style='border-collapse:collapse'>"
        f"<tr><th>Title</th><th>Severity</th><th>Status</th></tr>"
        f"{rows}</table>"
    )
    try:
        resp = _send_via_resend(conf, f"AuremCTO digest {today}", html)
    except Exception as e:                              # noqa: BLE001
        return {"sent": False, "error": str(e)[:200]}
    await db.founder_alert_sends.insert_one({
        "source_key": src_key, "level": "digest", "guard": "G10",
        "title": f"digest {today}", "sent_at": datetime.now(timezone.utc),
        "delivered": True, "resend_response": resp,
    })
    return {"sent": True, "n_incidents": len(incidents)}
