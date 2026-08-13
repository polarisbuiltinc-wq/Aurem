"""
services/user_deletion.py — Iter 388t · GDPR/DSAR self-serve delete.

Shared helper that fully purges a user account.  Used by BOTH:
  • POST /api/aurem-dev/auth/delete-me     (self-serve, JWT auth)
  • DELETE /api/aurem-dev/admin/users/{id} (admin cascade, refactored
                                            here to reuse this helper)

The helper is a superset of the earlier admin-only cascade in
routers/admin_users.py:730-747 which had two documented gaps:
  1. Stripe subscription cancellation — NEVER called.  User was
     deleted but billing continued.  Real revenue-continuity liability.
  2. GitHub App installation revocation — `github_installations` row
     wasn't in the cascade AND the real GitHub API `revoke_installation`
     was never called.  Repos stayed connected on GitHub's side.

This helper fixes both:
  • Cancels active Stripe subscription IMMEDIATELY (per founder
    decision — see the Bug 20 session log; "cancel_at_period_end"
    would leave the subscription in an ambiguous half-alive state
    when the account itself no longer exists to log in).
  • Calls github_app.revoke_installation() for every installation
    the user owns before deleting the local rows.

All external-API failures are logged + swallowed (best-effort) — a
GitHub 404 (user already uninstalled the app manually) or a Stripe
"subscription not found" should NOT block the local cascade.  The
returned dict surfaces per-step success so the caller can audit.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def cascade_delete_user_data(db, user_id: str) -> dict:
    """Full purge for user_id.  Returns a dict summarising every
    external cancellation + local collection deletion.

    Never raises — every step is wrapped in try/except so a partial
    failure (Stripe network hiccup, GitHub 404, one Mongo collection
    unavailable) doesn't block the rest of the purge.  The returned
    dict carries per-step success/error so the caller can audit."""
    report: dict = {
        "user_id":          user_id,
        "stripe_cancelled": None,   # None=no active sub, True=success, "error:...": failure
        "github_revoked":   [],     # list of installation_ids
        "github_errors":    [],
        "deletions":        {},     # collection → deleted_count (or -1 on error)
    }

    # ── 1. Look up the user record BEFORE deleting so we can extract
    #      external identifiers (Stripe subscription id, etc.).
    user_row = None
    try:
        user_row = await db.dev_users.find_one(
            {"user_id": user_id},
            {"_id": 0, "email": 1, "stripe_customer_id": 1,
             "stripe_subscription_id": 1, "subscription_id": 1},
        )
    except Exception as e:
        logger.warning("user_deletion[%s]: dev_users lookup failed: %r", user_id, e)

    # ── 2. Cancel active Stripe subscription IMMEDIATELY (per founder
    #      decision — an "active" Stripe subscription attached to a
    #      no-longer-loggable account is a confusing half-alive state).
    #      Uses stripe.Subscription.delete() which ends the sub at once
    #      and lets Stripe's own proration/refund policy handle the money.
    sub_id = None
    if user_row:
        sub_id = user_row.get("stripe_subscription_id") or user_row.get("subscription_id")
    if sub_id:
        try:
            import stripe
            from services.stripe_client import stripe_key
            k = stripe_key()
            if k:
                stripe.api_key = k
                stripe.Subscription.delete(sub_id)
                report["stripe_cancelled"] = True
                logger.info(
                    "user_deletion[%s]: cancelled stripe subscription %s",
                    user_id, sub_id,
                )
            else:
                report["stripe_cancelled"] = "error:no_stripe_key"
        except Exception as e:
            # Best-effort — log + continue.  Stripe outages / already-
            # cancelled subs / missing key must not block the purge.
            report["stripe_cancelled"] = f"error:{type(e).__name__}:{str(e)[:120]}"
            logger.warning(
                "user_deletion[%s]: stripe cancel failed for sub=%s: %r",
                user_id, sub_id, e,
            )

    # ── 3. Revoke all GitHub App installations the user owns BEFORE
    #      deleting local rows so we can look up the installation_ids.
    try:
        installs = await db.github_installations.find(
            {"user_id": user_id},
            {"_id": 0, "installation_id": 1, "active": 1},
        ).to_list(50)
    except Exception as e:
        installs = []
        logger.warning("user_deletion[%s]: github_installations query failed: %r",
                       user_id, e)

    if installs:
        try:
            from services import github_app as _ga
        except Exception as e:
            _ga = None
            report["github_errors"].append(f"import_failed:{type(e).__name__}")
        for inst in installs:
            iid = inst.get("installation_id")
            if not iid:
                continue
            if _ga is None:
                report["github_errors"].append(f"skip:{iid}:no_module")
                continue
            # Only try to revoke if it looks active — a row already
            # marked inactive is idempotent, no point calling GitHub.
            if inst.get("active") is False:
                report["github_revoked"].append({"id": iid, "skipped": "inactive"})
                continue
            try:
                await _ga.revoke_installation(int(iid))
                report["github_revoked"].append({"id": iid, "ok": True})
            except Exception as e:
                # GitHub returns 404 if the user manually uninstalled
                # the app earlier — treat as success (goal achieved).
                report["github_errors"].append(
                    f"{iid}:{type(e).__name__}:{str(e)[:80]}"
                )
                logger.warning(
                    "user_deletion[%s]: gh revoke_installation(%s) failed: %r",
                    user_id, iid, e,
                )

    # ── 4. Delete Mongo rows across every user-scoped collection.  We
    #      keep each delete in its own try/except so one failed
    #      collection doesn't block the rest.  15 collections total
    #      (was 10 in the admin-only path):
    _CASCADE = [
        # Original 10 (mirrors admin_users.py:730-741 pre-refactor)
        ("dev_users",           "user_id"),
        ("cto_sessions",        "user_id"),
        ("chat_sessions",       "user_id"),
        ("cto_projects",        "user_id"),
        ("cto_tasks",           "user_id"),
        ("cto_payments",        "user_id"),
        ("api_keys",            "user_id"),
        ("post_task_scans",     "user_id"),
        ("warm_start_jobs",     "user_id"),
        ("oauth_codes",         "user_id"),
        # Added in Iter 388t · GDPR self-serve delete
        ("github_installations", "user_id"),
        ("ui_settings",          "user_id"),
        ("user_seo_claims",      "user_id"),
        ("login_attempts",       "user_id"),
        ("oauth_states",         "user_id"),
    ]
    for coll, key in _CASCADE:
        try:
            res = await db[coll].delete_many({key: user_id})
            report["deletions"][coll] = res.deleted_count
        except Exception as e:
            logger.warning(
                "user_deletion[%s]: %s delete failed: %r", user_id, coll, e,
            )
            report["deletions"][coll] = -1

    report["email"] = (user_row or {}).get("email", "")
    return report


async def is_founder(email: Optional[str]) -> bool:
    """True iff `email` is on the FOUNDER_EMAILS deploy-time list.
    Founders MUST NOT be deletable via self-serve (would brick login
    + billing infrastructure); they must contact support instead."""
    import os
    if not email:
        return False
    e_lower = email.strip().lower()
    founder_list = [
        e.strip().lower() for e in
        (os.environ.get("FOUNDER_EMAILS") or "").split(",") if e.strip()
    ]
    return e_lower in founder_list
