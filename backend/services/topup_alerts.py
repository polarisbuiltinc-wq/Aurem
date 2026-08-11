"""services/topup_alerts.py — Top-up Alerts engine.

When the integration_health snapshot lands (either via the daily 06:00 UTC
cron or a manual `/admin/integrations/refresh`), this module:

  1. Walks every probe result.
  2. Classifies it as `critical` / `warning` / `nominal` using THRESHOLDS
     below.
  3. Dedupes against `db.topup_alerts` so the founder gets at most one
     email per (integration_id + severity + day).
  4. Emails the founder via Resend (reusing the same auth pattern as
     `daily_digest._send_via_resend`).
  5. Persists every active alert so the admin UI can render a banner
     and offer a Dismiss button.

The module is intentionally side-effect-free if `RESEND_API_KEY` /
`ADMIN_EMAIL` are unset — alerts are still persisted, the UI banner
still works, just no outbound email.
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

from services.http import ext_request
from services.http.client import ExternalCallError

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Threshold logic
# ────────────────────────────────────────────────────────────────────────

# Integrations that are central to the chat / code-task pipeline — any
# disruption here gets escalated to `critical` even if the probe only
# returns `warn`.
_CRITICAL_INTEGRATIONS = {
    "openrouter", "emergent_llm", "stripe", "mongodb",
}

# Money-keyword regex hits on a `warn` summary push it up to `critical`.
_LOW_BALANCE_PATTERNS = [
    re.compile(r"(?i)credits?\s+(low|exhausted|out|depleted)"),
    re.compile(r"(?i)balance\s+(low|out|depleted)"),
    re.compile(r"(?i)\$\s*0\."),                  # < $1
    re.compile(r"(?i)0\s+credits?"),
]


def classify(result: dict) -> Optional[str]:
    """Return `'critical'` / `'warning'` / `None`.

    `result` is one row from `services.integration_health.run_all_probes`.
    """
    status  = (result.get("status")  or "").lower()
    summary = (result.get("summary") or "")
    iid     = (result.get("id")      or "").lower()
    name    = (result.get("name")    or iid)
    if status == "broken":
        return "critical"
    if status == "missing":
        # Optional integrations (no key configured) — not actionable.
        return None
    if status == "warn":
        # Money / credits keywords → escalate to critical.
        for pat in _LOW_BALANCE_PATTERNS:
            if pat.search(summary):
                return "critical"
        # Core path integrations: any warn is critical.
        if iid in _CRITICAL_INTEGRATIONS:
            return "critical"
        return "warning"
    # status == "ok" or unknown → not an alert.
    _ = name  # silence linter — we use `name` in callers via the dict
    return None


# ────────────────────────────────────────────────────────────────────────
# Persistence + dedupe
# ────────────────────────────────────────────────────────────────────────


def _day_key(ts: float | None = None) -> str:
    """UTC YYYY-MM-DD key for per-day dedupe."""
    ts = ts if ts is not None else time.time()
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


async def upsert_alerts_from_snapshot(db, snap: dict) -> list[dict]:
    """Inspect `snap.results`, upsert one row per (integration, severity)
    into `db.topup_alerts`. Returns the list of NEW alerts that were
    not seen earlier in ANY currently-active window (i.e. the ones we
    should email about).

    Iter 212m-70 — collapsed the per-result `find_one` existence check
    + the three branches' single `update_one` / `update_many` /
    `insert_one` writes into one `bulk_write`.  Round-trips drop from
    1 + 2·N to 1 + 1, regardless of how many integrations the snapshot
    contains.

    Session 6 · Item 2 (2026-07-31) — dedup was per-day, so a
    long-standing critical (e.g. Tavily unpaid credits) piled up one
    new row EVERY DAY for the same underlying incident.  Real repro:
    Tavily had 18 active `critical` rows in prod, one per day going
    back to 2026-06-24.  Founder saw 2+ duplicates in the /admin
    banner.  Fix: dedup key changed from
    `{integration}::{severity}::{day}` → `{integration}::{severity}`
    when an already-active row exists.  A new row is only inserted
    when NO active row exists — i.e. an integration that was
    healthy comes back broken.  Prevents the daily-pile-up while
    keeping the "new incident after resolution" story intact.
    """
    from pymongo import InsertOne, UpdateOne, UpdateMany

    results = (snap or {}).get("results") or []
    day = _day_key((snap or {}).get("generated_at"))
    new_alerts: list[dict] = []
    if not results:
        return new_alerts

    # 1. Pre-fetch ALL currently-active rows for every (integration,
    #    severity) we might touch, in a single query.  A row counts as
    #    "already covering this alert" if `status="active"` regardless
    #    of which day originally opened it — long-standing incidents
    #    must NOT spawn a new row per day.
    candidate_bases: list[tuple[str, str]] = []   # (integration_id, severity)
    classified:     list[tuple[dict, str | None, str, tuple[str, str] | None]] = []
    for r in results:
        severity = classify(r)
        base = (r.get("id"), severity) if severity else None
        # Today's alert_key — still used when we DO insert a new row,
        # so the unique index prevents same-day dupes AND the record
        # keeps a day marker for reporting/audit.
        key = f"{r.get('id')}::{severity}::{day}" if severity else ""
        classified.append((r, severity, key, base))
        if base:
            candidate_bases.append(base)

    # Query: any active row for (integration_id, severity) — day-agnostic.
    existing_active_bases: set[tuple[str, str]] = set()
    active_row_ids_by_base: dict[tuple[str, str], str] = {}
    if candidate_bases:
        or_clauses = [
            {"integration_id": iid, "severity": sev}
            for (iid, sev) in candidate_bases
        ]
        cur = db.topup_alerts.find(
            {"$or": or_clauses, "status": "active"},
            {"_id": 0, "integration_id": 1, "severity": 1, "alert_key": 1},
        )
        async for d in cur:
            iid = d.get("integration_id")
            sev = d.get("severity")
            ak  = d.get("alert_key")
            if iid and sev:
                existing_active_bases.add((iid, sev))
                # Remember one canonical alert_key per base so we can
                # `update_one` the CURRENT row without scanning again.
                if (iid, sev) not in active_row_ids_by_base and ak:
                    active_row_ids_by_base[(iid, sev)] = ak

    # 2. Build a single bulk_write list across all 3 branches.
    ops: list = []
    now = time.time()
    for r, severity, key, base in classified:
        if not severity:
            # Iter 351 — resolve ALL active alerts for a now-healthy
            # integration, not just today's. The old `day_key: day`
            # filter left yesterday's alerts stuck "active" forever
            # (founder repro: Firecrawl probe LIVE but a stale timeout
            # alert stayed in the banner until manual dismiss).
            ops.append(UpdateMany(
                {
                    "integration_id": r.get("id"),
                    "status":         "active",
                },
                {"$set": {"status": "resolved", "resolved_at": now,
                          "resolved_by": "auto_probe"}},
            ))
            continue
        # Session 6 · Item 2 — active row already covers this
        # (integration, severity)?  Just refresh it, do NOT create a
        # new daily row.  If the existing alert_key differs from
        # today's, we still refresh — that keeps the founder-facing
        # `first_seen` stable at the true incident-open time.
        if base in existing_active_bases:
            existing_ak = active_row_ids_by_base.get(base) or key
            ops.append(UpdateOne(
                {"alert_key": existing_ak},
                {"$set": {
                    "last_seen": now,
                    "summary":   r.get("summary", "")[:300],
                    "detail":    r.get("detail", "")[:500],
                    "status":    "active",
                    "last_seen_day_key": day,   # audit trail
                },
                 "$inc": {"seen_count": 1}},
            ))
            continue
        # First sighting for this (integration, severity) — no active
        # row exists → open a brand new row for today.
        doc = {
            "alert_id":          f"al_{uuid.uuid4().hex[:10]}",
            "alert_key":         key,
            "integration_id":    r.get("id"),
            "integration_name":  r.get("name"),
            "severity":          severity,
            "summary":           r.get("summary", "")[:300],
            "detail":            r.get("detail", "")[:500],
            "fix_hint":          r.get("fix_hint", "")[:300],
            "day_key":           day,
            "first_seen":        now,
            "last_seen":         now,
            "seen_count":        1,
            "status":            "active",
            "email_sent":        False,
        }
        ops.append(InsertOne(doc))
        new_alerts.append({k: v for k, v in doc.items() if k != "_id"})

    if ops:
        try:
            await db.topup_alerts.bulk_write(ops, ordered=False)
        except Exception as e:
            logger.warning(f"topup_alerts bulk_write: {e!r}")
            new_alerts = []   # don't claim new alerts we failed to persist

    # Iter 363 · Guard 20 — funnel every critical integration alert into
    # the postmortem incident log, and auto-resolve linked incidents for
    # integrations that recovered this cycle.
    try:
        from services.incident_log import open_incident, resolve_incident
        healthy_ids = {r.get("id") for r, sev, _, _ in classified if not sev}
        for hid in healthy_ids:
            await resolve_incident(
                db, source_key=f"integration:{hid}",
                resolution="Integration probe healthy again (auto-resolved).")
        for a in new_alerts:
            if a.get("severity") == "critical":
                await open_incident(
                    db, guard="integration_health",
                    source_key=f"integration:{a.get('integration_id')}",
                    title=f"{a.get('integration_name')}: {a.get('summary')}"[:180],
                    detail=a.get("detail", ""),
                    follow_up=a.get("fix_hint", ""))
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[G20] integration incident hook failure: %r", e)
    return new_alerts


# ────────────────────────────────────────────────────────────────────────
# Email delivery
# ────────────────────────────────────────────────────────────────────────


async def _send_via_resend(to_email: str, subject: str, body: str) -> bool:
    """Reuses the same Resend auth pattern as daily_digest. Returns True
    on a 2xx send."""
    key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not key:
        return False
    sender = os.environ.get(
        "ALERT_FROM",
        os.environ.get("DIGEST_FROM", "AUREM <onboarding@resend.dev>"),
    )
    try:
        await ext_request(
            "resend", "POST", "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "from":    sender,
                "to":      [to_email],
                "subject": subject,
                "text":    body,
            },
        )
        return True
    except ExternalCallError as e:
        logger.warning(f"topup_alerts resend send failed: {e!r}")
        return False
    except Exception as e:
        logger.warning(f"topup_alerts resend send failed: {e!r}")
        return False


def _render_email(new_alerts: list[dict]) -> tuple[str, str]:
    """Build a single email summarising every new alert. Returns
    `(subject, body)`."""
    crits = [a for a in new_alerts if a["severity"] == "critical"]
    warns = [a for a in new_alerts if a["severity"] == "warning"]
    if crits and warns:
        subject = (
            f"🚨 AUREM — {len(crits)} critical + "
            f"{len(warns)} warning integration alert(s)"
        )
    elif crits:
        subject = f"🚨 AUREM — {len(crits)} critical integration alert(s)"
    else:
        subject = f"⚠️ AUREM — {len(warns)} integration warning(s)"

    lines: list[str] = [
        "AUREM has detected the following integration issues that"
        " need your attention:",
        "",
    ]
    if crits:
        lines.append("🚨 CRITICAL")
        for a in crits:
            lines.append(f"  • {a['integration_name']}: {a['summary']}")
            if a.get("fix_hint"):
                lines.append(f"      → fix: {a['fix_hint']}")
        lines.append("")
    if warns:
        lines.append("⚠️ WARNING")
        for a in warns:
            lines.append(f"  • {a['integration_name']}: {a['summary']}")
            if a.get("fix_hint"):
                lines.append(f"      → fix: {a['fix_hint']}")
        lines.append("")
    lines.append(
        "View live status & dismiss alerts at the admin Overview tab."
    )
    return subject, "\n".join(lines)


async def email_and_mark(db, new_alerts: list[dict]) -> bool:
    """Send a single grouped email for all new alerts. Marks each alert
    `email_sent: True` on success. Returns whether the email actually went
    out."""
    if not new_alerts:
        return False
    to_email = (os.environ.get("ADMIN_EMAIL") or "").strip()
    if not to_email:
        logger.info(
            f"topup_alerts: {len(new_alerts)} new alert(s) but ADMIN_EMAIL "
            "not set — alerts persisted, no email sent"
        )
        return False
    subject, body = _render_email(new_alerts)
    sent = await _send_via_resend(to_email, subject, body)
    if sent:
        try:
            await db.topup_alerts.update_many(
                {"alert_key": {"$in": [a["alert_key"] for a in new_alerts]}},
                {"$set": {"email_sent": True, "emailed_at": time.time()}},
            )
        except Exception as e:
            logger.warning(f"topup_alerts mark-sent: {e!r}")
    return sent


# ────────────────────────────────────────────────────────────────────────
# Public entry point
# ────────────────────────────────────────────────────────────────────────


async def process_snapshot(db, snap: dict) -> dict:
    """End-to-end: ingest a fresh integration_health snapshot, persist
    alerts, send one grouped email per refresh. Idempotent thanks to
    the (integration_id, severity, day) dedupe key."""
    new_alerts = await upsert_alerts_from_snapshot(db, snap)
    emailed = False
    if new_alerts:
        emailed = await email_and_mark(db, new_alerts)
    return {
        "new_alert_count": len(new_alerts),
        "emailed":         emailed,
        "alerts":          new_alerts,
    }
