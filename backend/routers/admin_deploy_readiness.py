"""
routers/admin_deploy_readiness.py — Deploy Readiness card endpoint
(Option A, 2026-08-24). Admin-only, advisory only — see
services/deploy_readiness.py for the Rule C rationale.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from cto_services.auth import require_admin_dep
from services.deploy_readiness import get_deploy_readiness

router = APIRouter(
    prefix="/admin/deploy-readiness",
    tags=["Admin-Deploy-Readiness"],
    dependencies=[Depends(require_admin_dep)],
)


@router.get("")
async def deploy_readiness() -> dict:
    return await get_deploy_readiness()
