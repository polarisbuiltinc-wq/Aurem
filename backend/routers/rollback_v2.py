"""routers/rollback_v2.py — Pillar 1 admin surface (founder-approved 2026-06).

Snapshot-based two-phase rollback + attempts ledger + synthetic drill.
All endpoints admin-gated (same require_admin_dep as other admin routes).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from cto_services.auth import require_admin_dep
from cto_services.db import get_db

router = APIRouter(prefix="/admin/rollback2", tags=["Admin-Rollback-v2"],
                   dependencies=[Depends(require_admin_dep)])


def _db(request: Request):
    return get_db()


def _env_token() -> str:
    return (os.environ.get("AUREM_DRILL_TOKEN", "").strip()
            or os.environ.get("GITHUB_ACTIONS_TOKEN", "").strip())


class SnapshotReq(BaseModel):
    owner: str
    repo: str
    branch: str = "main"
    file_paths: list[str] = Field(min_length=1)


class PreviewReq(BaseModel):
    snapshot_id: str


class ExecuteReq(BaseModel):
    snapshot_id: str
    preview_token: str
    confirm: bool = False
    initiated_by: str = "admin"


@router.post("/snapshot")
async def create_snapshot_ep(req: SnapshotReq, request: Request):
    from services.rollback_snapshot import create_snapshot
    row = await create_snapshot(
        _db(request), owner=req.owner, repo=req.repo, branch=req.branch,
        token=_env_token(), file_paths=req.file_paths, trigger="manual",
    )
    return {"ok": True, "snapshot": row}


@router.get("/snapshots")
async def list_snapshots(request: Request, limit: int = 20):
    out = []
    async for r in _db(request).rollback_snapshots.find(
            {}, {"_id": 0}).sort("created_at", -1).limit(min(limit, 100)):
        out.append(r)
    return {"snapshots": out}


@router.post("/preview")
async def preview_ep(req: PreviewReq, request: Request):
    from services.rollback_two_phase import preview_rollback
    return await preview_rollback(
        _db(request), snapshot_id=req.snapshot_id, token=_env_token())


@router.post("/execute")
async def execute_ep(req: ExecuteReq, request: Request):
    from services.rollback_two_phase import execute_rollback_from_snapshot
    return await execute_rollback_from_snapshot(
        _db(request), snapshot_id=req.snapshot_id,
        preview_token=req.preview_token,
        initiated_by=req.initiated_by, token=_env_token(),
        confirm=req.confirm,
    )


@router.get("/attempts")
async def list_attempts(request: Request, limit: int = 30):
    out = []
    async for r in _db(request).rollback_attempts.find(
            {}, {"_id": 0}).sort("timestamp", -1).limit(min(limit, 100)):
        out.append(r)
    return {"attempts": out}


@router.post("/drill")
async def run_drill_ep(request: Request):
    from services.rollback_drill import run_drill
    return await run_drill(_db(request), initiated_by="admin")


@router.get("/drills")
async def list_drills(request: Request, limit: int = 10):
    out = []
    async for r in _db(request).rollback_drills.find(
            {}, {"_id": 0}).sort("created_at", -1).limit(min(limit, 50)):
        out.append(r)
    return {"drills": out}
