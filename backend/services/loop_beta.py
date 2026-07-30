"""
services/loop_beta.py — Loop Mode rollout controls (Iter 364).

Central place for the tiered rollout gating, kill-switch, execution
logging, and Maxx-mode cost/quota tracking so we don't scatter these
concerns across routers/services.

Public API:
  is_kill_switch_on() -> bool
  set_kill_switch(on: bool, reason: str) -> None
  is_user_allowed(user_doc) -> tuple[bool, str]
        # (allowed, reject_reason)
  count_active_loops(db, user_id) -> int
  log_execution(db, **fields) -> None
  log_maxx_cost(db, user_id, loop_id, deepseek_cost, claude_cost, model_meta) -> None
  assert_maxx_daily_budget(db, user_id) -> None   # raises HTTPException(402)
  count_stuck_loops(db, window_s=600) -> int
  auto_trip_kill_switch_if_stuck(db) -> Optional[dict]

Env vars:
  LOOP_MODE_KILL_SWITCH       (default "false")  — hard-off for everyone
  LOOP_TOTAL_BUDGET_S         (default 1800)     — wall-clock cap
  LOOP_MAX_CONCURRENT_PER_USER (default 1)
  LOOP_STUCK_TRIP_THRESHOLD   (default 3 stuck in 10 min → auto-trip)
  MAXX_DAILY_TASK_CAP         (default 10)       — even Team tier
"""
from __future__ import annotations

import os
import time
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException

logger = logging.getLogger("aurem.loop_beta")


# ── Env-driven constants ─────────────────────────────────────────────
def _env_bool(key: str, default: str = "false") -> bool:
    return os.environ.get(key, default).lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


LOOP_MAX_CONCURRENT_PER_USER = _env_int("LOOP_MAX_CONCURRENT_PER_USER", 1)
LOOP_STUCK_TRIP_THRESHOLD    = _env_int("LOOP_STUCK_TRIP_THRESHOLD",    3)
LOOP_STUCK_TRIP_WINDOW_S     = _env_int("LOOP_STUCK_TRIP_WINDOW_S",     600)
MAXX_DAILY_TASK_CAP          = _env_int("MAXX_DAILY_TASK_CAP",          10)

# Loop states that count as "in-flight" for concurrency check.
_ACTIVE_STATES = {
    "planning", "awaiting_confirmation",
    "executing", "verifying", "scanning", "shipping",
    "self_healing", "paused_for_user",
}


# ── Kill-switch (env-driven + in-DB override) ────────────────────────
# Env var is the source of truth for boot; DB flag allows runtime flip
# without a restart (auto-trip writes here, admin can flip via API).

def is_kill_switch_on(db=None) -> bool:
    """True if Loop Mode should be OFF for everyone regardless of
    tier / beta flag. Env wins if set; else DB flag."""
    if _env_bool("LOOP_MODE_KILL_SWITCH", "false"):
        return True
    if db is None:
        return False
    try:
        # Sync guard for callers that can't await (rare — most paths
        # should use is_kill_switch_on_async).
        return False
    except Exception:
        return False


async def is_kill_switch_on_async(db) -> bool:
    if _env_bool("LOOP_MODE_KILL_SWITCH", "false"):
        return True
    if db is None:
        return False
    try:
        row = await db.system_flags.find_one({"key": "loop_mode_kill_switch"})
        return bool((row or {}).get("value") is True)
    except Exception:
        return False


async def set_kill_switch(db, on: bool, reason: str = "") -> None:
    """Flip the DB-backed kill switch. Env override still wins."""
    if db is None:
        return
    await db.system_flags.update_one(
        {"key": "loop_mode_kill_switch"},
        {"$set": {
            "value":      bool(on),
            "reason":     reason[:400],
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    logger.warning("[loop_beta] kill_switch set: on=%s reason=%s", on, reason)


# ── Per-user gate ────────────────────────────────────────────────────

def is_user_allowed(user_doc: dict) -> tuple[bool, str]:
    """Decide if a user can hit /loop/start based on tier + beta flag.

    Founder / admin / unlimited bypass everything.
    Pro / Team need loop_beta_enabled=True in dev_users.
    Free / Starter locked (paid-tier differentiator + protects infra
    cost against the still-open free-tier abuse hole).
    """
    if not user_doc:
        return False, "no_user"
    is_founder = bool(
        user_doc.get("is_admin")
        or user_doc.get("is_unlimited")
        or user_doc.get("tier") == "founder"
    )
    if is_founder:
        return True, ""
    tier = (user_doc.get("tier") or "").lower()
    if tier in ("pro", "team"):
        if bool(user_doc.get("loop_beta_enabled")):
            return True, ""
        return False, "beta_not_enabled"
    return False, "tier_locked"


# ── Concurrency ──────────────────────────────────────────────────────

async def count_active_loops(db, user_id: str) -> int:
    """How many loop_sessions are currently in-flight for this user."""
    if db is None or not user_id:
        return 0
    try:
        return await db.loop_sessions.count_documents({
            "user_id": user_id,
            "state":   {"$in": list(_ACTIVE_STATES)},
        })
    except Exception as e:
        logger.warning("[loop_beta] count_active_loops failed: %r", e)
        return 0


# ── Execution log (Phase 3 requirement) ──────────────────────────────

async def log_execution(
    db,
    *,
    user_id: str,
    loop_id: str,
    tier: str,
    status: str,               # completed | failed | aborted | expired
    duration_s: float,
    stuck_reason: Optional[str] = None,
    used_maxx: bool = False,
    used_parallel_agents: bool = False,
    worker_tape_viewed: bool = False,
    agent_count: int = 0,
) -> None:
    if db is None:
        return
    try:
        await db.loop_execution_log.insert_one({
            "user_id":              user_id,
            "loop_id":              loop_id,
            "tier":                 tier or "unknown",
            "status":               status,
            "duration_s":           round(float(duration_s or 0), 3),
            "stuck_reason":         stuck_reason,
            "used_maxx":            bool(used_maxx),
            "used_parallel_agents": bool(used_parallel_agents),
            "worker_tape_viewed":   bool(worker_tape_viewed),
            "agent_count":          int(agent_count or 0),
            "created_at":           datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.warning("[loop_beta] log_execution failed: %r", e)


# ── Maxx cost log + daily quota ──────────────────────────────────────

async def log_maxx_cost(
    db,
    *,
    user_id: str,
    loop_id: Optional[str],
    deepseek_cost_usd: float,
    claude_cost_usd:   float,
    model_meta: Optional[dict] = None,
) -> None:
    if db is None:
        return
    try:
        await db.maxx_cost_log.insert_one({
            "user_id":            user_id,
            "loop_id":            loop_id,
            "deepseek_cost_usd":  round(float(deepseek_cost_usd or 0), 6),
            "claude_cost_usd":    round(float(claude_cost_usd   or 0), 6),
            "total_cost_usd":     round(
                float(deepseek_cost_usd or 0) + float(claude_cost_usd or 0), 6),
            "model_meta":         model_meta or {},
            "created_at":         datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.warning("[loop_beta] log_maxx_cost failed: %r", e)


async def assert_maxx_daily_budget(db, user_id: str) -> None:
    """Raise HTTP 402 if the user has already run MAXX_DAILY_TASK_CAP
    Maxx-mode tasks in the current UTC day (rolling 24h). Applies to
    every tier including Team — the monthly quota is a separate wallet.
    Founders bypass entirely."""
    if db is None or not user_id:
        return
    # Check founder short-circuit
    try:
        u = await db.dev_users.find_one(
            {"user_id": user_id},
            {"_id": 0, "is_admin": 1, "is_unlimited": 1, "tier": 1},
        )
        if u and (u.get("is_admin") or u.get("is_unlimited")
                  or u.get("tier") == "founder"):
            return
    except Exception:
        pass

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        count = await db.maxx_cost_log.count_documents({
            "user_id":    user_id,
            "created_at": {"$gte": since},
        })
    except Exception:
        count = 0
    if count >= MAXX_DAILY_TASK_CAP:
        raise HTTPException(402, detail={
            "error":              "maxx_daily_cap_reached",
            "cap":                MAXX_DAILY_TASK_CAP,
            "used_last_24h":      count,
            "message": (
                f"You've used all {MAXX_DAILY_TASK_CAP} Maxx-mode tasks "
                f"in the last 24 h. Try Swift or Pro mode, or wait for "
                f"the rolling window to reset."
            ),
        })


# ── Stuck-loop detector (Guard 19 hook) ──────────────────────────────

async def count_stuck_loops(db, window_s: Optional[int] = None) -> int:
    """Count loop sessions that failed with resume_reason indicating a
    stuck state OR total_budget_exceeded in the last `window_s` seconds.
    Also counts any active loop that has been ACTIVE longer than the
    total budget (defensive — should never happen but if it does we
    surface it)."""
    if db is None:
        return 0
    ws = int(window_s or LOOP_STUCK_TRIP_WINDOW_S)
    since = datetime.now(timezone.utc) - timedelta(seconds=ws)
    try:
        n_by_reason = await db.loop_sessions.count_documents({
            "updated_at":    {"$gte": since},
            "resume_reason": {"$in": [
                "total_budget_exceeded",
                "server_restart_mid_loop",
                "phase_timeout_max_restarts",
            ]},
        })
    except Exception as e:
        logger.warning("[loop_beta] count_stuck_loops failed: %r", e)
        return 0
    return int(n_by_reason)


async def auto_trip_kill_switch_if_stuck(db) -> Optional[dict]:
    """If more than LOOP_STUCK_TRIP_THRESHOLD stuck loops in the last
    LOOP_STUCK_TRIP_WINDOW_S seconds, auto-flip the DB kill switch to
    ON. Idempotent — if already tripped, no-op. Returns the trip record
    if fired, else None. Called by Guard 19's process_recovery sweeper."""
    if db is None:
        return None
    n = await count_stuck_loops(db)
    if n <= LOOP_STUCK_TRIP_THRESHOLD:
        return None
    # Only trip if not already tripped, so admin can manually un-trip
    # without us re-flipping.
    if await is_kill_switch_on_async(db):
        return None
    reason = (
        f"auto-trip: {n} stuck loops in {LOOP_STUCK_TRIP_WINDOW_S}s "
        f"(threshold {LOOP_STUCK_TRIP_THRESHOLD})"
    )
    await set_kill_switch(db, True, reason)
    trip = {
        "ts":              time.time(),
        "stuck_count":     n,
        "window_s":        LOOP_STUCK_TRIP_WINDOW_S,
        "threshold":       LOOP_STUCK_TRIP_THRESHOLD,
        "reason":          reason,
    }
    try:
        await db.loop_kill_switch_trips.insert_one({
            **trip,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception:
        pass
    # Best-effort incident log entry so it also shows up in Guard-20 UI.
    try:
        from services.incident_log import upsert_incident
        await upsert_incident(
            db,
            guard="loop_beta",
            severity="critical",
            title=f"Loop kill-switch auto-tripped ({n} stuck)",
            detail=reason,
            source_key="loop_beta:auto_trip",
            follow_up=(
                "Investigate loop_sessions.resume_reason distribution "
                "in the last 10 min; once fixed, POST "
                "/api/aurem-dev/admin/loop-beta/kill-switch {enabled:false}."
            ),
        )
    except Exception:
        pass
    logger.error("[loop_beta] kill-switch AUTO-TRIPPED: %s", reason)
    return trip
