"""
services/journey_watch.py — Journey Watch (2026-08-27, Phase 3)

A 5-minute watchdog over the signup→first-loop funnel. Reuses ONLY
existing infra — no new collection, no new endpoint, no new vendor:

  - Reads `github_funnel_events` (connect_repo_click, github_auth_started,
    app_install_granted, app_install_denied — session/user-keyed) and
    `funnel_events` (project_connected, graph_built, first_loop_started —
    user_id-keyed) written by Phase 0's instrumentation.
  - Writes to the SAME `health_notifications` / `health_check_state`
    collections `services/health_notifier.py` already uses, so the
    existing cockpit bell (`NotificationBell.jsx`, polling the existing
    `/admin/status/notifications` endpoint) renders Journey Watch rows
    with ZERO frontend changes.
  - Hard-breaks (`app_install_denied`) also fire through the existing
    `services/founder_alerts.send_founder_alert` G10 Resend channel for
    an immediate out-of-band ping, with its built-in dedup.

SLO table (minutes) — deliberately in minutes, not the 24h scale
`funnel_nudge_cron.py` uses for its email nudges. That cron already
covers "stuck for a day+"; Journey Watch is for acute in-session
stalls a founder would want to know about within the hour. Thresholds
are env-overridable so they can be tuned without a code change.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_INTERVAL_S = int(os.environ.get("JOURNEY_WATCH_INTERVAL_S", "300"))  # 5 min
_LOOKBACK_HOURS = int(os.environ.get("JOURNEY_WATCH_LOOKBACK_HOURS", "24"))
_ESCALATE_MULTIPLIER = 2.0

# Waterfall order — the funnel stages Journey Watch actively tracks.
# `connect_repo_click` is the first tracked checkpoint (deliberate —
# time-to-first-click after signup varies wildly for normal reasons and
# is already covered by the 24h+ email nudge cron; this watch is for
# stalls WITHIN an already-started connect attempt).
STAGE_ORDER = (
    "connect_repo_click",
    "github_auth_started",
    "app_install_granted",
    "project_connected",
    "graph_built",
    "first_loop_started",
)

_EARLY_STAGES = ("connect_repo_click", "github_auth_started", "app_install_granted")
_LATE_STAGES = ("project_connected", "graph_built", "first_loop_started")

STAGE_LABELS = {
    "connect_repo_click":  "clicking Connect Repo",
    "github_auth_started": "the GitHub authorization screen",
    "app_install_granted": "picking a repo after granting GitHub access",
    "project_connected":   "waiting for the codebase graph to build",
    "graph_built":         "starting their first task",
}

# SLO (minutes) from reaching `stage` to reaching the NEXT stage.
SLO_MINUTES = {
    "connect_repo_click":  int(os.environ.get("JW_SLO_CONNECT_CLICK_MIN", "5")),
    "github_auth_started": int(os.environ.get("JW_SLO_AUTH_STARTED_MIN", "10")),
    "app_install_granted": int(os.environ.get("JW_SLO_APP_GRANTED_MIN", "10")),
    "project_connected":   int(os.environ.get("JW_SLO_PROJECT_CONNECTED_MIN", "15")),
    "graph_built":         int(os.environ.get("JW_SLO_GRAPH_BUILT_MIN", "30")),
}


def _stage_idx(stage: Optional[str]) -> int:
    if stage is None or stage not in STAGE_ORDER:
        return -1
    return STAGE_ORDER.index(stage)


async def _latest_stage_for_user(db, user_id: str) -> tuple[Optional[str], Optional[float]]:
    """Most-advanced tracked stage this user has reached + the epoch
    they reached it. Returns (None, None) if they haven't reached even
    the first tracked stage (`connect_repo_click`) yet."""
    latest_stage: Optional[str] = None
    latest_ts: Optional[float] = None

    cur = db.github_funnel_events.find(
        {"user_id": user_id, "stage": {"$in": list(_EARLY_STAGES)}},
        {"_id": 0, "stage": 1, "ts": 1},
    )
    async for row in cur:
        stage = row.get("stage")
        ts = row.get("ts")
        idx = _stage_idx(stage)
        if idx > _stage_idx(latest_stage):
            latest_stage, latest_ts = stage, ts
        elif idx == _stage_idx(latest_stage) and ts and (latest_ts is None or ts > latest_ts):
            latest_ts = ts

    cur = db.funnel_events.find(
        {"user_id": user_id, "event_type": {"$in": list(_LATE_STAGES)}},
        {"_id": 0, "event_type": 1, "ts_epoch": 1},
    )
    async for row in cur:
        stage = row.get("event_type")
        ts = row.get("ts_epoch")
        idx = _stage_idx(stage)
        if idx > _stage_idx(latest_stage):
            latest_stage, latest_ts = stage, ts
        elif idx == _stage_idx(latest_stage) and ts and (latest_ts is None or ts > latest_ts):
            latest_ts = ts

    return latest_stage, latest_ts


async def _fire_bell(db, *, check_id: str, name: str, category: str,
                      from_state: str, to_state: str, detail: str) -> None:
    """Same shape `health_notifier._fire_notification` writes — the bell
    UI needs nothing new to render this."""
    now_dt = datetime.now(timezone.utc)
    row = {
        "notif_id":      uuid.uuid4().hex[:12],
        "check_id":      check_id,
        "name":          name,
        "category":      category,
        "from_state":    from_state,
        "to_state":      to_state,
        "detail":        detail[:400],
        "created_at":    now_dt.isoformat(),
        "created_at_dt": now_dt,
        "read":          False,
    }
    try:
        await db.health_notifications.insert_one(row)
    except Exception:  # noqa: BLE001
        logger.warning("[journey-watch] failed to write bell row for %s", check_id, exc_info=True)


def _who(user: dict) -> str:
    return user.get("email") or user.get("user_id") or "unknown user"


async def _check_stall(db, user: dict, now: float) -> None:
    user_id = user["user_id"]
    latest_stage, latest_ts = await _latest_stage_for_user(db, user_id)
    check_id = f"journey:{user_id}"
    state_row = await db.health_check_state.find_one({"_id": check_id}) or {}
    was_stalled = state_row.get("last_known") == "stalled"
    stalled_stage = state_row.get("stalled_stage")

    if latest_stage is None or latest_stage == "first_loop_started":
        # Nothing to watch yet, or fully activated. If a PRIOR stall
        # exists, that means they just progressed past it — resolve.
        if was_stalled:
            await _fire_bell(
                db, check_id=check_id,
                name=f"Signup resumed — {STAGE_LABELS.get(stalled_stage, stalled_stage)}",
                category="journey_watch",
                from_state=stalled_stage or "unknown", to_state="green",
                detail=f"{_who(user)} progressed past the stall — resolved.",
            )
            await db.health_check_state.update_one(
                {"_id": check_id},
                {"$set": {"last_known": "resolved", "stalled_stage": None,
                          "escalated": False}},
            )
        return

    # A previously-stalled user who has since moved to a DIFFERENT
    # stage than the one they were stuck on — auto-resolve first, we'll
    # re-evaluate the new stage below on the next branch.
    if was_stalled and stalled_stage != latest_stage:
        await _fire_bell(
            db, check_id=check_id,
            name=f"Signup resumed — {STAGE_LABELS.get(stalled_stage, stalled_stage)}",
            category="journey_watch",
            from_state=stalled_stage or "unknown", to_state="green",
            detail=f"{_who(user)} progressed past the stall — resolved.",
        )
        await db.health_check_state.update_one(
            {"_id": check_id},
            {"$set": {"last_known": "resolved", "stalled_stage": None, "escalated": False}},
        )
        was_stalled = False

    slo_min = SLO_MINUTES.get(latest_stage)
    if slo_min is None or latest_ts is None:
        return  # terminal tracked stage with no further SLO, or no timestamp
    elapsed_min = (now - latest_ts) / 60.0
    if elapsed_min < slo_min:
        return  # still within budget

    escalated = bool(state_row.get("escalated")) if was_stalled and stalled_stage == latest_stage else False

    if not was_stalled or stalled_stage != latest_stage:
        # First time crossing SLO for this stage.
        await _fire_bell(
            db, check_id=check_id,
            name=f"Signup stalled — {STAGE_LABELS.get(latest_stage, latest_stage)}",
            category="journey_watch",
            from_state=latest_stage, to_state="red",
            detail=(f"{_who(user)} has been stuck at '{STAGE_LABELS.get(latest_stage, latest_stage)}' "
                    f"for {round(elapsed_min)} min (SLO {slo_min} min)."),
        )
        await db.health_check_state.update_one(
            {"_id": check_id},
            {"$set": {"last_known": "stalled", "stalled_stage": latest_stage,
                      "stalled_since": latest_ts, "escalated": False}},
            upsert=True,
        )
        return

    if not escalated and elapsed_min >= slo_min * _ESCALATE_MULTIPLIER:
        await _fire_bell(
            db, check_id=check_id,
            name=f"Signup stalled (escalated) — {STAGE_LABELS.get(latest_stage, latest_stage)}",
            category="journey_watch",
            from_state=latest_stage, to_state="red",
            detail=(f"⏫ Still stuck: {_who(user)} at '{STAGE_LABELS.get(latest_stage, latest_stage)}' "
                    f"for {round(elapsed_min)} min (2x SLO of {slo_min} min)."),
        )
        await db.health_check_state.update_one(
            {"_id": check_id}, {"$set": {"escalated": True}},
        )


async def _scan_hardbreaks(db, now: float) -> None:
    """Immediate alert on `app_install_denied` — no SLO wait. Cursor is
    stored in the existing `health_check_state` collection (row
    `_id="journey_watch_cursor"`) so we never re-alert the same event
    across ticks without needing a new collection."""
    cursor_row = await db.health_check_state.find_one({"_id": "journey_watch_cursor"}) or {}
    last_scan_ts = cursor_row.get("last_hardbreak_scan_ts")
    if last_scan_ts is None:
        # First run ever — don't backfill history, only alert forward.
        await db.health_check_state.update_one(
            {"_id": "journey_watch_cursor"},
            {"$set": {"last_hardbreak_scan_ts": now}},
            upsert=True,
        )
        return

    max_ts_seen = last_scan_ts
    cur = db.github_funnel_events.find(
        {"stage": "app_install_denied", "ts": {"$gt": last_scan_ts}},
        {"_id": 0, "event_id": 1, "user_id": 1, "session_id": 1, "ts": 1, "meta": 1},
    )
    async for row in cur:
        ts = row.get("ts") or now
        max_ts_seen = max(max_ts_seen, ts)
        user_id = row.get("user_id") or row.get("session_id") or "unknown"
        reason = (row.get("meta") or {}).get("reason", "unknown")
        event_id = row.get("event_id") or f"{user_id}:{ts}"

        who = user_id
        if row.get("user_id"):
            u = await db.dev_users.find_one({"user_id": row["user_id"]}, {"_id": 0, "email": 1})
            who = (u or {}).get("email") or row["user_id"]

        await _fire_bell(
            db, check_id=f"journey:hardbreak:{event_id}",
            name="GitHub install declined",
            category="journey_watch",
            from_state="app_install_denied", to_state="red",
            detail=f"🚫 {who} did not complete the GitHub App install (reason={reason}).",
        )
        try:
            from services.founder_alerts import send_founder_alert
            await send_founder_alert(
                db,
                source_key=f"journey_watch:hardbreak:{event_id}",
                title="GitHub install declined",
                detail=f"{who} did not complete the GitHub App install (reason={reason}).",
                level="warning",
                guard="Journey-Watch",
            )
        except Exception:  # noqa: BLE001
            logger.warning("[journey-watch] founder_alert send failed", exc_info=True)

    await db.health_check_state.update_one(
        {"_id": "journey_watch_cursor"},
        {"$set": {"last_hardbreak_scan_ts": max_ts_seen}},
        upsert=True,
    )


async def _tick_once() -> None:
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return
    now = time.time()

    await _scan_hardbreaks(db, now)

    since = now - (_LOOKBACK_HOURS * 3600)
    cur = db.dev_users.find(
        {"created_at": {"$gte": since}},
        {"_id": 0, "user_id": 1, "email": 1, "created_at": 1,
         "is_admin": 1, "is_unlimited": 1, "tier": 1},
    )
    async for u in cur:
        if u.get("is_admin") or u.get("is_unlimited") or u.get("tier") == "founder":
            continue
        if not u.get("user_id"):
            continue
        try:
            await _check_stall(db, u, now)
        except Exception:  # noqa: BLE001 — one bad user must never kill the tick
            logger.warning("[journey-watch] stall check failed for %s", u.get("user_id"), exc_info=True)


async def journey_watch_loop() -> None:
    """Long-lived cron, same _supervise() pattern as every other
    background task in main.py."""
    logger.info("[journey-watch] starting · interval=%ds lookback=%dh",
                _INTERVAL_S, _LOOKBACK_HOURS)
    await asyncio.sleep(5.0)
    while True:
        try:
            await _tick_once()
        except Exception:  # noqa: BLE001
            logger.exception("[journey-watch] tick crashed")
        await asyncio.sleep(_INTERVAL_S)


# ── Optional weekly "Quiet Funnel Digest" ───────────────────────────
# Founder's ask was "only if cheap" — reuses the exact same Resend
# helper `services/leak_digest.py` already uses, no new email system.

def _digest_enabled() -> bool:
    v = (os.environ.get("ENABLE_FUNNEL_DIGEST_CRON") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


async def build_funnel_digest(db) -> dict:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)
    since_epoch = since.timestamp()

    signups = await db.dev_users.count_documents({"created_at": {"$gte": since_epoch}})
    stalls = await db.health_notifications.count_documents({
        "category": "journey_watch", "to_state": "red",
        "created_at_dt": {"$gte": since},
    })
    resolved = await db.health_notifications.count_documents({
        "category": "journey_watch", "to_state": "green",
        "created_at_dt": {"$gte": since},
    })
    hardbreaks = await db.health_notifications.count_documents({
        "category": "journey_watch", "check_id": {"$regex": "^journey:hardbreak:"},
        "created_at_dt": {"$gte": since},
    })
    return {
        "generated_at": now.isoformat(),
        "window_days": 7,
        "signups": signups,
        "stalls_flagged": stalls,
        "stalls_resolved": resolved,
        "hardbreaks": hardbreaks,
    }


def _render_funnel_digest_text(d: dict) -> str:
    return "\n".join([
        "AUREM — Weekly Quiet Funnel Digest",
        d["generated_at"],
        (f"This week: {d['signups']} new signups. Journey Watch flagged "
         f"{d['stalls_flagged']} stall(s), {d['stalls_resolved']} resolved "
         f"on their own, {d['hardbreaks']} GitHub install decline(s)."),
    ])


async def _run_digest_once(db) -> None:
    from services.daily_digest import _send_via_resend
    digest = await build_funnel_digest(db)
    body = _render_funnel_digest_text(digest)
    logger.info(f"🧭 QUIET FUNNEL DIGEST\n{body}")
    admin_email = os.environ.get("ADMIN_EMAIL", "").strip()
    if admin_email:
        sent = await _send_via_resend(admin_email, "AUREM — Weekly Quiet Funnel Digest", body)
        if not sent:
            logger.info("RESEND_API_KEY not set — funnel digest only logged.")


# ── P7 — admin Journey Watch card (2026-08-27) ──────────────────────
# Reuses ONLY `health_notifications` (already written by `_fire_bell`
# above) + `health_check_state` (already written by `_check_stall`) —
# no new collection, no new endpoint-side write path.

async def compute_journey_watch_card(db, period_days: int = 7) -> dict:
    """7-day rollup for the admin Overview card: stalls/resolves/
    hard-breaks + a per-stage breakdown + currently-still-stalled
    users, plus the 5 most recent rows for the card's "view in bell"
    deep link to make sense of at a glance."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=period_days)

    stalls = await db.health_notifications.count_documents({
        "category": "journey_watch", "to_state": "red",
        "check_id": {"$not": {"$regex": "^journey:hardbreak:"}},
        "created_at_dt": {"$gte": since},
    })
    resolved = await db.health_notifications.count_documents({
        "category": "journey_watch", "to_state": "green",
        "created_at_dt": {"$gte": since},
    })
    hardbreaks = await db.health_notifications.count_documents({
        "category": "journey_watch", "check_id": {"$regex": "^journey:hardbreak:"},
        "created_at_dt": {"$gte": since},
    })

    by_stage: dict[str, int] = {}
    cur = db.health_notifications.find(
        {"category": "journey_watch", "to_state": "red",
         "check_id": {"$not": {"$regex": "^journey:hardbreak:"}},
         "created_at_dt": {"$gte": since}},
        {"_id": 0, "from_state": 1},
    )
    async for row in cur:
        stage = row.get("from_state") or "unknown"
        by_stage[stage] = by_stage.get(stage, 0) + 1
    stage_breakdown = [
        {"stage": s, "label": STAGE_LABELS.get(s, s), "count": c}
        for s, c in sorted(by_stage.items(), key=lambda kv: -kv[1])
    ]

    active_stalls = await db.health_check_state.count_documents({
        "_id": {"$regex": "^journey:"}, "last_known": "stalled",
    })

    recent_rows = await db.health_notifications.find(
        {"category": "journey_watch"},
        {"_id": 0, "notif_id": 1, "name": 1, "detail": 1, "to_state": 1,
         "created_at": 1},
    ).sort("created_at", -1).limit(5).to_list(5)

    return {
        "period_days":     period_days,
        "generated_at":    now.isoformat(),
        "stalls_flagged":  stalls,
        "stalls_resolved": resolved,
        "hardbreaks":      hardbreaks,
        "active_stalls":   active_stalls,
        "by_stage":        stage_breakdown,
        "recent":          recent_rows,
    }


async def schedule_funnel_digest_cron() -> None:
    if not _digest_enabled():
        logger.info("funnel_digest_cron disabled (ENABLE_FUNNEL_DIGEST_CRON=0)")
        return
    target_weekday = int(os.environ.get("FUNNEL_DIGEST_WEEKDAY_UTC", "0"))  # Monday
    target_hour = int(os.environ.get("FUNNEL_DIGEST_HOUR_UTC", "8"))  # 1h after leak digest
    while True:
        now = datetime.now(timezone.utc)
        days_ahead = (target_weekday - now.weekday()) % 7
        next_run = (now + timedelta(days=days_ahead)).replace(
            hour=target_hour, minute=0, second=0, microsecond=0,
        )
        if next_run <= now:
            next_run += timedelta(days=7)
        wait_s = (next_run - now).total_seconds()
        try:
            await asyncio.sleep(wait_s)
            from cto_services.db import get_db
            db = get_db()
            if db is not None:
                await _run_digest_once(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("funnel digest scheduler crash")
            await asyncio.sleep(3600)
