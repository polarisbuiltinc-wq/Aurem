"""routers/maintenance_public.py — public, unauthenticated maintenance
status probe. The frontend's global MaintenanceGate polls this to
decide whether to show the "System Maintenance" screen. Must stay
reachable with NO auth (logged-out visitors need it too) and answer
from the in-memory cache only — no DB round-trip on the hot path.
"""
from __future__ import annotations

from fastapi import APIRouter

from services.maintenance import get_maintenance_cache

router = APIRouter(prefix="/aurem-dev", tags=["Maintenance"])


@router.get("/maintenance/status")
async def maintenance_status():
    c = get_maintenance_cache()
    return {
        "manual_enabled": c["manual_enabled"],
        "message": c["message"],
        "window": c["window"],
        "updated_at": c["updated_at"],
    }


__all__ = ["router"]
