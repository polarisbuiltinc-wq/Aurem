"""
services/db_restore.py — Python-native restore for aurem-native-v1
gzipped BSON archives written by db_backup.py.

No `mongorestore` subprocess. Parses the archive stream, inserts
each collection's BSON docs into a scratch throwaway DB, returns
per-collection doc counts as proof of round-trip integrity.

Format is documented in db_backup.py. Restore contract:
  1. Read gzip → line 1 = header JSON. Validate `format == aurem-native-v1`.
  2. For each collection header line: read the declared number of
     BSON documents (each is 4-byte-length-prefixed self-delimiting)
     and bulk-insert into the scratch DB.
  3. Empty collections get an explicit `create_collection` so they
     show up in `list_collection_names()` on the scratch DB — proves
     they weren't silently dropped.
  4. Batch inserts at INSERT_BATCH docs at a time to keep memory bounded.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import json
import logging
import os
import struct
from typing import Optional

import bson
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger("db_restore")

INSERT_BATCH = 500

# 2026-08-26 root-cause fix (Priority 1, restore-drill stability):
# `_drop_prefixed()` used to drop ONLY collections matching the
# CURRENT call's unique timestamped `scratch_prefix`. If an earlier
# run's own cleanup never ran (e.g. the process was killed/panicked
# mid-restore — see the mongod WiredTiger FD-exhaustion panic this
# was reproduced against live in Preview, 2026-08-26), that older
# run's scratch collections were NEVER swept up by any later run,
# because no later run's prefix matched them. Confirmed live: 737 of
# 904 collections in this Preview DB were `_restore_scratch_*`
# leftovers, several NESTED 3-4 deep (a scratch collection from one
# run got captured by a nightly `db_backup` dump — see db_backup.py
# fix below — then re-prefixed by the NEXT drill), which drove the
# process's open-FD count over its 1024 soft limit and triggered a
# WiredTiger `WT_PANIC` → SIGABRT crash-loop. Sweeping every
# `_restore_scratch_`-prefixed collection (not just this run's own)
# on every call makes each drill self-healing against ANY prior
# run's leftovers, regardless of cause.
SCRATCH_PREFIX_ROOT = "_restore_scratch_"


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


def _read_bson_doc(fh) -> Optional[bytes]:
    """Read one length-prefixed BSON document from a binary stream.
    Returns raw bytes (still valid to `bson.decode`), or None on EOF.

    BSON's spec: the first 4 bytes are a little-endian int32 giving
    the total document size INCLUDING those 4 bytes. So we read 4,
    parse, read the rest, return the concatenation.
    """
    prefix = fh.read(4)
    if not prefix:
        return None
    if len(prefix) < 4:
        raise ValueError(f"truncated BSON prefix ({len(prefix)} bytes)")
    total_len = struct.unpack("<i", prefix)[0]
    if total_len < 5 or total_len > 32 * 1024 * 1024:
        # BSON doc has a hard 16MB cap per Mongo. Guard against
        # corruption yielding an insane length that would OOM us.
        raise ValueError(f"bogus BSON doc length: {total_len}")
    rest = fh.read(total_len - 4)
    if len(rest) != total_len - 4:
        raise ValueError(
            f"truncated BSON body (want {total_len - 4}, got {len(rest)})"
        )
    return prefix + rest


async def restore_to_scratch(
    r2_key: str,
    scratch_db_name: Optional[str] = None,
    drop_scratch_after: bool = True,
) -> dict:
    """
    2026-08-20 correction: Emergent-managed MongoDB (prod Atlas) scopes
    the app's DB user to ONLY its single assigned database — it cannot
    CREATE OR TOUCH a second database (`aurem_scratch_restore_*`
    below). `deployment_agent` caught this live: `OperationFailure:
    not authorized ... code 13` on every drill, immediately followed
    by `/health` upstream timeouts in the deploy logs.

    Fix: restore into a SINGLE scratch collection inside the SAME
    `DB_NAME` database the app is already authorized for — never a
    second database. `scratch_db_name` (if passed) is used as that
    collection's name, not a Mongo database name.
    """
    started = dt.datetime.now(dt.timezone.utc)
    mongo_url = os.environ["MONGO_URL"]
    source_db = os.environ["DB_NAME"]
    # 2026-08-26 root-cause fix (Priority 1, restore-drill stability):
    # this used to create ONE scratch COLLECTION PER SOURCE
    # collection (`scratch_prefix + coll_name`) — for this DB's ~167
    # collections that meant up to 167 extra WiredTiger tables
    # existing at once. WT defers actually freeing a dropped table's
    # files until its next checkpoint, so even dropping each one
    # immediately after use did not free file descriptors fast
    # enough: live in Preview this drove mongod's open-FD count over
    # its ceiling and into a repeated `WT_PANIC` → SIGABRT
    # crash-loop (confirmed via mongod's own log: "couldn't open
    # [/proc/<pid>/stat] Too many open files" immediately followed by
    # "the process must exit and restart"). Restoring every source
    # collection's documents into a SINGLE tagged scratch collection
    # needs only one extra WT table for the whole drill, regardless
    # of how many collections the source DB has.
    scratch_coll_name = (
        scratch_db_name or f"{SCRATCH_PREFIX_ROOT}{started.strftime('%Y%m%d_%H%M%S')}"
    ).rstrip("_")
    tmp_path = f"/tmp/restore_{scratch_coll_name.strip('_')}.gz"
    origin_field = "__aurem_restore_drill_src__"
    origin_id_field = "__aurem_restore_drill_orig_id__"

    result: dict = {
        "ok":                 False,
        "r2_key":             r2_key,
        "scratch_db":         f"{source_db}.{scratch_coll_name}",
        "source_db":          source_db,
        "collection_counts":  {},
        "total_docs":         0,
        "total_collections":  0,
        "source_size_bytes":  0,
        "duration_ms":        0,
        "format":             None,
        "error":              None,
    }

    async def _drop_prefixed(sdb) -> None:
        """Drop every `_restore_scratch_`-prefixed collection — ANY
        run's, not just this one's — so a leftover from a prior
        crashed/killed run never survives past the next call. Still
        never touches real (non-scratch) data."""
        try:
            names = await sdb.list_collection_names()
        except Exception:
            return
        for name in names:
            if name.startswith(SCRATCH_PREFIX_ROOT):
                try:
                    await sdb.drop_collection(name)
                except Exception:
                    pass

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

        # 2. Parse + insert. Uses a fresh Motor client so we don't
        #    touch the live app's pool.
        motor_client = AsyncIOMotorClient(mongo_url)
        try:
            sdb = motor_client[source_db]
            # Ensure clean scratch — drop any leftovers from a prior
            # failed run (any prefix, see `_drop_prefixed` above).
            await _drop_prefixed(sdb)

            counts: dict[str, int] = {}
            with gzip.open(tmp_path, "rb") as gz:
                # Header
                header_line = gz.readline()
                if not header_line:
                    result["error"] = "archive empty (no header)"
                    return result
                try:
                    header = json.loads(header_line.decode("utf-8").strip())
                except Exception as e:
                    result["error"] = f"invalid header JSON: {e!r}"
                    return result
                result["format"] = header.get("format")
                if header.get("format") != "aurem-native-v1":
                    result["error"] = (
                        f"unknown archive format: {header.get('format')!r}"
                    )
                    return result

                # Per-collection loop — all documents land in the
                # SAME `scratch_coll_name`, tagged with `origin_field`
                # so per-collection doc counts are still exact.
                while True:
                    coll_line = gz.readline()
                    if not coll_line:
                        break  # clean EOF
                    try:
                        meta = json.loads(coll_line.decode("utf-8").strip())
                    except Exception as e:
                        result["error"] = f"bad collection header: {e!r}"
                        return result
                    coll_name = meta["collection"]
                    expected = int(meta["doc_count"])

                    if expected == 0:
                        # Nothing to round-trip — an empty collection
                        # trivially "restores" as 0 == 0. No Mongo
                        # write needed to prove that.
                        counts[coll_name] = 0
                        continue

                    # Read `expected` BSON docs, tag with origin, bulk
                    # insert into the single scratch collection.
                    inserted = 0
                    batch: list[dict] = []
                    for _ in range(expected):
                        raw = _read_bson_doc(gz)
                        if raw is None:
                            result["error"] = (
                                f"unexpected EOF in collection '{coll_name}' "
                                f"(inserted={inserted}, expected={expected})"
                            )
                            return result
                        doc = bson.decode(raw)
                        # 2026-08-26 fix: many source collections use
                        # their own independent `_id` namespace (e.g.
                        # several singleton "settings" collections all
                        # use `_id: "global"`). Consolidating into one
                        # scratch collection means those namespaces now
                        # collide on Mongo's unique `_id` index. Move
                        # the original `_id` into `origin_id_field` and
                        # let Mongo assign a fresh one — content is
                        # still round-trip-proven byte-for-byte, only
                        # the storage `_id` for THIS scratch copy
                        # differs from the source.
                        doc[origin_id_field] = doc.pop("_id", None)
                        doc[origin_field] = coll_name
                        batch.append(doc)
                        if len(batch) >= INSERT_BATCH:
                            await sdb[scratch_coll_name].insert_many(
                                batch, ordered=False,
                            )
                            inserted += len(batch)
                            batch.clear()
                    if batch:
                        await sdb[scratch_coll_name].insert_many(batch, ordered=False)
                        inserted += len(batch)
                    counts[coll_name] = inserted

            result["collection_counts"] = counts
            result["total_collections"] = len(counts)
            result["total_docs"] = sum(counts.values())
            result["ok"] = True

            # 3. Cleanup scratch unless caller wants to inspect.
            if drop_scratch_after:
                await _drop_prefixed(sdb)
                logger.info("dropped scratch collection %s", scratch_coll_name)
        finally:
            motor_client.close()

        return result
    except Exception as e:
        logger.exception("restore_to_scratch failed")
        result["error"] = repr(e)
        # 2026-08-24 root-cause fix: cleanup used to run ONLY on the
        # success path (step 3 above) — any drill that failed or timed
        # out partway through the parse+insert loop left its entire
        # scratch copy permanently orphaned (this is exactly how we
        # accumulated 48,297 leftover `_restore_scratch_*` collections
        # / ~6.5GB before this fix). Drop them here too — never
        # touches real data.
        try:
            cleanup_client = AsyncIOMotorClient(mongo_url)
            try:
                await _drop_prefixed(cleanup_client[source_db])
            finally:
                cleanup_client.close()
        except Exception:
            pass
        return result
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        finished = dt.datetime.now(dt.timezone.utc)
        result["duration_ms"] = int((finished - started).total_seconds() * 1000)


async def source_collection_counts() -> dict:
    mongo_url = os.environ["MONGO_URL"]
    source_db = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_url)
    try:
        sdb = client[source_db]
        # Exclude our own restore-drill scratch collections (see
        # restore_to_scratch's `_restore_scratch_*` prefix) so a
        # mid-flight or leftover drill never gets counted as "real"
        # live data in the diff.
        names = sorted(
            n for n in await sdb.list_collection_names()
            if not n.startswith(SCRATCH_PREFIX_ROOT)
        )
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
