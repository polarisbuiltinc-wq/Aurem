"""
routers/migrations_admin.py
===========================
Admin-only HTTP surface for the AUREM migration framework.

Founder-facing bookkeeping / observability endpoints — no schema
mutation happens on this router unless the founder explicitly POSTs
to `up`. Mounted under `/api/aurem-dev/admin/migrations/*`.

Gates: same JWT admin gate every other admin route uses. If you can
hit `/api/aurem-dev/admin/health/*` you can hit this.

Endpoints:
    GET  status
    POST mark-applied/{version}
    POST up                     (kept for future — apply pending)
    POST down                   (rollback last / until target)
    POST verify                 (checksum drift report)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from cto_services.auth import require_admin_dep
from cto_services.db import require_db
from migrations import framework as fw

logger = logging.getLogger(__name__)

# Router-boundary admin gate. Any route added later inherits the gate
# automatically — same defense-in-depth pattern as routers/admin.py.
router = APIRouter(
    prefix="/admin/migrations",
    tags=["Admin · Migrations"],
    dependencies=[Depends(require_admin_dep)],
)


def _serialize_status(report: fw.StatusReport) -> dict:
    def _iso(dt):
        return dt.isoformat() if dt else None
    return {
        "applied": [
            {
                "version":        r.version,
                "name":           r.name,
                "applied_at":     _iso(r.applied_at),
                "duration_ms":    r.duration_ms,
                "checksum":       r.checksum,
                "env":            r.env,
                "status":         r.status,
                "rolled_back_at": _iso(r.rolled_back_at),
            }
            for r in report.applied
        ],
        "pending": [
            {
                "version":      m.version,
                "name":         m.name,
                "description":  m.description,
                "dev_only":     m.dev_only,
                "irreversible": m.irreversible,
                "checksum":     m.checksum,
            }
            for m in report.pending
        ],
        "drift": [
            {
                "version": d.version,
                "name":    d.name,
                "recorded_checksum": d.recorded_checksum,
                "current_checksum":  d.current_checksum,
            }
            for d in report.drift
        ],
        "orphans": [
            {"version": o.version, "name": o.name, "checksum": o.checksum}
            for o in report.orphans
        ],
        "is_clean": report.is_clean,
    }


@router.get("/status", summary="List applied / pending / drift / orphan migrations")
async def get_status(_admin: dict = Depends(require_admin_dep)) -> dict:
    db = require_db()
    report = await fw.status(db)
    return _serialize_status(report)


@router.post(
    "/mark-applied/{version}",
    summary=(
        "Record a migration as already-applied WITHOUT running .up(). "
        "Used to import existing pre-framework state on a live DB."
    ),
)
async def post_mark_applied(
    version: str,
    _admin: dict = Depends(require_admin_dep),
) -> dict:
    db = require_db()
    try:
        await fw.mark_applied(db, version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    report = await fw.status(db)
    logger.info(
        "migration %s marked applied by admin %s",
        version, _admin.get("email"),
    )
    return {
        "ok": True,
        "version": version,
        "action": "mark_applied",
        "status": _serialize_status(report),
    }


@router.post("/verify", summary="Return list of migrations whose file drifted since apply")
async def post_verify(_admin: dict = Depends(require_admin_dep)) -> dict:
    db = require_db()
    drift = await fw.verify_checksums(db)
    return {
        "clean": len(drift) == 0,
        "drift": [
            {
                "version": d.version,
                "name":    d.name,
                "recorded_checksum": d.recorded_checksum,
                "current_checksum":  d.current_checksum,
            }
            for d in drift
        ],
    }


@router.post(
    "/up",
    summary=(
        "Apply pending migrations in order. Idempotent — skips ones "
        "already applied. Optional `?target=NNN` caps how far it goes."
    ),
)
async def post_up(
    target: Optional[str] = None,
    dry_run: bool = False,
    _admin: dict = Depends(require_admin_dep),
) -> dict:
    db = require_db()
    results = await fw.apply_pending(db, target=target, dry_run=dry_run)
    report = await fw.status(db)
    logger.info(
        "migration up run by admin %s: %d results, dry_run=%s, target=%s",
        _admin.get("email"), len(results), dry_run, target,
    )
    return {
        "ok": all(r.ok for r in results),
        "dry_run": dry_run,
        "results": [
            {
                "version":     r.version,
                "name":        r.name,
                "duration_ms": r.duration_ms,
                "ok":          r.ok,
                "error":       r.error,
            }
            for r in results
        ],
        "status": _serialize_status(report),
    }


@router.post(
    "/down",
    summary=(
        "Rollback the most recent migration, OR rollback everything above "
        "?target=NNN. Refused for migrations marked irreversible unless "
        "?force=true."
    ),
)
async def post_down(
    target: Optional[str] = None,
    force: bool = False,
    _admin: dict = Depends(require_admin_dep),
) -> dict:
    db = require_db()
    results = await fw.rollback_last(db, target=target, force=force)
    report = await fw.status(db)
    logger.info(
        "migration down run by admin %s: %d results, target=%s, force=%s",
        _admin.get("email"), len(results), target, force,
    )
    return {
        "ok": all(r.ok for r in results) if results else True,
        "results": [
            {
                "version":     r.version,
                "duration_ms": r.duration_ms,
                "ok":          r.ok,
                "error":       r.error,
            }
            for r in results
        ],
        "status": _serialize_status(report),
    }
