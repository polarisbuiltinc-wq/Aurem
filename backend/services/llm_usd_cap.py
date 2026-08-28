"""
services/llm_usd_cap.py — R6 (2026-08-28).

Real per-plan DOLLAR ceiling for the ORA v2 (DashScope/Qwen) LLM
client (services/ora_chat_v2/llm_client.py) — closes audit item #22
for that client. Enforced PRE-CALL inside llm_client.py's single
choke point, right after model resolution and BEFORE the real
provider call — zero tokens are ever spent past the cap.

This is layered ON TOP of (does not replace) the existing token/task
caps in services/usage.py, which gate the OTHER (legacy/orchestrator)
chat path, and the existing global hourly/daily breaker in
services/llm_cost_breaker.py, which gates the orchestrator/loop path.
Both stay exactly as-is — cheap secondary guards, belt and suspenders.

Admin-editable via GET/POST /admin/llm/usd-caps (Models & LLM settings
screen). Defaults below are deliberately conservative starting
points — tune from real observed cost once Qwen actually serves
non-founder traffic.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("aurem.llm_usd_cap")

DEFAULT_PER_PLAN_CAPS_USD: dict[str, Optional[float]] = {
    "free":    0.50,
    "starter": 3.00,
    "pro":     15.00,
    "team":    50.00,
    "founder": None,   # unlimited — matches every other cap in this codebase
}
DEFAULT_GLOBAL_KILL_SWITCH_USD = 200.00

_CAPS_DOC_ID = "llm_usd_caps"
LEDGER_COLLECTION = "ora_chat_v2_usd_ledger"
LIMIT_MESSAGE = "Monthly limit reached — upgrade to continue."


class LLMUsdCapExceeded(Exception):
    def __init__(self, message: str, cap_kind: str):
        self.message = message
        self.cap_kind = cap_kind
        super().__init__(message)


def _current_month_key(ts: Optional[float] = None) -> str:
    n = datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else datetime.now(timezone.utc)
    return f"{n.year:04d}-{n.month:02d}"


async def get_usd_caps(db) -> dict:
    caps = {"per_plan": dict(DEFAULT_PER_PLAN_CAPS_USD),
            "global_kill_switch_usd": DEFAULT_GLOBAL_KILL_SWITCH_USD}
    if db is None:
        return caps
    try:
        doc = await db.admin_settings.find_one({"_id": _CAPS_DOC_ID})
        if doc:
            if doc.get("per_plan"):
                caps["per_plan"].update(doc["per_plan"])
            if doc.get("global_kill_switch_usd") is not None:
                caps["global_kill_switch_usd"] = float(doc["global_kill_switch_usd"])
    except Exception:                                          # noqa: BLE001
        pass
    return caps


async def set_usd_caps(db, *, per_plan: Optional[dict] = None,
                        global_kill_switch_usd: Optional[float] = None,
                        updated_by: Optional[str] = None) -> None:
    updates: dict = {"updated_at": time.time(), "updated_by": updated_by}
    if per_plan is not None:
        updates["per_plan"] = {k: (float(v) if v is not None else None) for k, v in per_plan.items()}
    if global_kill_switch_usd is not None:
        updates["global_kill_switch_usd"] = float(global_kill_switch_usd)
    await db.admin_settings.update_one({"_id": _CAPS_DOC_ID}, {"$set": updates}, upsert=True)


async def _resolve_tier(db, user_id: str) -> str:
    """Founder/admin accounts are always unlimited — matches the
    exemption pattern used everywhere else in this codebase
    (services/usage.py founder-email check, is_admin/is_unlimited)."""
    try:
        user = await db.dev_users.find_one(
            {"user_id": user_id}, {"tier": 1, "is_unlimited": 1, "is_admin": 1})
    except Exception:                                          # noqa: BLE001
        user = None
    if not user:
        return "free"
    if user.get("is_unlimited") or user.get("is_admin"):
        return "founder"
    return user.get("tier") or "free"


async def month_spend_usd(db, *, user_id: Optional[str] = None, ts: Optional[float] = None) -> float:
    """Sum of real logged spend for the given (default: current) UTC
    month. user_id=None sums ORG-WIDE (the global kill-switch check)."""
    if db is None:
        return 0.0
    match: dict = {"month_key": _current_month_key(ts)}
    if user_id is not None:
        match["user_id"] = user_id
    total = 0.0
    async for row in db[LEDGER_COLLECTION].aggregate([
        {"$match": match},
        {"$group": {"_id": None, "sum": {"$sum": "$cost_usd"}}},
    ]):
        total = float(row.get("sum") or 0.0)
    return total


async def assert_within_usd_cap(db, *, user_id: str, est_cost_usd: float) -> None:
    """Raises LLMUsdCapExceeded if EITHER the org-wide global
    kill-switch OR the user's own per-plan monthly ceiling would be
    exceeded by this call. Tier is resolved internally so callers
    (llm_client.py) only ever need a user_id."""
    if db is None:
        return
    caps = await get_usd_caps(db)

    global_spend = await month_spend_usd(db, user_id=None)
    if global_spend + est_cost_usd > caps["global_kill_switch_usd"]:
        await _log_block(db, "global_kill_switch", user_id, "*", global_spend, caps["global_kill_switch_usd"])
        raise LLMUsdCapExceeded(LIMIT_MESSAGE, "global_kill_switch")

    tier = await _resolve_tier(db, user_id)
    plan_cap = caps["per_plan"].get(tier, caps["per_plan"].get("free"))
    if plan_cap is None:
        return  # unlimited plan (e.g. founder)

    user_spend = await month_spend_usd(db, user_id=user_id)
    if user_spend + est_cost_usd > plan_cap:
        await _log_block(db, "per_plan", user_id, tier, user_spend, plan_cap)
        raise LLMUsdCapExceeded(LIMIT_MESSAGE, "per_plan")


async def _log_block(db, cap_kind: str, user_id: str, tier: str, spend: float, cap: float) -> None:
    logger.warning(
        "[llm_usd_cap] GW_BLOCK_COST — cap=%s user_id=%s tier=%s spend=$%.4f cap=$%.4f",
        cap_kind, user_id, tier, spend, cap,
    )
    try:
        await db.guardrail_events.insert_one({
            "event": "GW_BLOCK_COST", "rule": "llm_usd_cap", "cap_kind": cap_kind,
            "user_id": user_id, "tier": tier, "spend_usd": round(spend, 4),
            "cap_usd": cap, "ts": datetime.now(timezone.utc),
        })
    except Exception:                                          # noqa: BLE001
        pass


async def record_usd_spend(db, *, user_id: str, model: str,
                            input_tokens: int, output_tokens: int, cost_usd: float) -> None:
    """Best-effort, never raises — a logging bug must never break a
    real chat turn that already succeeded and already spent real $."""
    if db is None:
        return
    try:
        await db[LEDGER_COLLECTION].insert_one({
            "user_id": user_id, "ts": time.time(), "month_key": _current_month_key(),
            "model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost_usd": cost_usd,
        })
    except Exception as e:                                     # noqa: BLE001
        logger.warning("record_usd_spend insert failed: %r", e)


async def backfill_current_month_from_usage_log(db, *, dry_run: bool = True) -> dict:
    """One-time (safe to re-run) migration: for every `ora_chat_usage`
    row in the current UTC month that has no matching ledger entry
    yet, price it with the current rate table and insert it into
    `ora_chat_v2_usd_ledger` — so the cap starts accurate for any
    user who already had real usage this month before the cap went
    live, instead of starting blind at $0.

    Idempotent: each backfilled ledger row is upserted keyed on
    `backfilled_from` = the source usage row's _id, so running this
    twice never double-counts.
    dry_run=True (default) computes and returns the totals WITHOUT
    writing anything — always dry-run in Preview first."""
    from services.llm_rate_table import get_rate_table, cost_usd as _cost_usd

    if db is None:
        return {"ok": False, "error": "no db"}

    month_key = _current_month_key()
    since = datetime(
        datetime.now(timezone.utc).year, datetime.now(timezone.utc).month, 1,
        tzinfo=timezone.utc,
    ).timestamp()

    rates = await get_rate_table(db)
    per_user_totals: dict[str, float] = {}
    rows_considered = 0
    rows_written = 0

    async for usage_row in db.ora_chat_usage.find({"ts": {"$gte": since}}):
        rows_considered += 1
        admin_id = usage_row.get("admin_id")
        model = usage_row.get("model") or "_default"
        tokens_in = int(usage_row.get("tokens_in") or 0)
        tokens_out = int(usage_row.get("tokens_out") or 0)
        cost = _cost_usd(rates, model, tokens_in, tokens_out)
        per_user_totals[admin_id] = per_user_totals.get(admin_id, 0.0) + cost

        if not dry_run:
            await db[LEDGER_COLLECTION].update_one(
                {"backfilled_from": str(usage_row["_id"])},
                {"$set": {
                    "user_id": admin_id, "ts": usage_row.get("ts") or time.time(),
                    "month_key": month_key, "model": model,
                    "input_tokens": tokens_in, "output_tokens": tokens_out,
                    "cost_usd": cost, "backfilled_from": str(usage_row["_id"]),
                }},
                upsert=True,
            )
            rows_written += 1

    return {
        "ok": True, "dry_run": dry_run, "month_key": month_key,
        "rows_considered": rows_considered, "rows_written": rows_written,
        "per_user_totals_usd": {k: round(v, 4) for k, v in per_user_totals.items()},
    }
