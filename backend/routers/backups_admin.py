"""
routers/backups_admin.py
========================
Admin-only HTTP surface for the Backup Hardening pipeline (item #5).

All endpoints mounted at `/api/aurem-dev/admin/backups/*` behind the
same admin JWT gate as `routers/admin.py` and `routers/migrations_admin.py`.

Endpoints:
    GET  status              — recent 20 history rows (success + failure)
    GET  list                — R2 object listing (prefix mongo/)
    POST run                 — trigger a backup NOW, return proof dict
    POST test-restore        — download a backup, restore to scratch DB,
                                return per-collection doc counts

Founder's explicit ruling (2026-02-09):
  "A backup nobody has ever restored from isn't verified."
  ↳ /test-restore is the operational proof endpoint. The founder must
    be able to hit it any day and get a fresh restore report.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from cto_services.auth import require_admin_dep
from cto_services.db import require_db
from services import db_backup, db_restore

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/backups",
    tags=["Admin · Backups"],
    dependencies=[Depends(require_admin_dep)],
)


@router.get("/status", summary="Recent backup history (successes and failures)")
async def get_status(
    limit: int = Query(20, ge=1, le=200),
    _admin: dict = Depends(require_admin_dep),
) -> dict:
    """Return the last `limit` backup_history rows, newest first.

    Also returns rolled-up counters:
      last_success_at, last_failure_at, consecutive_failures
    — the last one is the signal the founder wants for alerting.
    """
    db = require_db()
    cursor = db.backup_history.find({}, sort=[("created_at", -1)]).limit(limit)
    rows = []
    async for doc in cursor:
        doc.pop("_id", None)
        rows.append(doc)

    # Roll-ups.
    last_success = next((r["created_at"] for r in rows if r.get("status") == "success"), None)
    last_failure = next((r["created_at"] for r in rows if r.get("status") == "failed"), None)
    consecutive_failures = 0
    for r in rows:
        if r.get("status") == "failed":
            consecutive_failures += 1
        else:
            break

    return {
        "ok": True,
        "history": rows,
        "last_success_at":      last_success,
        "last_failure_at":      last_failure,
        "consecutive_failures": consecutive_failures,
        "alert":                consecutive_failures >= 2,
    }


@router.get("/list", summary="List backup objects currently in R2")
async def get_list(
    _admin: dict = Depends(require_admin_dep),
) -> dict:
    """List every object under the `mongo/` prefix in the R2 bucket
    with size + last-modified so the founder can eyeball what's
    actually stored offsite right now."""
    import os
    from services.db_backup import _r2_client, R2_PREFIX

    client = _r2_client()
    bucket = os.environ.get("R2_BUCKET")
    if not bucket:
        raise HTTPException(500, "R2_BUCKET not configured")

    objects = []
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=R2_PREFIX):
            for obj in page.get("Contents") or []:
                objects.append({
                    "key":            obj["Key"],
                    "size_bytes":     obj["Size"],
                    "last_modified":  obj["LastModified"].isoformat(),
                    "etag":           obj.get("ETag", "").strip('"'),
                })
    except Exception as e:
        logger.exception("R2 list failed")
        raise HTTPException(502, f"R2 list failed: {e!r}")

    objects.sort(key=lambda o: o["last_modified"], reverse=True)
    total_bytes = sum(o["size_bytes"] for o in objects)
    return {
        "ok":            True,
        "bucket":        bucket,
        "prefix":        R2_PREFIX,
        "count":         len(objects),
        "total_bytes":   total_bytes,
        "total_mb":      round(total_bytes / 1024 / 1024, 2),
        "objects":       objects,
    }


@router.post("/run", summary="Trigger a backup NOW (blocks until done)")
async def post_run(
    _admin: dict = Depends(require_admin_dep),
) -> dict:
    """Run a single backup synchronously. Founder-triggerable so we
    never have to wait for the 03:00 UTC cron to prove things work."""
    db = require_db()
    result = await db_backup.run_backup(db)
    if not result.get("ok"):
        # Return 200 with ok=false so the caller sees the error dict
        # (a 500 would obscure the shape). Alert flag surfaces this.
        return {"ok": False, **result}
    logger.info(
        "manual backup by admin=%s → %s (%d bytes, %dms)",
        _admin.get("email"), result.get("r2_key"),
        result.get("size_bytes", 0), result.get("duration_ms", 0),
    )
    return result


@router.post(
    "/test-restore",
    summary=(
        "Download a backup from R2 and restore into a scratch DB. "
        "Returns per-collection doc counts as proof the archive is "
        "recoverable. Scratch DB is dropped after unless keep=true."
    ),
)
async def post_test_restore(
    key: Optional[str] = Query(
        None,
        description=(
            "R2 object key to restore. If omitted, uses the most-recent "
            "successful backup from history."
        ),
    ),
    keep: bool = Query(
        False,
        description="Leave the scratch DB in Mongo after restore for inspection.",
    ),
    _admin: dict = Depends(require_admin_dep),
) -> dict:
    """Proof-of-restore endpoint. Returns per-collection doc counts
    from BOTH the source DB and the restored scratch DB so the founder
    can visually confirm they match."""
    db = require_db()

    # If no key supplied, pick the most-recent successful backup.
    if not key:
        latest = await db.backup_history.find_one(
            {"status": "success"}, sort=[("created_at", -1)],
        )
        if not latest:
            raise HTTPException(
                404, "No successful backup found in history — run one first.",
            )
        key = latest["r2_key"]

    # Live source counts (before restore, for comparison).
    source_counts = await db_restore.source_collection_counts()
    source_total_docs = sum(v for v in source_counts.values() if v >= 0)

    # Do the restore.
    restore_result = await db_restore.restore_to_scratch(
        r2_key=key, drop_scratch_after=not keep,
    )

    # Diff: which collections/counts don't match?
    restored_counts = restore_result.get("collection_counts", {}) or {}
    mismatches = []
    for name, src_count in source_counts.items():
        rst_count = restored_counts.get(name, 0)
        if src_count != rst_count:
            mismatches.append({
                "collection": name,
                "source":     src_count,
                "restored":   rst_count,
                "delta":      rst_count - src_count,
            })
    # New collections that appeared only in restore (shouldn't happen
    # unless someone deleted a collection between backup and now).
    for name in restored_counts:
        if name not in source_counts:
            mismatches.append({
                "collection": name,
                "source":     0,
                "restored":   restored_counts[name],
                "delta":      restored_counts[name],
                "note":       "only in restore",
            })

    logger.info(
        "test-restore by admin=%s: key=%s ok=%s source=%d docs, restored=%d docs, mismatches=%d",
        _admin.get("email"), key, restore_result.get("ok"),
        source_total_docs, restore_result.get("total_docs", 0),
        len(mismatches),
    )

    return {
        "ok":                 restore_result.get("ok", False),
        "r2_key":             key,
        "scratch_db":         restore_result.get("scratch_db"),
        "source_total_docs":  source_total_docs,
        "restored_total_docs": restore_result.get("total_docs", 0),
        "source_collections": len(source_counts),
        "restored_collections": restore_result.get("total_collections", 0),
        "source_counts":      source_counts,
        "restored_counts":    restored_counts,
        "mismatches":         mismatches,
        "counts_match":       len(mismatches) == 0,
        "source_size_bytes":  restore_result.get("source_size_bytes", 0),
        "duration_ms":        restore_result.get("duration_ms", 0),
        "error":              restore_result.get("error"),
        "kept":               keep,
    }
