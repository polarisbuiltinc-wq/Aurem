"""
services/db_restore.py — Restore Verification (item #5 requirement).

Downloads a backup archive from R2 and restores it into a **scratch
throwaway database** so we can prove the backup is genuinely
recoverable. Nothing here ever touches the live database.

Design:
  1. Download the `.archive.gz` from R2 by key.
  2. `mongorestore --archive --gzip --nsFrom=<src>.* --nsTo=<scratch>.*`
     rewrites every collection into the scratch DB name.
  3. Count docs per collection in the scratch DB and return them.
  4. Optionally drop the scratch DB after verification.

Founder's explicit requirement (2026-02-09):
  "a backup nobody has ever restored from isn't verified" — the admin
  `/backups/test-restore` endpoint uses THIS module to prove a real
  round-trip, and returns per-collection doc counts in the response.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import subprocess
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger("db_restore")


def _r2_client():
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


def _mongo_uri_source_db() -> tuple[str, str]:
    """Return (mongo_url, source_db_name). Source db is DB_NAME from
    env — that's the DB whose contents the backup archive contains."""
    return os.environ["MONGO_URL"], os.environ["DB_NAME"]


def _run_mongorestore(
    archive_path: str, mongo_url: str, source_db: str, scratch_db: str,
) -> tuple[int, str, bool]:
    """Restore the archive into `scratch_db`, rewriting namespaces.

    Returns (returncode, stderr_tail, effective_ok).

    `effective_ok` handles mongorestore's noisy exit behavior: it can
    return rc=1 for benign warnings (metadata quirks, index-already-
    exists on --drop retries) even when every document restored
    successfully. We detect that case by checking for
    "0 document(s) failed to restore" in stderr, which mongorestore
    always emits when data is intact.

    Special error contract:
      - Missing binary (FileNotFoundError on subprocess.run) → returns
        rc=127 with a stderr describing exactly which binary is missing.
        Same 2026-02-09 lesson as _run_mongodump_to_gz.
    """
    try:
        proc = subprocess.run(
            [
                "mongorestore",
                f"--uri={mongo_url}",
                "--archive=" + archive_path,
                "--gzip",
                "--nsFrom", f"{source_db}.*",
                "--nsTo",   f"{scratch_db}.*",
                # `--drop` ensures we don't merge into a stale scratch DB
                # from a prior run — every test-restore starts clean.
                "--drop",
            ],
            capture_output=True, text=True, timeout=1800,
        )
    except FileNotFoundError as e:
        path_searched = os.environ.get("PATH", "<PATH unset>")
        msg = (
            f"MISSING BINARY: 'mongorestore' not found on PATH. "
            f"errno={e.errno} strerror={e.strerror!r} filename={e.filename!r}. "
            f"PATH searched: {path_searched}. "
            f"FIX: install mongodb-database-tools in the Docker image."
        )
        return 127, msg, False
    except OSError as e:
        return 127, f"OSError spawning mongorestore: {e!r}", False

    stderr = (proc.stderr or "")[-1500:]
    effective_ok = (
        proc.returncode == 0
        or "0 document(s) failed to restore" in stderr
    )
    return proc.returncode, stderr, effective_ok


async def restore_to_scratch(
    r2_key: str,
    scratch_db_name: Optional[str] = None,
    drop_scratch_after: bool = True,
) -> dict:
    """Download `r2_key` from R2 and restore into a scratch DB.

    Returns a proof dict:
      {
        ok:            bool,
        r2_key:        str,
        scratch_db:    str,
        collection_counts: {name: int, ...},
        total_docs:    int,
        total_collections: int,
        source_size_bytes: int,
        duration_ms:   int,
        error:         Optional[str],
      }

    If `drop_scratch_after=True`, the scratch DB is dropped after
    verification so it doesn't linger. Set False if you want to
    inspect the restored data manually.
    """
    started = dt.datetime.now(dt.timezone.utc)
    mongo_url, source_db = _mongo_uri_source_db()
    scratch_db = scratch_db_name or (
        f"aurem_scratch_restore_{started.strftime('%Y%m%d_%H%M%S')}"
    )
    tmp_path = f"/tmp/restore_{scratch_db}.archive.gz"

    result: dict = {
        "ok":              False,
        "r2_key":          r2_key,
        "scratch_db":      scratch_db,
        "collection_counts": {},
        "total_docs":      0,
        "total_collections": 0,
        "source_size_bytes": 0,
        "duration_ms":     0,
        "error":           None,
    }

    try:
        # 1. Download from R2.
        client = _r2_client()
        bucket = os.environ["R2_BUCKET"]
        try:
            client.download_file(bucket, r2_key, tmp_path)
        except Exception as e:
            result["error"] = f"R2 download failed: {e!r}"
            return result

        result["source_size_bytes"] = os.path.getsize(tmp_path)

        # 2. mongorestore into scratch DB.
        rc, stderr, effective_ok = await asyncio.to_thread(
            _run_mongorestore, tmp_path, mongo_url, source_db, scratch_db,
        )
        if not effective_ok:
            result["error"] = f"mongorestore rc={rc}: {stderr}"
            return result
        if rc != 0:
            # Data restored but mongorestore whined — surface it as a
            # note but don't fail the restore.
            logger.info("mongorestore rc=%d (benign, docs restored): %s", rc, stderr[-200:])

        # 3. Count docs per collection in scratch DB — this is the
        #    proof requirement: not "restore succeeded", but actual
        #    numbers per collection so the founder can verify.
        client_motor = AsyncIOMotorClient(mongo_url)
        try:
            sdb = client_motor[scratch_db]
            names = await sdb.list_collection_names()
            names.sort()
            counts: dict[str, int] = {}
            for name in names:
                try:
                    counts[name] = await sdb[name].count_documents({})
                except Exception as e:
                    counts[name] = -1
                    logger.warning("count %s failed: %r", name, e)
            result["collection_counts"] = counts
            result["total_collections"] = len(counts)
            result["total_docs"] = sum(v for v in counts.values() if v >= 0)

            # 4. Cleanup scratch DB unless caller wants to inspect.
            if drop_scratch_after:
                await client_motor.drop_database(scratch_db)
                logger.info("dropped scratch DB %s", scratch_db)
        finally:
            client_motor.close()

        result["ok"] = True
        return result
    except Exception as e:
        logger.exception("restore_to_scratch failed")
        result["error"] = repr(e)
        return result
    finally:
        # Delete the downloaded archive — no /tmp/ residue.
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        finished = dt.datetime.now(dt.timezone.utc)
        result["duration_ms"] = int((finished - started).total_seconds() * 1000)


async def source_collection_counts() -> dict:
    """Return the LIVE source-DB collection counts. Used by the admin
    endpoint to compare restored counts against source to make the
    proof self-contained ("source had X docs, restore has X docs")."""
    mongo_url, source_db = _mongo_uri_source_db()
    client = AsyncIOMotorClient(mongo_url)
    try:
        sdb = client[source_db]
        names = sorted(await sdb.list_collection_names())
        counts: dict[str, int] = {}
        for name in names:
            try:
                counts[name] = await sdb[name].count_documents({})
            except Exception:
                counts[name] = -1
        return counts
    finally:
        client.close()


__all__ = ["restore_to_scratch", "source_collection_counts"]
