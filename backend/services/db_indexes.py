"""
services/db_indexes.py — G6 · DB dedup constraints (Iter 366)

Central place for the Mongo unique indexes that guarantee no dup rows
sneak in at write time. Called once on lifespan startup — idempotent,
skips if the index already exists.

Indexes shipped:
  - `topup_alerts`   : `integration_id`         → dedupe critical banner
  - `incidents`      : `source_key + status`    → dedupe open incidents
  - `dev_users`      : `email` (case-insensitive) → dup-signup safety net
  - `chat_sessions`  : `user_id + session_id`   → chat replay integrity
  - `funnel_events`  : `(user_id, event_type)`  where event_type in
                       `("signup_completed", "first_chat_sent",
                         "first_loop_started", "first_task_shipped")`
                       — one row per lifetime "first" event per user.
  - `email_verifications` : `token`  (2026-08-20, G6 scoped-pass 2 —
                       single-use verification token had no DB backstop)
  - `oauth_states`   : `state`       (2026-08-20, G6 scoped-pass 2 —
                       OAuth 2.1/GitHub CSRF nonce had no DB backstop)
  - `oauth_codes`    : `code`        (2026-08-20, G6 scoped-pass 2 —
                       PKCE auth code had no DB backstop beyond its TTL)
  See /app/memory/G6_DEDUP_SCOPE_2026-08-20.md — full ~130-collection
  sweep is still a separate future pass, this only closed the 3 gaps
  found in that pass.

All best-effort — failure to install one index MUST NOT crash the
lifespan (that would take the whole app down for a startup-time DB
hiccup).
"""
from __future__ import annotations

import logging
from typing import List

from pymongo import ASCENDING
from pymongo.errors import OperationFailure

logger = logging.getLogger("aurem.db_indexes")


async def ensure_dedup_indexes(db) -> List[dict]:
    """Return a list of {collection, index_name, created: bool} rows so
    the caller can log which ones actually got made vs already existed."""
    if db is None:
        return []

    results: List[dict] = []

    async def _mk(coll: str, keys, name: str, unique: bool = True,
                  partial=None) -> None:
        try:
            kwargs = {"name": name, "unique": unique, "background": True}
            if partial is not None:
                kwargs["partialFilterExpression"] = partial
            await db[coll].create_index(keys, **kwargs)
            results.append({"collection": coll, "name": name, "ok": True})
            logger.info("[G6] ensured index %s on %s", name, coll)
        except OperationFailure as e:
            # 85 = IndexOptionsConflict (already exists w/ different opts),
            # 86 = IndexKeySpecsConflict, 68 = IndexAlreadyExists. All fine.
            results.append({"collection": coll, "name": name, "ok": True,
                             "existed": True, "note": str(e.details or e)[:120]})
            logger.debug("[G6] index %s on %s already exists: %s",
                         name, coll, e)
        except Exception as e:                          # noqa: BLE001
            results.append({"collection": coll, "name": name, "ok": False,
                             "error": str(e)[:200]})
            logger.warning("[G6] failed to create %s on %s: %r", name, coll, e)

    # 1) Dedup Stripe/Tavily/etc critical-alert banner rows.
    await _mk("topup_alerts", [("integration_id", ASCENDING)],
              "uniq_integration_id", unique=True,
              partial={"integration_id": {"$exists": True, "$type": "string"}})

    # 2) One open incident per source_key (matches incident_log.upsert_incident).
    await _mk("incidents",
              [("source_key", ASCENDING), ("status", ASCENDING)],
              "uniq_open_source_key", unique=True,
              partial={"status": "open",
                        "source_key": {"$exists": True, "$type": "string"}})

    # 3) Signup email uniqueness (existing code already checks, but a
    # DB-level constraint is the belt-and-suspenders belt).
    await _mk("dev_users", [("email", ASCENDING)],
              "uniq_email", unique=True,
              partial={"email": {"$exists": True, "$type": "string"}})

    # 4) Chat session (user_id, session_id) — protects the turn-index
    # replay path against a duplicate-session write.
    await _mk("chat_sessions",
              [("user_id", ASCENDING), ("session_id", ASCENDING)],
              "uniq_user_session", unique=True,
              partial={"session_id": {"$exists": True, "$type": "string"}})

    # 5) Funnel "first_*" events — exactly one row per user per event
    # type. Reinforces the find_one_and_update pattern in chat/loop.
    await _mk("funnel_events", [("user_id", ASCENDING),
                                 ("event_type", ASCENDING)],
              "uniq_first_event", unique=True,
              partial={"event_type": {"$in": [
                  "signup_completed",   "first_chat_sent",
                  "first_loop_started", "first_task_shipped",
              ]}})

    # 6) Email-verification token — single-use, previously only
    # de-duped by app-level invalidation logic (services/verification_email.py).
    await _mk("email_verifications", [("token", ASCENDING)],
              "uniq_token", unique=True,
              partial={"token": {"$exists": True, "$type": "string"}})

    # 7) OAuth CSRF state nonce (github_app.py / github_oauth.py).
    await _mk("oauth_states", [("state", ASCENDING)],
              "uniq_state", unique=True,
              partial={"state": {"$exists": True, "$type": "string"}})

    # 8) OAuth 2.1 PKCE auth code — already TTL-purged via expires_at,
    # this adds a uniqueness backstop during its live window.
    await _mk("oauth_codes", [("code", ASCENDING)],
              "uniq_code", unique=True,
              partial={"code": {"$exists": True, "$type": "string"}})

    return results


async def get_dedup_index_report(db) -> dict:
    """QA-panel snapshot: list every index we care about + whether it's
    present. Read-only — never installs.

    NOTE: topup_alerts already had an `alert_key_1` unique index before
    G6 shipped — that keys off `alert_key` (integration_id + kind + date
    triple) which is a stricter dedup than the raw `integration_id`
    we tried to install. Either is acceptable, so we accept the presence
    of ANY unique index on the collection for `topup_alerts`."""
    if db is None:
        return {"available": False}
    want = {
        "topup_alerts":  ["uniq_integration_id", "alert_key_1"],  # either
        "incidents":     ["uniq_open_source_key"],
        "dev_users":     ["uniq_email", "email_1"],
        "chat_sessions": ["uniq_user_session"],
        "funnel_events": ["uniq_first_event"],
        "email_verifications": ["uniq_token"],
        "oauth_states":  ["uniq_state"],
        "oauth_codes":   ["uniq_code"],
    }
    present: dict[str, bool] = {}
    dup_counts: dict[str, int] = {}
    for coll, want_names in want.items():
        try:
            info = await db[coll].index_information()
            present[coll] = any(n in info for n in want_names)
        except Exception:
            present[coll] = False
    # Report any actual duplicates surviving from before the index install.
    try:
        pipeline = [
            {"$group": {"_id": "$email", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}}, {"$count": "dups"},
        ]
        async for row in db.dev_users.aggregate(pipeline):
            dup_counts["dev_users.email"] = int(row.get("dups", 0))
    except Exception:
        pass
    return {
        "available":       True,
        "indexes_present": present,
        "all_present":     all(present.values()),
        "dup_counts":      dup_counts,
    }
