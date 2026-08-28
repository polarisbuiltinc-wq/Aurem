"""
routers/admin_llm_usd_cap.py — R6 (2026-08-28).

  GET  /admin/llm/rate-table          model -> $/1M-token rates
  POST /admin/llm/rate-table
  GET  /admin/llm/usd-caps            per-plan monthly $ ceilings +
  POST /admin/llm/usd-caps            the global kill-switch cap
  GET  /admin/llm/usd-caps/spend/{user_id}   current-month spend
  POST /admin/llm/usd-caps/backfill   one-time migration (dry-run by
                                       default, see llm_usd_cap.py)

All admin-guarded. See services/llm_rate_table.py + services/llm_usd_cap.py
for the enforcement itself (wired into services/ora_chat_v2/llm_client.py).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from cto_services.auth import require_admin
from cto_services.db import get_db
from services import llm_rate_table, llm_usd_cap

router = APIRouter(prefix="/admin/llm", tags=["Admin · LLM Settings"])


class RateTableBody(BaseModel):
    rates: dict


class UsdCapsBody(BaseModel):
    per_plan: Optional[dict] = None
    global_kill_switch_usd: Optional[float] = None


class BackfillBody(BaseModel):
    dry_run: bool = True


async def _admin_id(authorization: Optional[str]) -> str:
    user = await require_admin(authorization)
    return user.get("user_id") if isinstance(user, dict) else str(user)


@router.get("/rate-table")
async def get_rate_table(authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    db = get_db()
    return {"ok": True, "rates": await llm_rate_table.get_rate_table(db)}


@router.post("/rate-table")
async def set_rate_table(body: RateTableBody, authorization: Optional[str] = Header(None)):
    admin_id = await _admin_id(authorization)
    if not body.rates:
        raise HTTPException(400, "rates must be a non-empty object")
    db = get_db()
    await llm_rate_table.set_rate_table(db, body.rates, updated_by=admin_id)
    return {"ok": True, "rates": await llm_rate_table.get_rate_table(db)}


@router.get("/usd-caps")
async def get_usd_caps(authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    db = get_db()
    return {"ok": True, **await llm_usd_cap.get_usd_caps(db)}


@router.post("/usd-caps")
async def set_usd_caps(body: UsdCapsBody, authorization: Optional[str] = Header(None)):
    admin_id = await _admin_id(authorization)
    db = get_db()
    await llm_usd_cap.set_usd_caps(
        db, per_plan=body.per_plan, global_kill_switch_usd=body.global_kill_switch_usd,
        updated_by=admin_id)
    return {"ok": True, **await llm_usd_cap.get_usd_caps(db)}


@router.get("/usd-caps/spend/{user_id}")
async def get_spend(user_id: str, authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    db = get_db()
    return {
        "ok": True, "user_id": user_id,
        "month_spend_usd": round(await llm_usd_cap.month_spend_usd(db, user_id=user_id), 4),
        "global_spend_usd": round(await llm_usd_cap.month_spend_usd(db, user_id=None), 4),
    }


@router.post("/usd-caps/backfill")
async def backfill(body: BackfillBody, authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    db = get_db()
    return await llm_usd_cap.backfill_current_month_from_usage_log(db, dry_run=body.dry_run)
