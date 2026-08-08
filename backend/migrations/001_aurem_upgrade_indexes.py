"""
migrations/001_aurem_upgrade_indexes.py
========================================
Creates the indexes needed for AUREM's core collections.

Idempotent — safe to re-run. Rollback drops the indexes.

New framework:
    python -m backend.migrations up

Legacy invocation (still works):
    python -m migrations.001_aurem_upgrade_indexes
"""
from __future__ import annotations

import asyncio
import os

from motor.motor_asyncio import AsyncIOMotorClient

from .base import Migration


# All indexes owned by this migration. Format: (collection, keys, kwargs).
# `keys` is either a str field name or a list of (field, direction) tuples.
_INDEXES: list[tuple[str, object, dict]] = [
    # project_brains
    ("project_brains", "project_id",              {"unique": True}),
    ("project_brains", "repo_full_name",          {}),
    ("project_brains", "updated_at",              {}),
    # ora_council_logs
    ("ora_council_logs", [("timestamp", -1)],     {}),
    ("ora_council_logs", "mode",                  {}),
    ("ora_council_logs", "exported_for_training", {}),
    ("ora_council_logs", "project_id",            {}),
    ("ora_council_logs", "user_id",               {}),
    # issues_cache — includes a TTL index that auto-expires cached rows
    ("issues_cache", "repo",                      {"unique": True}),
    ("issues_cache", [("fetched_at", -1)],        {}),
    ("issues_cache", "fetched_at",
     {"expireAfterSeconds": 3600, "name": "issues_cache_ttl"}),
]


class UpgradeIndexesMigration(Migration):
    version = "001"
    name = "aurem_upgrade_indexes"
    description = "Create indexes for project_brains, ora_council_logs, issues_cache."
    dev_only = False
    irreversible = False

    async def up(self, db) -> None:
        for coll, keys, opts in _INDEXES:
            try:
                await db[coll].create_index(keys, **opts)
            except Exception as e:
                # create_index is idempotent by name/spec — but a
                # spec-mismatch on an existing index will raise. Log
                # and keep going so one bad row doesn't block the rest.
                import logging
                logging.getLogger("aurem.migrations").warning(
                    "001 create_index %s %r failed: %r", coll, keys, e,
                )

    async def down(self, db) -> None:
        # Drop indexes in reverse order. We identify by name when
        # supplied (only the TTL entry has one), else by pymongo's
        # auto-generated key spec name.
        from pymongo.errors import OperationFailure

        def _index_name(keys, opts) -> str:
            if opts.get("name"):
                return opts["name"]
            if isinstance(keys, str):
                return f"{keys}_1"
            parts = [f"{k}_{v}" for k, v in keys]
            return "_".join(parts)

        for coll, keys, opts in reversed(_INDEXES):
            idx_name = _index_name(keys, opts)
            try:
                await db[coll].drop_index(idx_name)
            except OperationFailure:
                # index-not-found is fine on rollback — down should be
                # tolerant of partial-apply states.
                pass


# ── Legacy CLI shim ──────────────────────────────────────────────────
# Keeps the pre-framework invocation path working for anyone still
# calling `python -m migrations.001_aurem_upgrade_indexes` from a
# deploy script.

async def run_migrations():
    mongo_uri = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(
        mongo_uri,
        maxPoolSize=10, minPoolSize=1, maxIdleTimeMS=30_000,
        connectTimeoutMS=10_000,
    )
    try:
        db = client[db_name]
        print("Running AUREM upgrade migrations (legacy shim)...")
        await UpgradeIndexesMigration().up(db)
        print("✅ 001_aurem_upgrade_indexes complete.")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(run_migrations())
