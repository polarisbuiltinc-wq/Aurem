"""
migrations/002_encrypt_pats.py
==============================
Encrypts plaintext `github_token` values in `cto_projects` using the
per-customer vault. Idempotent — rows already encrypted (token starts
with "v1:") are skipped.

Marked irreversible: rolling back encryption would leak plaintext
tokens to disk and is never a safe automated operation. Rollback is a
deliberate incident-response call, not a framework knob.

New framework:
    python -m backend.migrations up

Legacy invocation (still works):
    python -m migrations.002_encrypt_pats
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

from motor.motor_asyncio import AsyncIOMotorClient

# Ensure backend root on path so `services.vault` resolves when this
# file is imported by the framework's dynamic loader.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from services.vault import encrypt, is_vault_available  # noqa: E402

from .base import Migration


class EncryptPatsMigration(Migration):
    version = "002"
    name = "encrypt_pats"
    description = "Encrypt plaintext GitHub PATs in cto_projects.github_token."
    dev_only = False
    irreversible = True   # rollback would leak plaintext tokens

    async def up(self, db) -> None:
        if not is_vault_available():
            raise RuntimeError(
                "AUREM_MASTER_KEY missing or too short — cannot encrypt PATs."
            )
        cursor = db.cto_projects.find(
            {"github_token": {"$exists": True, "$nin": [None, ""]}},
            {"project_id": 1, "user_id": 1, "github_token": 1, "_id": 1},
        )
        docs = await cursor.to_list(length=10_000)
        migrated = already = skipped = 0
        for d in docs:
            tok = (d.get("github_token") or "").strip()
            uid = d.get("user_id") or ""
            if not tok:
                skipped += 1
                continue
            if tok.startswith("v1:"):
                already += 1
                continue
            if not uid:
                skipped += 1
                continue
            try:
                ct = await encrypt(uid, tok, kind="github_token")
            except Exception:
                skipped += 1
                continue
            await db.cto_projects.update_one(
                {"_id": d["_id"]},
                {"$set": {
                    "github_token":     ct,
                    "pat_encrypted":    True,
                    "pat_encrypted_at": time.time(),
                }},
            )
            migrated += 1
        # Store rollup counters on the migration_history row for audit.
        # (framework only writes the standard fields; individual
        # migrations can decorate the history row via a companion
        # collection if needed. We keep it simple: just log.)
        import logging
        logging.getLogger("aurem.migrations").info(
            "002_encrypt_pats: migrated=%d already=%d skipped=%d",
            migrated, already, skipped,
        )

    async def down(self, db) -> None:
        raise NotImplementedError(
            "002_encrypt_pats is irreversible: rollback would leak "
            "plaintext GitHub PATs. Rotate compromised tokens instead."
        )


# ── Legacy CLI shim ──────────────────────────────────────────────────

async def migrate():
    mongo_uri = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(
        mongo_uri,
        maxPoolSize=10, minPoolSize=1, maxIdleTimeMS=30_000,
        connectTimeoutMS=10_000,
    )
    try:
        db = client[db_name]
        print("Running 002_encrypt_pats (legacy shim)...")
        await EncryptPatsMigration().up(db)
        print("✅ 002_encrypt_pats complete.")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(migrate())
