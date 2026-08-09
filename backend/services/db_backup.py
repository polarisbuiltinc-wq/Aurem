"""
services/db_backup.py — Backup Hardening (item #5), Python-native.

Full DB backup via motor/pymongo directly. No `mongodump` subprocess.
No base-image dependency. Uploads a gzipped BSON stream to Cloudflare R2.

Format (aurem-native-v1) — designed for round-trip within AUREM only,
NOT compatible with the official `mongorestore` CLI:

  Gzip wrapper contains:
    Line 1 (JSON):
      {"format":"aurem-native-v1","created_at":"<iso>",
       "source_db":"<name>","total_collections":<n>}
    For each collection (in list_collection_names() order):
      Line: {"collection":"<name>","doc_count":<n>}
      Then <n> BSON documents concatenated (each self-delimiting via
      its 4-byte length prefix — BSON's native framing).

Design rationale:
  - Newline-separated JSON headers keep boundaries greppable.
  - BSON-per-document preserves ALL native types the way pymongo
    stores them: ObjectId, datetime, Decimal128, embedded docs,
    arrays, Binary, UUID, MinKey/MaxKey. No JSON conversion layer =
    no lossy round-trip.
  - Concatenated BSON documents work because each starts with a
    4-byte little-endian length that INCLUDES the length itself —
    parse the prefix, read the rest, done.
  - Empty collections write only their header line, no BSON.

Env config: unchanged from the previous mongodump-based version.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import io
import json
import logging
import os
import time
from typing import Optional

import bson
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger("db_backup")

R2_PREFIX = "mongo/"
FORMAT_VERSION = "aurem-native-v1"

# Streaming knobs. Kept small so peak RSS stays bounded on a
# collection with millions of docs (each cursor batch = one
# .write() worth of bytes before gc).
DUMP_CURSOR_BATCH = 500        # docs per motor cursor batch


def _r2_client():
    """S3-compatible boto3 client for Cloudflare R2. Path-style
    addressing is required with the account-scoped endpoint
    (`<account>.r2.cloudflarestorage.com`) — the default virtual-
    hosted style produces SignatureDoesNotMatch."""
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _capture_sentry(exc: BaseException, extra: Optional[dict] = None) -> None:
    """Best-effort Sentry breadcrumb. Never raises."""
    try:
        import sentry_sdk  # type: ignore
        if extra:
            with sentry_sdk.new_scope() as scope:
                for k, v in extra.items():
                    scope.set_extra(k, v)
                sentry_sdk.capture_exception(exc)
        else:
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass


async def _write_history(
    db, *, r2_key: str, status: str, size_bytes: int, duration_ms: int,
    error: Optional[str] = None, doc_count: Optional[int] = None,
) -> None:
    doc = {
        "r2_key":       r2_key,
        "status":       status,
        "size_bytes":   size_bytes,
        "duration_ms":  duration_ms,
        "error":        error,
        "doc_count":    doc_count,
        "format":       FORMAT_VERSION,
        "created_at":   dt.datetime.now(dt.timezone.utc).isoformat(),
        "env":          os.environ.get("SENTRY_ENV") or "unknown",
        "bucket":       os.environ.get("R2_BUCKET"),
    }
    try:
        await db.backup_history.insert_one(doc)
    except Exception as e:
        logger.error("backup_history insert failed: %r", e)


async def _dump_db_to_gzip_file(mongo_url: str, source_db: str, out_path: str) -> dict:
    """Iterate every collection in `source_db` and write a
    gzipped aurem-native-v1 stream to `out_path`.

    Returns {"total_docs": int, "total_collections": int, "per_collection": {name: count}}.
    Uses a fresh Motor client so this can be called from anywhere
    without interfering with the app-level connection pool.
    """
    client = AsyncIOMotorClient(mongo_url)
    per_collection: dict[str, int] = {}
    total_docs = 0
    try:
        sdb = client[source_db]
        names = sorted(await sdb.list_collection_names())
        # Header line first (BEFORE any per-collection data) so a
        # reader can validate the format without seeking.
        header = {
            "format": FORMAT_VERSION,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_db": source_db,
            "total_collections": len(names),
        }
        # gzip.open in text-then-binary mixed writes is awkward, so
        # we open binary and encode ourselves — keeps offsets exact.
        with gzip.open(out_path, "wb", compresslevel=6) as gz:
            gz.write((json.dumps(header) + "\n").encode("utf-8"))
            for name in names:
                coll = sdb[name]
                # Count first for the header line — cheap on Mongo
                # because it uses metadata not a full scan for small
                # collections; for very large collections we accept
                # this cost as the price of a self-describing archive.
                doc_count = await coll.count_documents({})
                per_collection[name] = doc_count
                total_docs += doc_count
                gz.write((json.dumps({
                    "collection": name, "doc_count": doc_count,
                }) + "\n").encode("utf-8"))
                if doc_count == 0:
                    # Empty collection — header only, no BSON body.
                    continue
                # Stream all docs. motor's cursor batches internally.
                cursor = coll.find({}, batch_size=DUMP_CURSOR_BATCH)
                async for doc in cursor:
                    gz.write(bson.encode(doc))
    finally:
        client.close()
    return {
        "total_docs": total_docs,
        "total_collections": len(per_collection),
        "per_collection": per_collection,
    }


async def run_backup(db) -> dict:
    """Perform a single backup end-to-end.

    Returns:
      {ok, r2_key, size_bytes, duration_ms, pruned, total_docs,
       total_collections, error}
    """
    started = time.monotonic()
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        err = "MONGO_URL or DB_NAME missing"
        await _write_history(db, r2_key="", status="failed", size_bytes=0,
                             duration_ms=0, error=err)
        return {"ok": False, "error": err}

    for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_ENDPOINT"):
        if not os.environ.get(k):
            err = f"{k} missing — R2 not configured"
            logger.error(err)
            await _write_history(db, r2_key="", status="failed", size_bytes=0,
                                 duration_ms=0, error=err)
            return {"ok": False, "error": err}

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    r2_key = f"{R2_PREFIX}aurem_{stamp}.aurem-native-v1.gz"
    tmp_path = f"/tmp/{r2_key.replace('/', '_')}"

    try:
        # 1. Dump every collection to a local gz file.
        dump_stats = await _dump_db_to_gzip_file(mongo_url, db_name, tmp_path)

        size_bytes = os.path.getsize(tmp_path)

        # 2. Upload to R2.
        client = _r2_client()
        bucket = os.environ["R2_BUCKET"]
        with open(tmp_path, "rb") as fh:
            client.put_object(
                Bucket=bucket, Key=r2_key, Body=fh,
                ContentType="application/gzip",
                Metadata={
                    "aurem-format": FORMAT_VERSION,
                    "aurem-source-db": db_name,
                    "aurem-doc-count": str(dump_stats["total_docs"]),
                    "aurem-coll-count": str(dump_stats["total_collections"]),
                },
            )

        # 3. Prune old objects per retention policy.
        retention = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))
        pruned = await asyncio.to_thread(_prune_old, client, bucket, retention)

        duration_ms = int((time.monotonic() - started) * 1000)
        await _write_history(
            db, r2_key=r2_key, status="success", size_bytes=size_bytes,
            duration_ms=duration_ms, doc_count=dump_stats["total_docs"],
        )
        logger.info(
            "backup OK → r2://%s/%s (%.2f MB, %d docs across %d colls, %dms, pruned=%d)",
            bucket, r2_key, size_bytes / 1024 / 1024,
            dump_stats["total_docs"], dump_stats["total_collections"],
            duration_ms, pruned,
        )
        return {
            "ok":                 True,
            "r2_key":             r2_key,
            "size_bytes":         size_bytes,
            "duration_ms":        duration_ms,
            "pruned":             pruned,
            "total_docs":         dump_stats["total_docs"],
            "total_collections":  dump_stats["total_collections"],
        }
    except Exception as e:
        duration_ms = int((time.monotonic() - started) * 1000)
        import traceback
        tb = traceback.format_exc()
        error_str = f"{type(e).__name__}: {e!r}\n{tb}"[:1800]
        logger.exception("backup failed")
        _capture_sentry(e, {"stage": "run_backup", "r2_key": r2_key})
        await _write_history(
            db, r2_key=r2_key, status="failed", size_bytes=0,
            duration_ms=duration_ms, error=error_str,
        )
        return {"ok": False, "error": error_str, "r2_key": r2_key}
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _prune_old(client, bucket: str, retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=retention_days)
    deleted = 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        to_delete: list[dict] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=R2_PREFIX):
            for obj in page.get("Contents") or []:
                if obj["LastModified"] < cutoff:
                    to_delete.append({"Key": obj["Key"]})
        for i in range(0, len(to_delete), 1000):
            batch = to_delete[i:i + 1000]
            client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
            deleted += len(batch)
    except Exception as e:
        logger.warning("prune skipped: %r", e)
        _capture_sentry(e, {"stage": "prune"})
    return deleted


async def backup_cron(db_getter=None) -> None:
    target_hour = int(os.environ.get("BACKUP_SCHEDULE_UTC_HOUR", "3"))
    while True:
        try:
            now = dt.datetime.now(dt.timezone.utc)
            target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
            if target <= now:
                target += dt.timedelta(days=1)
            sleep_s = max(60.0, (target - now).total_seconds())
            logger.info("next backup in %.0fs (target %s UTC)", sleep_s, target)
            await asyncio.sleep(sleep_s)
            db = db_getter() if db_getter else None
            if db is None:
                logger.warning("backup_cron: no db available — skipping this cycle")
                continue
            await run_backup(db)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("backup_cron loop error: %r — sleeping 1h", e)
            _capture_sentry(e, {"stage": "backup_cron_loop"})
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise


__all__ = ["run_backup", "backup_cron", "R2_PREFIX", "FORMAT_VERSION"]
