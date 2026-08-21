"""routers/admin_maintenance.py — admin-gated planned-maintenance
toggle + outage incident tracker.

  GET  /aurem-dev/admin/maintenance            current settings
  POST /aurem-dev/admin/maintenance/settings    update settings (partial)
  GET  /aurem-dev/admin/maintenance/incidents   outage incident list + stats
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from cto_services.auth import require_admin_dep
from services.maintenance import (
    get_maintenance_cache, set_maintenance_state,
    list_outage_incidents, outage_stats,
)
from routers._admin_common import _require_admin

router = APIRouter(
    prefix="/aurem-dev/admin/maintenance",
    tags=["Admin-maintenance"],
    dependencies=[Depends(require_admin_dep)],
)


class MaintenanceSettings(BaseModel):
    manual_enabled: Optional[bool] = None
    message: Optional[str] = None
    window: Optional[str] = None
    outage_threshold_s: Optional[int] = None


@router.get("")
async def get_settings():
    return get_maintenance_cache()


@router.post("/settings")
async def update_settings(body: MaintenanceSettings,
                           authorization: Optional[str] = Header(None)):
    from cto_services.db import get_db
    user = await _require_admin(authorization)
    db = get_db()
    return await set_maintenance_state(
        db,
        manual_enabled=body.manual_enabled,
        message=body.message,
        window=body.window,
        outage_threshold_s=body.outage_threshold_s,
        updated_by=user.get("email"),
    )


@router.get("/incidents")
async def get_incidents(limit: int = 100):
    from cto_services.db import get_db
    db = get_db()
    rows = await list_outage_incidents(db, limit=limit)
    stats = await outage_stats(db)
    return {"incidents": rows, "stats": stats}


__all__ = ["router"]
