"""
routers/admin_vanguard.py — admin UI for per-mode Vanguard config.

Two endpoints, both admin/founder-gated:

  GET  /admin/vanguard/config  →  current { enabled, levels, updated_at, updated_by }
  POST /admin/vanguard/config  →  upsert  { enabled, levels }

The body's `levels` key MUST be a dict of `{swift|pro|maxx: OFF|CRITICAL|HIGH}`.
Unknown modes are ignored; unknown levels coerce to the safest default
(CRITICAL) by the service layer.

The frontend at `/admin/vanguard` calls these to drive the three radio
pills + global master switch.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from cto_services.auth import current_dev
from services.vanguard_config import get_config, save_config

router = APIRouter(prefix="/admin/vanguard", tags=["Admin / Vanguard"])

MAX_BODY_BYTES = 10_000  # 10 KB — config payloads are tiny


def _require_admin(me: dict) -> None:
    if not (me.get("is_admin") or me.get("tier") == "founder"):
        raise HTTPException(403, "Admin access required")


async def _limit_body_size(request: Request) -> None:
    cl = request.headers.get("content-length")
    if cl and int(cl) > MAX_BODY_BYTES:
        raise HTTPException(413, "Request body too large")


@router.get("/config")
async def read_vanguard_config(
    authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    _require_admin(me)
    cfg = await get_config()
    # `updated_at` may be a tz-aware datetime; ISO-string it for JSON.
    ts = cfg.get("updated_at")
    if hasattr(ts, "isoformat"):
        cfg["updated_at"] = ts.isoformat()
    return {"ok": True, "config": cfg}


class _ConfigBody(BaseModel):
    enabled: bool
    levels:  dict  # {swift|pro|maxx: OFF|CRITICAL|HIGH}


@router.post("/config", dependencies=[Depends(_limit_body_size)])
async def write_vanguard_config(
    body: _ConfigBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    _require_admin(me)
    saved = await save_config(
        enabled=body.enabled,
        levels=body.levels or {},
        updated_by=me.get("user_id"),
    )
    ts = saved.get("updated_at")
    if hasattr(ts, "isoformat"):
        saved["updated_at"] = ts.isoformat()
    return {"ok": True, "config": saved}