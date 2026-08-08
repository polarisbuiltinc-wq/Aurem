"""
services/db_backup.py — Backup Hardening (item #5, D+ → A).

Nightly full MongoDB backup, streamed directly to Cloudflare R2 (S3-compatible).

Design:
  1. `mongodump --uri=$MONGO_URL --archive` streams a BSON archive to stdout.
  2. gzip'd on the fly (subprocess pipeline).
  3. boto3 uploads the resulting archive to R2 with key
       `mongo/aurem_<YYYYMMDD_HHMMSS>.tar.gz`.
  4. After success, prune R2 objects older than BACKUP_RETENTION_DAYS.
  5. Every attempt writes a `backup_history` doc so we have a durable audit
     trail even if R2 or the pod dies mid-run.

Why R2 (not /tmp/):
  Prior implementation wrote to `/tmp/backups/` which is pod-ephemeral —
  a redeploy or OOM wipes every "backup" we ever took. R2 is durable,
  offsite, and versioned.

Env config (all required except retention/hour which have sensible defaults):
  R2_ACCOUNT_ID
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_BUCKET
  R2_ENDPOINT                     https://<account>.r2.cloudflarestorage.com
  BACKUP_RETENTION_DAYS           default 30
  BACKUP_SCHEDULE_UTC_HOUR        default 3

Failure semantics:
  Never crashes the app. On failure: logs ERROR, captures to Sentry if
  wired, and writes a `status=failed` doc to `backup_history`. The
  admin `/backups/status` endpoint surfaces recent failures so the
  founder sees them without digging through supervisor logs.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import subprocess
import time
from typing import Optional

logger = logging.getLogger("db_backup")

# ── R2 object-key convention ─────────────────────────────────────────
# All Mongo backups live under a `mongo/` prefix so the same bucket can
# safely be used for other artifact types later without collision.
R2_PREFIX = "mongo/"


def _r2_client():
    """Return a boto3 S3 client wired for Cloudflare R2.

    R2 requires path-style addressing when using the account-scoped
    endpoint (`<account>.r2.cloudflarestorage.com`) — the default
    virtual-hosted-style produces SignatureDoesNotMatch. Baked here so
    no caller can accidentally break it.
    """
    import boto3
    from botocore.config import Config

    endpoint = os.environ["R2_ENDPOINT"]
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
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
            with sentry_sdk.push_scope() as scope:
                for k, v in extra.items():
                    scope.set_extra(k, v)
                sentry_sdk.capture_exception(exc)
        else:
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass


async def _write_history(
    db, *, r2_key: str, status: str, size_bytes: int, duration_ms: int,
    error: Optional[str] = None,
) -> None:
    """Append a backup_history doc. Idempotency not required —
    every run gets its own row so status='failed' entries persist for
    audit. This is the only source of durable backup audit history."""
    doc = {
        "r2_key":       r2_key,
        "status":       status,
        "size_bytes":   size_bytes,
        "duration_ms":  duration_ms,
        "error":        error,
        "created_at":   dt.datetime.now(dt.timezone.utc).isoformat(),
        "env":          os.environ.get("SENTRY_ENV") or "unknown",
        "bucket":       os.environ.get("R2_BUCKET"),
    }
    try:
        await db.backup_history.insert_one(doc)
    except Exception as e:
        # Even the history-write failed — log loudly but don't crash.
        logger.error("backup_history insert failed: %r", e)


def _run_mongodump_to_gz(out_path: str, mongo_url: str) -> tuple[int, str]:
    """Spawn `mongodump | gzip` and stream to `out_path`.

    Returns (returncode, stderr_tail). Times out at 30 min — a
    healthy AUREM DB dumps in seconds; 30 min ceiling is generous
    protection against a hung Mongo endpoint.
    """
    # Two-process pipeline: dump → gzip. Using `--archive` streams
    # a single BSON archive on stdout (not a directory tree).
    dump = subprocess.Popen(
        ["mongodump", f"--uri={mongo_url}", "--archive", "--gzip"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    with open(out_path, "wb") as fh:
        # Read in 1 MB chunks so we don't pin memory on large dumps.
        assert dump.stdout is not None
        while True:
            chunk = dump.stdout.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
    try:
        dump.wait(timeout=1800)  # 30 min
    except subprocess.TimeoutExpired:
        dump.kill()
        return 124, "mongodump timed out after 30m"
    stderr = ""
    if dump.stderr is not None:
        try:
            stderr = dump.stderr.read().decode("utf-8", errors="replace")[-800:]
        except Exception:
            pass
    return dump.returncode, stderr


def _prune_old(client, bucket: str, retention_days: int) -> int:
    """Delete R2 objects under R2_PREFIX older than retention_days.
    Returns count of deleted objects. Never raises — logs and moves on."""
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
        # boto3 delete_objects caps at 1000 keys per call.
        for i in range(0, len(to_delete), 1000):
            batch = to_delete[i:i + 1000]
            client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
            deleted += len(batch)
    except Exception as e:
        logger.warning("prune skipped: %r", e)
        _capture_sentry(e, {"stage": "prune"})
    return deleted


async def run_backup(db) -> dict:
    """Perform a single backup end-to-end. Returns a result dict:
       {ok, r2_key, size_bytes, duration_ms, pruned, error}

    `db` is the AsyncIOMotorDatabase used to write history. Called
    both from the daily cron AND from the admin `/backups/run`
    endpoint, so it must be safe to invoke on demand.
    """
    started = time.monotonic()
    mongo_url = os.environ.get("MONGO_URL")
    if not mongo_url:
        return {"ok": False, "error": "MONGO_URL missing"}

    for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_ENDPOINT"):
        if not os.environ.get(k):
            err = f"{k} missing — R2 not configured"
            logger.error(err)
            await _write_history(
                db, r2_key="", status="failed", size_bytes=0,
                duration_ms=0, error=err,
            )
            return {"ok": False, "error": err}

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    r2_key = f"{R2_PREFIX}aurem_{stamp}.archive.gz"
    tmp_path = f"/tmp/{r2_key.replace('/', '_')}"

    try:
        # 1. Dump + gzip locally to a temp file first (streaming an
        #    unknown-size gzip directly to R2 is more brittle than
        #    doing it in two steps — the temp file is deleted in
        #    finally: below, so no persistent /tmp/ residue).
        rc, stderr = await asyncio.to_thread(
            _run_mongodump_to_gz, tmp_path, mongo_url,
        )
        if rc != 0:
            err = f"mongodump rc={rc}: {stderr[:400]}"
            logger.error(err)
            duration_ms = int((time.monotonic() - started) * 1000)
            await _write_history(
                db, r2_key=r2_key, status="failed", size_bytes=0,
                duration_ms=duration_ms, error=err,
            )
            return {"ok": False, "error": err, "r2_key": r2_key}

        size_bytes = os.path.getsize(tmp_path)

        # 2. Upload to R2.
        client = _r2_client()
        bucket = os.environ["R2_BUCKET"]
        with open(tmp_path, "rb") as fh:
            client.put_object(
                Bucket=bucket, Key=r2_key, Body=fh,
                ContentType="application/gzip",
                # Server-side encryption is provided by Cloudflare R2
                # by default — no header needed. Add it if we ever
                # switch to a provider that doesn't encrypt at rest.
            )

        # 3. Prune old objects per retention policy.
        retention = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))
        pruned = await asyncio.to_thread(_prune_old, client, bucket, retention)

        duration_ms = int((time.monotonic() - started) * 1000)
        await _write_history(
            db, r2_key=r2_key, status="success",
            size_bytes=size_bytes, duration_ms=duration_ms,
        )
        logger.info(
            "backup OK → r2://%s/%s (%.2f MB, %dms, pruned=%d)",
            bucket, r2_key, size_bytes / 1024 / 1024, duration_ms, pruned,
        )
        return {
            "ok":           True,
            "r2_key":       r2_key,
            "size_bytes":   size_bytes,
            "duration_ms":  duration_ms,
            "pruned":       pruned,
        }
    except Exception as e:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.exception("backup failed")
        _capture_sentry(e, {"stage": "run_backup", "r2_key": r2_key})
        await _write_history(
            db, r2_key=r2_key, status="failed", size_bytes=0,
            duration_ms=duration_ms, error=repr(e)[:400],
        )
        return {"ok": False, "error": repr(e), "r2_key": r2_key}
    finally:
        # Never leave the temp file lying around — /tmp/ is where
        # the old bug lived; we do NOT want any /tmp/ backup residue.
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


async def backup_cron(db_getter=None) -> None:
    """Runs forever, kicks off `run_backup` once/day at
    BACKUP_SCHEDULE_UTC_HOUR (default 03:00 UTC).

    Launched from FastAPI lifespan via `_supervise(...)` so it dies
    with the process. Never raises — outer loop catches everything
    so one failure doesn't kill the cron for the rest of the day.
    """
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

            # `db_getter` lets the cron re-fetch a live db handle each
            # cycle so if the app-level db reconnects, we still work.
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


__all__ = ["run_backup", "backup_cron", "R2_PREFIX"]
