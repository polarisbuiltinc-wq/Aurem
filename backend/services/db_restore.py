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

    Fix: restore into a set of PREFIXED COLLECTIONS inside the SAME
    `DB_NAME` database the app is already authorized for — never a
    second database. `scratch_db_name` (if passed) is now used as the
    collection-name prefix, not a Mongo database name.
    """
    started = dt.datetime.now(dt.timezone.utc)
    mongo_url = os.environ["MONGO_URL"]
    source_db = os.environ["DB_NAME"]
    scratch_prefix = (
        scratch_db_name or f"_restore_scratch_{started.strftime('%Y%m%d_%H%M%S')}_"
    )
    if not scratch_prefix.endswith("_"):
        scratch_prefix += "_"
    tmp_path = f"/tmp/restore_{scratch_prefix.strip('_')}.gz"

    result: dict = {
        "ok":                 False,
        "r2_key":             r2_key,
        "scratch_db":         f"{source_db} (prefix={scratch_prefix}*)",
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
        """Drop only OUR prefixed collections — never touches real data."""
        try:
            names = await sdb.list_collection_names()
        except Exception:
            return
        for name in names:
            if name.startswith(scratch_prefix):
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
            # failed run, scoped strictly to our prefix.
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

                # Per-collection loop
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
                    scratch_coll_name = scratch_prefix + coll_name
                    expected = int(meta["doc_count"])

                    if expected == 0:
                        # Explicit create so empty collections still
                        # show up in list_collection_names().
                        try:
                            await sdb.create_collection(scratch_coll_name)
                        except Exception:
                            pass  # already exists (shouldn't after drop)
                        counts[coll_name] = 0
                        continue

                    # Read `expected` BSON docs and bulk insert.
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
                logger.info("dropped scratch collections (prefix=%s)", scratch_prefix)
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
        # / ~6.5GB before this fix). Drop them here too, scoped strictly
        # to `scratch_prefix` — never touches real data.
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
            if not n.startswith("_restore_scratch_")
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
