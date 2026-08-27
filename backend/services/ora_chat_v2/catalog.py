"""
services/ora_chat_v2/catalog.py — Admin ORA Chat rebuild, P4.

The model never invents actions — it may only CHOOSE from this
catalog (data), and the backend executes deterministic code, never
model-generated code. Risk tiers: READ (P5 tools, no approval),
REVERSIBLE (one-click approval, ON by default), SENSITIVE (before/
after diff + explicit approval, OFF by default via ORA_CHAT_SENSITIVE).
DESTRUCTIVE is intentionally not defined anywhere in this file.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# env-whitelisted keys only — never arbitrary env editing (P4 rule).
_SLO_ENV_KEYS = {
    "connect_repo_click", "auth_started", "app_granted",
    "chat_opened", "verified", "graph_built",
}
SET_ENV_WHITELIST = {f"FUNNEL_SLO_{k.upper()}_MIN" for k in _SLO_ENV_KEYS} | {
    "ORA_CHAT_ACTIONS", "ORA_CHAT_SENSITIVE", "ORA_CHAT_RATE_LIMIT_PER_HOUR",
    "ORA_CHAT_DAILY_TOKEN_CAP",
}
TOGGLE_FLAG_WHITELIST = {"explain_plain_english_v1"}

ACTION_CATALOG = {
    "trigger_digest": {
        "name": "Send a digest email now",
        "risk": "reversible",
        "description": "Send the leak or funnel digest email to the admin now, instead of waiting for its daily schedule.",
        "params_schema": {"kind": {"type": "string", "enum": ["leak", "funnel"]},
                           "to": {"type": "string"}},
        "enabled_by_default": True,
        "idempotency_seconds": 3600,
    },
    "unpark_backlog_item": {
        "name": "Unpark a backlog item",
        "risk": "reversible",
        "description": "Move a parked backlog item back to queued.",
        "params_schema": {"id": {"type": "string"}},
        "enabled_by_default": True,
        "idempotency_seconds": 0,
    },
    "park_backlog_item": {
        "name": "Park a backlog item",
        "risk": "reversible",
        "description": "Mark a backlog item as parked, with a reason note.",
        "params_schema": {"id": {"type": "string"}, "note": {"type": "string"}},
        "enabled_by_default": True,
        "idempotency_seconds": 0,
    },
    "create_backlog_item": {
        "name": "Create a backlog item",
        "risk": "reversible",
        "description": "Add a new item to the internal backlog.",
        "params_schema": {"title": {"type": "string"}, "note": {"type": "string"}},
        "enabled_by_default": True,
        "idempotency_seconds": 0,
    },
    "set_funnel_slo": {
        "name": "Change a funnel stage's stall SLO",
        "risk": "sensitive",
        "description": "Change how many minutes a signup can sit in a funnel stage before Journey Watch flags it as stalled.",
        "params_schema": {"stage": {"type": "string"}, "minutes": {"type": "integer"}},
        "enabled_by_default": False,
        "idempotency_seconds": 0,
    },
    "set_env": {
        "name": "Change a whitelisted config value",
        "risk": "sensitive",
        "description": "Change one of a small whitelist of safe config values (SLO minutes, ORA_CHAT_* caps).",
        "params_schema": {"key": {"type": "string"}, "value": {"type": "string"}},
        "enabled_by_default": False,
        "idempotency_seconds": 0,
    },
    "toggle_flag": {
        "name": "Toggle a feature flag",
        "risk": "sensitive",
        "description": "Turn a whitelisted feature flag on or off.",
        "params_schema": {"flag_name": {"type": "string"}, "value": {"type": "boolean"}},
        "enabled_by_default": False,
        "idempotency_seconds": 0,
    },
}


def catalog_prompt_block() -> str:
    """Rendered for the model's [ACTION CATALOG] context — includes
    SENSITIVE entries even when disabled, so the model can explain
    why it can't run one rather than pretending it doesn't exist."""
    sensitive_on = os.getenv("ORA_CHAT_SENSITIVE", "off").strip().lower() == "on"
    lines = ["[ACTION CATALOG — choose from this list only, propose at most 1 per turn]"]
    for action_id, spec in ACTION_CATALOG.items():
        disabled_note = ""
        if spec["risk"] == "sensitive" and not sensitive_on:
            disabled_note = " (currently DISABLED by the founder's env setting)"
        lines.append(f"- {action_id} [{spec['risk']}]{disabled_note}: {spec['description']}")
    lines.append("[/ACTION CATALOG]")
    return "\n".join(lines)


async def _check_idempotency(db, action_id: str, params: dict, window_s: int) -> bool:
    """True if this exact (action_id, params) already executed within
    the window — refused with a plain reason, not a silent retry."""
    if window_s <= 0:
        return False
    since = time.time() - window_s
    kind = (params or {}).get("kind")
    query = {"action_id": action_id, "event_type": "executed",
              "ts": {"$gte": since}}
    if kind:
        query["params.kind"] = kind
    row = await db.ora_chat_actions.find_one(query)
    return row is not None


async def execute_action(db, action_id: str, params: dict) -> dict:
    """Deterministic backend execution — never model-generated code.
    Returns {"ok": bool, "result"/"error": ...}."""
    spec = ACTION_CATALOG.get(action_id)
    if spec is None:
        return {"ok": False, "error": "undefined_action"}

    if spec["risk"] == "sensitive" and \
            os.getenv("ORA_CHAT_SENSITIVE", "off").strip().lower() != "on":
        return {"ok": False, "error": "sensitive_actions_disabled",
                "detail": "ORA_CHAT_SENSITIVE is off — no execution, no env change."}

    if await _check_idempotency(db, action_id, params, spec["idempotency_seconds"]):
        return {"ok": False, "error": "idempotency_window_active",
                "detail": f"Already ran within the last {spec['idempotency_seconds']}s."}

    try:
        if action_id == "trigger_digest":
            return await _trigger_digest(db, params)
        if action_id == "unpark_backlog_item":
            return await _set_backlog_status(db, params["id"], "queued", None)
        if action_id == "park_backlog_item":
            return await _set_backlog_status(db, params["id"], "parked", params.get("note"))
        if action_id == "create_backlog_item":
            return await _create_backlog_item(db, params["title"], params.get("note"))
        if action_id == "set_funnel_slo":
            return await _set_funnel_slo(db, params["stage"], int(params["minutes"]))
        if action_id == "set_env":
            return await _set_env(params["key"], params["value"])
        if action_id == "toggle_flag":
            return await _toggle_flag(db, params["flag_name"], bool(params["value"]))
    except KeyError as e:
        return {"ok": False, "error": "missing_param", "detail": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.warning("ora_chat_v2 execute_action(%s) failed: %r", action_id, e)
        return {"ok": False, "error": type(e).__name__, "detail": str(e)[:200]}
    return {"ok": False, "error": "unhandled_action"}


async def _trigger_digest(db, params: dict) -> dict:
    kind = params.get("kind")
    # Reuses the EXACT existing scheduler+Resend path (R15-style cost/infra
    # discipline) — these are the same internal functions the daily/weekly
    # cron already calls; both always send to ADMIN_EMAIL (no new
    # recipient-override system per "no new infra").
    if kind == "leak":
        from services.daily_digest import _run_once
        await _run_once()
    elif kind == "funnel":
        from services.journey_watch import _run_digest_once
        await _run_digest_once(db)
    else:
        return {"ok": False, "error": "invalid_kind"}
    return {"ok": True, "result": {"kind": kind, "sent_to": os.environ.get("ADMIN_EMAIL", "")}}


async def _set_backlog_status(db, backlog_id: str, status: str, note) -> dict:
    before = await db.ora_backlog_items.find_one({"backlog_id": backlog_id}, {"_id": 0})
    if before is None:
        return {"ok": False, "error": "not_found"}
    update = {"status": status, "updated_at": time.time()}
    if note is not None:
        update["note"] = note
    await db.ora_backlog_items.update_one({"backlog_id": backlog_id}, {"$set": update})
    return {"ok": True, "result": {"before": before, "after": {**before, **update}}}


async def _create_backlog_item(db, title: str, note) -> dict:
    import uuid
    backlog_id = uuid.uuid4().hex[:10]
    doc = {"backlog_id": backlog_id, "title": title, "note": note or "",
           "status": "queued", "created_at": time.time(), "updated_at": time.time()}
    await db.ora_backlog_items.insert_one(dict(doc))
    return {"ok": True, "result": doc}


async def _set_funnel_slo(db, stage: str, minutes: int) -> dict:
    if stage not in _SLO_ENV_KEYS:
        return {"ok": False, "error": "unknown_stage"}
    key = f"FUNNEL_SLO_{stage.upper()}_MIN"
    before = os.environ.get(key)
    os.environ[key] = str(minutes)
    await db.ora_chat_config.update_one(
        {"key": key}, {"$set": {"key": key, "value": str(minutes),
                                  "updated_at": time.time()}}, upsert=True)
    return {"ok": True, "result": {"key": key, "before": before, "after": str(minutes)}}


async def _set_env(key: str, value: str) -> dict:
    if key not in SET_ENV_WHITELIST:
        return {"ok": False, "error": "key_not_whitelisted"}
    before = os.environ.get(key)
    os.environ[key] = str(value)
    return {"ok": True, "result": {"key": key, "before": before, "after": str(value)}}


async def _toggle_flag(db, flag_name: str, value: bool) -> dict:
    if flag_name not in TOGGLE_FLAG_WHITELIST:
        return {"ok": False, "error": "flag_not_whitelisted"}
    before_doc = await db.feature_flags.find_one({"flag": flag_name}, {"_id": 0})
    before = (before_doc or {}).get("enabled")
    await db.feature_flags.update_one(
        {"flag": flag_name}, {"$set": {"enabled": value}}, upsert=True)
    from services.feature_flags import invalidate_cache
    invalidate_cache()
    return {"ok": True, "result": {"flag": flag_name, "before": before, "after": value}}
