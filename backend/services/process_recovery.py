"""Guard 19 — process-level auto-recovery (Iter 362, always active).

Supervisor already restarts backend/frontend on crash/OOM
(autorestart=true — the pod's supervisor conf is READ-ONLY). This
module adds the application-level half of G19:

  • Every boot records a row in `process_boots` (ts, git sha, reason).
  • Restart-loop detection: >= LOOP_THRESHOLD boots inside LOOP_WINDOW_S
    → a CRITICAL alert into the EXISTING topup_alerts banner (via G10
    once wired) + a `process_loop_trips` row. A tight crash loop hides
    the real bug behind an endlessly "restarting" pod, so we surface it.
  • Heartbeat: a monotonic timestamp bumped by the app; exposed on
    /api/healthz so an external monitor (G9) can tell "process up but
    event loop wedged".
  • QA row helpers: restarts_7d, last boot reason, loop trips (7d).

All Mongo writes are best-effort — recovery telemetry must NEVER be
able to crash the very boot it is trying to record.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LOOP_WINDOW_S = int(os.getenv("RECOVERY_LOOP_WINDOW_S", "600"))     # 10 min
LOOP_THRESHOLD = int(os.getenv("RECOVERY_LOOP_THRESHOLD", "3"))     # 3 boots

# Heartbeat — bumped by the app; read by /api/healthz + external monitor.
_LAST_HEARTBEAT: float = time.time()


def beat() -> None:
    global _LAST_HEARTBEAT
    _LAST_HEARTBEAT = time.time()


def heartbeat_age_s() -> float:
    return round(time.time() - _LAST_HEARTBEAT, 1)


def last_heartbeat_iso() -> str:
    return datetime.fromtimestamp(_LAST_HEARTBEAT, tz=timezone.utc).isoformat()


def _git_sha() -> str:
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd="/app",
            stderr=subprocess.DEVNULL, timeout=3).decode().strip()
    except Exception:
        return os.getenv("BUILD_HASH", "unknown")


async def record_boot(db, *, reason: str = "supervisor_start") -> dict:
    """Record this boot + detect a restart loop. Returns a summary dict.
    Called once from the FastAPI lifespan startup."""
    beat()
    now = time.time()
    summary = {"recorded": False, "boots_in_window": 0, "loop_detected": False}
    if db is None:
        return summary
    try:
        doc = {
            "boot_id": f"boot_{uuid.uuid4().hex[:10]}",
            "ts": now,
            "ts_iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "sha": _git_sha(),
            "reason": reason,
            "pid": os.getpid(),
        }
        await db.process_boots.insert_one(dict(doc))
        summary["recorded"] = True

        window_start = now - LOOP_WINDOW_S
        boots_in_window = await db.process_boots.count_documents(
            {"ts": {"$gte": window_start}})
        summary["boots_in_window"] = boots_in_window

        if boots_in_window >= LOOP_THRESHOLD:
            summary["loop_detected"] = True
            await _trip_loop(db, boots_in_window, doc["sha"])
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[G19] record_boot best-effort failure: %r", e)
    return summary


async def _trip_loop(db, boots: int, sha: str) -> None:
    """Log a loop-trip row + raise a CRITICAL alert in the EXISTING
    topup_alerts banner. Deduped to one active alert at a time."""
    now = time.time()
    try:
        await db.process_loop_trips.insert_one({
            "trip_id": f"trip_{uuid.uuid4().hex[:10]}",
            "ts": now,
            "ts_iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "boots_in_window": boots,
            "window_s": LOOP_WINDOW_S,
            "sha": sha,
        })
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[G19] loop-trip log failure: %r", e)

    logger.error(
        "[G19] RESTART LOOP DETECTED — %d boots in %ds (sha=%s). "
        "The process keeps dying; supervisor auto-restart is masking a "
        "hard crash. Investigate before it burns the pod.",
        boots, LOOP_WINDOW_S, sha)

    # Iter 363 · Guard 20 — auto-create a postmortem incident (its own
    # dedup). Done BEFORE the banner-alert dedup below so a repeated
    # trip (banner already active) still guarantees the incident exists.
    try:
        from services.incident_log import open_incident
        await open_incident(
            db, guard="G19", source_key="process_recovery",
            title=f"Restart loop: {boots} boots in {LOOP_WINDOW_S // 60}min",
            detail=(f"Backend restarted {boots} times inside {LOOP_WINDOW_S}s "
                    f"(sha={sha}). Supervisor keeps restarting a crashing "
                    f"process — hard boot failure masked."),
            follow_up="Check backend.err.log; fix boot crash; confirm stable.")
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[G20] incident hook (loop) failure: %r", e)

    # Surface in the EXISTING critical-alerts banner. Dedup: skip if an
    # active loop alert already exists (auto-resolves via QA/cron only
    # when boots settle — see resolve_if_stable).
    try:
        existing = await db.topup_alerts.find_one(
            {"integration_id": "process_recovery", "status": "active"},
            {"_id": 1})
        if existing:
            await db.topup_alerts.update_one(
                {"integration_id": "process_recovery", "status": "active"},
                {"$set": {"last_seen": now,
                          "summary": f"Restart loop: {boots} boots in "
                                     f"{LOOP_WINDOW_S // 60}min"},
                 "$inc": {"seen_count": 1}})
            return
        day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        await db.topup_alerts.insert_one({
            "alert_id": f"al_{uuid.uuid4().hex[:10]}",
            "alert_key": f"process_recovery::critical::{day}",
            "integration_id": "process_recovery",
            "integration_name": "Process Auto-Recovery",
            "severity": "critical",
            "summary": f"Restart loop: {boots} boots in {LOOP_WINDOW_S // 60}min",
            "detail": (f"Backend restarted {boots} times inside "
                       f"{LOOP_WINDOW_S}s (sha={sha}). Supervisor keeps "
                       f"restarting a crashing process — this masks a hard "
                       f"boot failure (bad migration, import error, OOM). "
                       f"Check backend.err.log."),
            "fix_hint": "tail -n 200 /var/log/supervisor/backend.err.log",
            "day_key": day,
            "first_seen": now,
            "last_seen": now,
            "seen_count": 1,
            "status": "active",
            "email_sent": False,
        })
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[G19] critical-alert raise failure: %r", e)


async def resolve_if_stable(db) -> bool:
    """If no boots occurred in the last LOOP_WINDOW_S, auto-resolve any
    active restart-loop alert. Returns True if it resolved one."""
    if db is None:
        return False
    try:
        boots = await db.process_boots.count_documents(
            {"ts": {"$gte": time.time() - LOOP_WINDOW_S}})
        if boots < LOOP_THRESHOLD:
            res = await db.topup_alerts.update_many(
                {"integration_id": "process_recovery", "status": "active"},
                {"$set": {"status": "resolved", "resolved_at": time.time(),
                          "resolved_by": "auto_recovery_stable"}})
            if res.modified_count:
                try:
                    from services.incident_log import resolve_incident
                    await resolve_incident(
                        db, source_key="process_recovery",
                        resolution="Boots settled below loop threshold; "
                                   "process stable again.",
                        root_cause="Transient restart burst (deploy/hot-reload "
                                   "or recovered crash).")
                except Exception:
                    pass
            return bool(res.modified_count)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[G19] resolve_if_stable failure: %r", e)
    return False


async def recovery_status(db) -> dict:
    """QA row payload: restarts (7d), last boot reason/time, loop trips
    (7d), heartbeat age, current window boot count."""
    out = {
        "guard": "G19",
        "supervisor_autorestart": True,   # pod conf: autorestart=true (read-only)
        "heartbeat_age_s": heartbeat_age_s(),
        "last_heartbeat": last_heartbeat_iso(),
        "loop_window_s": LOOP_WINDOW_S,
        "loop_threshold": LOOP_THRESHOLD,
        "restarts_7d": None,
        "boots_in_window": None,
        "last_boot": None,
        "loop_trips_7d": None,
        "loop_active": False,
    }
    if db is None:
        return out
    week = time.time() - 7 * 86400
    try:
        out["restarts_7d"] = await db.process_boots.count_documents(
            {"ts": {"$gte": week}})
        out["boots_in_window"] = await db.process_boots.count_documents(
            {"ts": {"$gte": time.time() - LOOP_WINDOW_S}})
        last = await db.process_boots.find_one(
            {}, {"_id": 0}, sort=[("ts", -1)])
        if last:
            out["last_boot"] = {"ts_iso": last.get("ts_iso"),
                                "reason": last.get("reason"),
                                "sha": last.get("sha")}
        out["loop_trips_7d"] = await db.process_loop_trips.count_documents(
            {"ts": {"$gte": week}})
        out["loop_active"] = bool(await db.topup_alerts.find_one(
            {"integration_id": "process_recovery", "status": "active"},
            {"_id": 1}))
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[G19] recovery_status failure: %r", e)
    return out
