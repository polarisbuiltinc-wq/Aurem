"""
services/dora_metrics.py — 2026-08-24, Guard 22 (Phase 5.3 blueprint gap)

The 4 standard DORA metrics, computed as pure aggregation queries over
collections that already exist and are already populated in real
production traffic — `deploy_events` (services/deploy_logger.py),
`rollback_attempts` (services/rollback_two_phase.py), `incidents`
(services/incident_log.py, which already computes `mttr_s` per
incident on resolve). No new event-logging infrastructure, per Rule
12 — this file only reads what's already there.

Definitions used (DORA, simplified for AUREM's scale):
  - Deployment Frequency : deploy_events count in period / period_days
  - Lead Time for Changes : avg(timestamp - commit_timestamp) across
    deploy_events in period, in hours
  - Change Failure Rate  : % of deploy_events followed within
    `failure_window_hours` by either a rollback_attempts row or an
    incidents row (best-effort proxy — a deploy causing an incident
    or requiring rollback counts as a "failed" change)
  - MTTR : avg(mttr_s) across incidents.resolved in the period,
    already computed at resolve-time by incident_log.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def _parse_iso(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def compute_dora(db, *, period_days: int = 30, env: Optional[str] = "production",
                        failure_window_hours: int = 24) -> dict:
    if db is None:
        return {"error": "no_db"}
    since_dt = datetime.now(timezone.utc) - timedelta(days=period_days)
    since_iso = since_dt.isoformat()

    query: dict = {"timestamp": {"$gte": since_iso}}
    if env:
        query["env"] = env
    deploys = await db.deploy_events.find(query, {"_id": 0}).sort("timestamp", 1).to_list(2000)

    # Deployment Frequency
    deploy_count = len(deploys)
    deploy_freq_per_day = round(deploy_count / max(period_days, 1), 2)

    # Lead Time for Changes
    lead_times_h = []
    for d in deploys:
        committed = _parse_iso(d.get("commit_timestamp"))
        deployed = _parse_iso(d.get("timestamp"))
        if committed and deployed and deployed >= committed:
            lead_times_h.append((deployed - committed).total_seconds() / 3600)
    avg_lead_time_h = round(sum(lead_times_h) / len(lead_times_h), 2) if lead_times_h else None
    median_lead_time_h = None
    if lead_times_h:
        s = sorted(lead_times_h)
        mid = len(s) // 2
        median_lead_time_h = round(s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2, 2)

    # Change Failure Rate — did a rollback or incident follow within window?
    failed_deploys = 0
    for d in deploys:
        deployed = _parse_iso(d.get("timestamp"))
        if not deployed:
            continue
        window_end = (deployed + timedelta(hours=failure_window_hours)).isoformat()
        rb = await db.rollback_attempts.count_documents({
            "timestamp": {"$gte": d["timestamp"], "$lte": window_end},
        })
        inc = await db.incidents.count_documents({
            "detected_at_iso": {"$gte": d["timestamp"], "$lte": window_end},
        })
        if rb > 0 or inc > 0:
            failed_deploys += 1
    change_failure_rate_pct = (
        round(failed_deploys / deploy_count * 100, 1) if deploy_count else None
    )

    # MTTR — already computed per-incident at resolve time
    resolved = await db.incidents.find(
        {"status": "resolved", "resolved_at_iso": {"$gte": since_iso}, "mttr_s": {"$ne": None}},
        {"_id": 0, "mttr_s": 1},
    ).to_list(2000)
    mttr_values = [r["mttr_s"] for r in resolved if isinstance(r.get("mttr_s"), (int, float))]
    avg_mttr_h = round(sum(mttr_values) / len(mttr_values) / 3600, 2) if mttr_values else None

    return {
        "period_days": period_days,
        "env": env,
        "deployment_frequency": {
            "count": deploy_count,
            "per_day": deploy_freq_per_day,
        },
        "lead_time_for_changes": {
            "avg_hours": avg_lead_time_h,
            "median_hours": median_lead_time_h,
            "sample_size": len(lead_times_h),
        },
        "change_failure_rate": {
            "pct": change_failure_rate_pct,
            "failed_deploys": failed_deploys,
            "total_deploys": deploy_count,
            "failure_window_hours": failure_window_hours,
        },
        "mttr": {
            "avg_hours": avg_mttr_h,
            "sample_size": len(mttr_values),
        },
    }
