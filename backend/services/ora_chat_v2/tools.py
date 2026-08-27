"""
services/ora_chat_v2/tools.py — Admin ORA Chat rebuild, P5.

The ONLY 6 read-only tools exposed to the model (function calling).
They double as the READ risk-tier of the P4 action catalog — no
approval needed, they execute instantly. A model attempt to call
anything not in TOOL_SCHEMAS is rejected here and logged.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_INVESTIGATION_DIR = "/app/memory"


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_funnel_stats",
        "description": "7-day signup funnel stage counts, stalls, hard-breaks.",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "integer", "default": 7}}}}},
    {"type": "function", "function": {
        "name": "get_alerts",
        "description": "Recent admin bell/notification events, counted by kind.",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "integer", "default": 7}}}}},
    {"type": "function", "function": {
        "name": "get_backlog",
        "description": "Parked + queued internal backlog items, with status.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_production_status",
        "description": "Current live build hash + health.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "read_investigation",
        "description": "Read a memory/investigation_*.md file by name.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "get_recent_loop_failures",
        "description": "Count + last error class of failed fix-loops in the window.",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "integer", "default": 7}}}}},
]

_KNOWN_TOOLS = {s["function"]["name"] for s in TOOL_SCHEMAS}


async def get_funnel_stats(db, days: int = 7) -> dict:
    from services.journey_watch import compute_journey_watch_card
    return await compute_journey_watch_card(db, period_days=int(days or 7))


async def get_alerts(db, days: int = 7) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=int(days or 7))
    by_kind: dict[str, int] = {}
    cur = db.health_notifications.find(
        {"created_at_dt": {"$gte": since}}, {"_id": 0, "category": 1, "name": 1})
    rows = 0
    async for row in cur:
        rows += 1
        k = row.get("category") or "other"
        by_kind[k] = by_kind.get(k, 0) + 1
    return {"days": days, "total": rows, "by_kind": by_kind}


async def get_backlog(db) -> dict:
    cur = db.ora_backlog_items.find(
        {}, {"_id": 0, "backlog_id": 1, "title": 1, "status": 1, "note": 1,
             "updated_at": 1},
    ).sort("updated_at", -1).limit(30)
    return {"items": [row async for row in cur]}


async def get_production_status(db) -> dict:
    from services.ora_chat_v2.state_block import _production_status
    return await _production_status(db)


_SAFE_NAME_RE = None


async def read_investigation(db, name: str) -> dict:
    import re
    global _SAFE_NAME_RE
    if _SAFE_NAME_RE is None:
        _SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")
    name = (name or "").strip()
    if not name.startswith("investigation_") or not name.endswith(".md") \
            or not _SAFE_NAME_RE.match(name) or "/" in name or ".." in name:
        return {"error": "invalid_name", "detail": "must be memory/investigation_*.md"}
    path = os.path.join(_INVESTIGATION_DIR, name)
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(_INVESTIGATION_DIR)):
        return {"error": "invalid_name"}
    if not os.path.isfile(real):
        return {"error": "not_found", "name": name}
    with open(real, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(20000)
    return {"name": name, "content": content}


async def get_recent_loop_failures(db, days: int = 7) -> dict:
    since = time.time() - int(days or 7) * 86400
    cur = db.loop_sessions.find(
        {"state": "failed", "updated_at": {"$gte": since}},
        {"_id": 0, "error_code": 1, "updated_at": 1, "loop_id": 1},
    ).sort("updated_at", -1).limit(20)
    rows = [r async for r in cur]
    return {"days": days, "count": len(rows),
            "last_error_class": rows[0].get("error_code") if rows else None}


_DISPATCH = {
    "get_funnel_stats": get_funnel_stats,
    "get_alerts": get_alerts,
    "get_backlog": get_backlog,
    "get_production_status": get_production_status,
    "read_investigation": read_investigation,
    "get_recent_loop_failures": get_recent_loop_failures,
}


async def execute_tool(db, name: str, args: dict) -> dict:
    """Dispatch a model tool-call. Anything not in TOOL_SCHEMAS is
    rejected + logged — the model cannot call an undefined tool."""
    if name not in _KNOWN_TOOLS:
        logger.warning("ora_chat_v2: rejected undefined tool call: %s", name)
        return {"error": "undefined_tool", "name": name}
    fn = _DISPATCH[name]
    try:
        kwargs = {k: v for k, v in (args or {}).items()
                  if k in ("days", "name")}
        return await fn(db, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("ora_chat_v2 tool %s failed: %r", name, e)
        return {"error": type(e).__name__, "detail": str(e)[:200]}
