"""
services/g22_idle_spend_guard.py — 2026-08-19

Guard 22 — idle-window LLM spend alert.

Founder request (cost-leak investigation, 2026-08-19): after finding
`periodic_longcat_reprobe` and `integration_health_cron`'s Emergent-LLM
probe both burning real tokens on a fixed cadence with zero user
attribution, the founder asked for a standing safety net — not just a
one-time fix — that alerts if ANY LLM spend happens during a window
with zero real (non-system) user activity, so a future "just in case"
background LLM call doesn't silently become a new leak.

Every recurring background process that calls an LLM must log through
`cost_tracker.log_call()` under a `system:*` user_id so this guard can
see it. Known, already-reviewed system actors are allowlisted with a
tiny per-hour ceiling (their expected near-zero cost after the
cost-leak fix); spend from an UNKNOWN system actor, or spend above the
ceiling, opens an incident — same G1-G21 pattern (services/incident_log.py).
"""
from __future__ import annotations

import logging
import os
import time

from services.incident_log import open_incident

logger = logging.getLogger(__name__)

# Known, already-reviewed background actors and their expected ceiling.
# Anything spending under a KNOWN actor's ceiling is expected — the
# guard's job is to catch NEW/UNEXPECTED background LLM spend, not to
# alarm on the intentional (now near-zero-cost) health checks.
_KNOWN_SYSTEM_ACTORS: dict[str, float] = {
    "system:health_check": 0.01,   # integration_health_cron's gpt-5.4-mini probe
    "canary":              0.05,   # nightly ORA grounding canary (once/day burst)
}
_DEFAULT_UNKNOWN_ACTOR_CEILING_USD = 0.0  # any $ from an unreviewed actor = alert

CHECK_INTERVAL_S = int(os.environ.get("G22_CHECK_INTERVAL_S", "3600"))


def _is_real_user_id(user_id: str) -> bool:
    return bool(user_id) and not user_id.startswith("system:") \
        and user_id not in _KNOWN_SYSTEM_ACTORS


async def check_idle_window_spend(db, *, hours_back: float = 1.0) -> dict:
    """Scan the last `hours_back` hours of `ora_chat_usage`. If there
    was zero real-user activity in that window, verify any system-actor
    spend stayed within its known ceiling. Returns a summary dict;
    opens/resolves an incident as a side effect. Best-effort — never
    raises (a guard crashing must not take anything else down)."""
    result = {"real_user_activity": True, "system_spend_usd": 0.0,
              "flagged": False, "unknown_actors": []}
    if db is None:
        return result
    try:
        cutoff = time.time() - hours_back * 3600
        real_user_seen = False
        system_spend_by_actor: dict[str, float] = {}
        async for doc in db.ora_chat_usage.find(
            {"ts": {"$gte": cutoff}}, {"_id": 0, "user_id": 1, "cost_usd": 1},
        ):
            uid = doc.get("user_id", "")
            cost = float(doc.get("cost_usd") or 0.0)
            if _is_real_user_id(uid):
                real_user_seen = True
            else:
                key = uid if uid in _KNOWN_SYSTEM_ACTORS else (uid or "unknown")
                system_spend_by_actor[key] = system_spend_by_actor.get(key, 0.0) + cost

        result["real_user_activity"] = real_user_seen
        result["system_spend_usd"] = round(sum(system_spend_by_actor.values()), 6)

        if real_user_seen:
            # Real users were active — any spend is plausibly attributable
            # to normal usage, not an idle-window leak. Nothing to flag.
            await _resolve_if_open(db)
            return result

        problems = []
        for actor, spend in system_spend_by_actor.items():
            ceiling = _KNOWN_SYSTEM_ACTORS.get(actor, _DEFAULT_UNKNOWN_ACTOR_CEILING_USD)
            if spend > ceiling:
                problems.append(f"{actor}: ${spend:.4f} (ceiling ${ceiling:.4f})")
                if actor not in _KNOWN_SYSTEM_ACTORS:
                    result["unknown_actors"].append(actor)

        if problems:
            result["flagged"] = True
            await open_incident(
                db, guard="idle_llm_spend",
                title="LLM spend during a zero-active-user window",
                detail=(
                    f"Window: last {hours_back}h, no real user activity. "
                    f"Over-ceiling actors: {'; '.join(problems)}"
                ),
                source_key="idle_llm_spend_window",
                severity="warning",
                follow_up="Trace which background job logged this spend "
                          "and confirm it has an explicit reason + rate limit.",
            )
        else:
            await _resolve_if_open(db)
    except Exception as e:  # noqa: BLE001 — guard must never crash the app
        logger.warning("[G22] check_idle_window_spend best-effort failure: %r", e)
    return result


async def _resolve_if_open(db) -> None:
    from services.incident_log import resolve_incident
    await resolve_incident(
        db, source_key="idle_llm_spend_window",
        resolution="Spend returned within known ceilings / real user activity resumed.",
    )


async def schedule_idle_spend_guard(db_getter) -> None:
    """Hourly tick — same scheduler shape as `schedule_payment_reconciliation`.
    `db_getter` is a zero-arg callable (e.g. `lambda: app.state.db`)."""
    import asyncio
    await asyncio.sleep(120)  # let the app finish booting first
    while True:
        try:
            db = db_getter()
            if db is not None:
                await check_idle_window_spend(db)
        except Exception as e:  # noqa: BLE001
            logger.warning("[G22] schedule_idle_spend_guard tick failed: %r", e)
        try:
            await asyncio.sleep(CHECK_INTERVAL_S)
        except asyncio.CancelledError:
            return
