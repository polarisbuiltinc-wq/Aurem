#!/usr/bin/env python3
"""
scripts/rotate_password.py — 2026-08-19

One-time manual rotation for a `dev_users` account's password, for use
when the self-service reset flow (/auth/forgot-password) can't be used
(e.g. rotating a credential that leaked into git history before the
self-service flow existed).

No credentials are hardcoded here. Reads MONGO_URL/DB_NAME from the
environment (same as the rest of this app) and prompts for the new
password interactively via getpass, so it never appears in shell
history or process listings.

Usage:
    MONGO_URL=... DB_NAME=... python scripts/rotate_password.py --email you@example.com

Requires --confirm to actually write (safety rail against accidental runs):
    python scripts/rotate_password.py --email you@example.com --confirm
"""
from __future__ import annotations
import argparse
import asyncio
import getpass
import os
import sys

import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="Account email to rotate")
    ap.add_argument("--confirm", action="store_true",
                     help="Actually write the change (omit to dry-run)")
    args = ap.parse_args()
    email = args.email.strip().lower()

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "aurem_dev")
    if not mongo_url:
        print("ERROR: MONGO_URL not set in environment.", file=sys.stderr)
        return 1

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    user = await db.dev_users.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
    if not user:
        print(f"ERROR: no dev_users row found for {email}", file=sys.stderr)
        return 1

    print(f"Found account: user_id={user['user_id']} email={user['email']} "
          f"tier={user.get('tier')} has_password={bool(user.get('password'))}")

    new_password = getpass.getpass("New password (min 8 chars, not echoed): ")
    if len(new_password) < 8:
        print("ERROR: password must be at least 8 characters.", file=sys.stderr)
        return 1
    confirm_password = getpass.getpass("Confirm new password: ")
    if new_password != confirm_password:
        print("ERROR: passwords did not match.", file=sys.stderr)
        return 1

    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

    if not args.confirm:
        print("DRY RUN — no write performed. Re-run with --confirm to apply.")
        return 0

    await db.dev_users.update_one(
        {"user_id": user["user_id"]}, {"$set": {"password": hashed}},
    )
    # Best-effort: also invalidate any outstanding self-service reset
    # tokens for this account so an old link can't be replayed.
    try:
        await db.password_reset_tokens.update_many(
            {"user_id": user["user_id"]}, {"$set": {"used": True}},
        )
    except Exception:
        pass
    print(f"DONE — password rotated for {user['email']}. "
          "Existing JWT sessions remain valid until they expire; use "
          "POST /auth/revoke-all-sessions if you want to force logout too.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
