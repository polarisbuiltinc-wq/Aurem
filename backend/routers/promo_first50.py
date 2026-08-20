"""
routers/promo_first50.py — Track 3 (item #31)

First-50 signup promo + email verification.

Endpoints:
  • GET  /api/aurem-dev/promo/first50/status
        Public. Real-time counter for the landing page badge.
        `{claimed, total, remaining, is_active}`.

  • GET  /api/aurem-dev/auth/verify?token=xyz
        Public single-click verification. Steps (all atomic):
          1. Consume the `email_verifications` row (findAndModify
             where `used_at IS NULL AND expires_at > now`).
          2. Mark `dev_users.email_verified = true`.
          3. Try to claim one of the 50 promo spots via
             `promo_first50_state` singleton
             (`findAndModify` with `$expr spots_claimed<total`).
          4. If claimed → upgrade tier to `pro` with a 30-day
             `pro_expires_at` (auto-downgrade handled by cron).
          5. 302 redirect to `/verify?status=ok&claimed=<bool>` on
             the frontend so the founder sees a themed success page.

  • POST /api/aurem-dev/auth/resend-verification
        Authenticated. Lets a logged-in unverified user request a
        fresh link if the first email was lost. Rate-limited via
        the existing global 300/min/IP middleware; per-user cap of
        1 resend / 15 min enforced in this handler.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.rate_limiter import check_rate_limit_async, client_ip_from_request
from services.signup_guards import _disposable_hit, _extract_domain
from services.verification_email import (
    send_verification_email, create_verification_token,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Promo First50"])

# ── Tunables ─────────────────────────────────────────────────────────
PROMO_TOTAL_SPOTS   = int(os.environ.get("PROMO_FIRST50_TOTAL", "50"))
PROMO_PRO_DAYS      = int(os.environ.get("PROMO_FIRST50_PRO_DAYS", "30"))
PROMO_SINGLETON_ID  = "global"
RESEND_COOLDOWN_MIN = 15

PUBLIC_BASE         = os.environ.get(
    "PUBLIC_APP_URL", "https://auremcto.com",
).rstrip("/")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _ensure_singleton(db) -> dict:
    """Idempotent — create the `{_id: 'global'}` row on first call."""
    doc = await db.promo_first50_state.find_one_and_update(
        {"_id": PROMO_SINGLETON_ID},
        {"$setOnInsert": {
            "_id":            PROMO_SINGLETON_ID,
            "total":          PROMO_TOTAL_SPOTS,
            "spots_claimed":  0,
            "is_active":      True,
            "created_at":     _now(),
        }},
        upsert=True,
        return_document=True,
    )
    if not doc:
        doc = await db.promo_first50_state.find_one({"_id": PROMO_SINGLETON_ID})
    return doc or {
        "total": PROMO_TOTAL_SPOTS, "spots_claimed": 0, "is_active": True,
    }


# ── Public counter ───────────────────────────────────────────────────
@router.get("/promo/first50/status")
async def first50_status() -> dict:
    """Public — real-time counter for the landing badge."""
    db = get_db()
    if db is None:
        return {
            "claimed":   0,
            "total":     PROMO_TOTAL_SPOTS,
            "remaining": PROMO_TOTAL_SPOTS,
            "is_active": False,
        }
    doc = await _ensure_singleton(db)
    claimed   = int(doc.get("spots_claimed", 0))
    total     = int(doc.get("total", PROMO_TOTAL_SPOTS))
    remaining = max(0, total - claimed)
    return {
        "claimed":   claimed,
        "total":     total,
        "remaining": remaining,
        "is_active": bool(doc.get("is_active", True)) and remaining > 0,
    }


# ── The verification click ───────────────────────────────────────────
def _redirect_to_frontend(status: str, claimed: bool = False,
                          reason: str = "") -> RedirectResponse:
    """302 to `/verify?...` on the frontend. Never leaks the token."""
    params = {"status": status}
    if claimed:
        params["claimed"] = "1"
    if reason:
        params["reason"] = reason
    target = f"{PUBLIC_BASE}/verify?{urlencode(params)}"
    return RedirectResponse(url=target, status_code=302)


@router.get("/auth/verify")
async def verify_email(token: str = ""):
    """Single-use email verification. 302-redirects to the frontend
    success/error page. NEVER echoes the token back to the client."""
    db = get_db()
    if db is None:
        return _redirect_to_frontend("error", reason="db_unavailable")
    if not token or not token.strip():
        return _redirect_to_frontend("error", reason="missing_token")

    now = _now()
    # ── Step 1: atomically consume the token ─────────────────────
    # findOneAndUpdate ensures a single-use guarantee — two concurrent
    # clicks can't both mark the same token as used.
    row = await db.email_verifications.find_one_and_update(
        {"token": token, "used_at": None, "expires_at": {"$gt": now}},
        {"$set": {"used_at": now}},
        return_document=True,
    )
    if not row:
        # Distinguish expired vs already-used vs unknown for the UI.
        existing = await db.email_verifications.find_one(
            {"token": token},
            {"_id": 0, "used_at": 1, "expires_at": 1},
        )
        if not existing:
            return _redirect_to_frontend("error", reason="invalid_token")
        if existing.get("used_at"):
            return _redirect_to_frontend("ok", reason="already_verified")
        return _redirect_to_frontend("error", reason="expired_token")

    user_id = row["user_id"]

    # ── Step 2: mark user verified (idempotent) ──────────────────
    user_doc = await db.dev_users.find_one_and_update(
        {"user_id": user_id},
        {"$set": {
            "email_verified":    True,
            "email_verified_at": now,
        }},
        return_document=True,
    )
    if not user_doc:
        logger.warning("verify: user %s not found for token", user_id)
        return _redirect_to_frontend("error", reason="user_not_found")

    # 2026-08-20 — real funnel_events row so `/admin/funnel`'s
    # `event_counts` (aggregate) and the per-user Activity Log both
    # see this milestone. Previously only inferable from
    # `email_verifications.used_at`, which the aggregate endpoint
    # never queries.
    from services.signup_guards import emit_funnel_event
    await emit_funnel_event(
        db, user_id=user_id, event_type="email_verified", metadata={},
    )

    # Track 3 (item #33) — fire the post-verification welcome email
    # as a safe_bg task. Idempotent by campaign + user_id inside the
    # welcome module itself, so a second verify click is a no-op.
    try:
        from services.welcome_email import send_welcome_email
        from services.bg_safe import safe_bg
        import asyncio as _asyncio
        _asyncio.create_task(safe_bg(send_welcome_email)(db, {
            "user_id": user_doc["user_id"],
            "email":   user_doc.get("email", ""),
            "name":    user_doc.get("name", ""),
        }))
    except Exception as e:                              # noqa: BLE001
        logger.warning("welcome email dispatch failed: %r", e)

    already_claimed = bool(user_doc.get("promo_first50_claimed"))
    if already_claimed:
        return _redirect_to_frontend("ok", claimed=True,
                                     reason="already_claimed")

    # ── Step 3: atomic promo spot claim ──────────────────────────
    await _ensure_singleton(db)
    slot = await db.promo_first50_state.find_one_and_update(
        {"_id": PROMO_SINGLETON_ID,
         "is_active": True,
         "$expr": {"$lt": ["$spots_claimed", "$total"]}},
        {"$inc": {"spots_claimed": 1}},
        return_document=True,
    )
    if slot is None:
        # Promo is sold out or paused. Verification still succeeded.
        return _redirect_to_frontend("ok", claimed=False,
                                     reason="promo_full")

    # ── Step 4: 30-day Pro upgrade ───────────────────────────────
    pro_expires = now + timedelta(days=PROMO_PRO_DAYS)
    upd = await db.dev_users.find_one_and_update(
        {"user_id": user_id, "promo_first50_claimed": {"$ne": True}},
        {"$set": {
            "tier":                    "pro",
            "promo_first50_claimed":   True,
            "promo_first50_claimed_at": now,
            "pro_expires_at":          pro_expires,
        }},
        return_document=True,
    )
    if not upd:
        # Race: another concurrent verify already claimed for the same
        # user. Give the spot back to keep the counter honest.
        await db.promo_first50_state.update_one(
            {"_id": PROMO_SINGLETON_ID, "spots_claimed": {"$gt": 0}},
            {"$inc": {"spots_claimed": -1}},
        )
        return _redirect_to_frontend("ok", claimed=True,
                                     reason="already_claimed")

    return _redirect_to_frontend("ok", claimed=True)


# ── Resend verification link ─────────────────────────────────────────
@router.post("/auth/resend-verification")
async def resend_verification(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Authenticated user asks for a fresh verification link.
    Cooldown: 1 resend per 15 min per user. Silent no-op for already
    verified users (returns ok=True so we don't leak account state)."""
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")
    user = await db.dev_users.find_one(
        {"user_id": me["user_id"]},
        {"_id": 0, "user_id": 1, "email": 1, "name": 1,
         "email_verified": 1},
    )
    if not user:
        raise HTTPException(404, "user not found")
    if user.get("email_verified"):
        return {"ok": True, "already_verified": True}

    # Cooldown check via last `sent_at` on our audit row.
    cutoff = _now() - timedelta(minutes=RESEND_COOLDOWN_MIN)
    recent = await db.onboarding_emails.find_one(
        {"user_id": user["user_id"], "campaign": "email_verification",
         "sent_at": {"$gt": cutoff}},
        {"_id": 1},
    )
    if recent:
        raise HTTPException(
            429,
            f"Please wait {RESEND_COOLDOWN_MIN} minutes between "
            "verification-email requests.",
        )
    result = await send_verification_email(db, user)
    return {"ok": bool(result.get("ok"))}


# ── Downgrade helper (called by cron) ────────────────────────────────
async def downgrade_expired_promos(db) -> dict:
    """Idempotent — every user whose `pro_expires_at` has passed and
    who is still on tier=pro gets downgraded to free. Safe to run
    every hour. Founders + never-promo Pro subscribers are excluded
    via the `promo_first50_claimed=True` gate.

    Returns a summary dict for logging."""
    now = _now()
    q = {
        "promo_first50_claimed": True,
        "tier":                  "pro",
        "pro_expires_at":        {"$lt": now},
        # never touch a founder or a paid Pro subscriber
        "is_admin":              {"$ne": True},
        "stripe_subscription_active": {"$ne": True},
    }
    matched = 0
    async for u in db.dev_users.find(q, {"_id": 0, "user_id": 1}):
        await db.dev_users.update_one(
            {"user_id": u["user_id"]},
            {"$set": {"tier": "free", "promo_downgraded_at": now}},
        )
        matched += 1
    if matched:
        logger.info("promo_first50: downgraded %d expired Pro users", matched)
    return {"downgraded": matched, "at": now.isoformat()}


async def downgrade_cron(interval_seconds: int = 3600) -> None:
    """Hourly loop that runs `downgrade_expired_promos`. Fires
    silently — errors surface via logger only."""
    import asyncio
    while True:
        try:
            db = get_db()
            if db is not None:
                await downgrade_expired_promos(db)
        except Exception as e:                          # noqa: BLE001
            logger.warning("downgrade_cron tick failed: %r", e)
        await asyncio.sleep(interval_seconds)


# ── Waitlist ─────────────────────────────────────────────────────────
# Scope (founder-locked 2026-02-09): email capture only. Same
# disposable/rate-limit guards as signup. NO automatic conversion when
# a spot frees up — founder does manual outreach for now. Reason: at
# current scale we do not yet know demand shape, so no autopilot logic
# gets built. Note on "can a spot free up?": today, no code path
# decrements `promo_first50_state.spots_claimed` beyond the internal
# race-compensation branch in `verify_email`. Admin user deletes via
# /api/aurem-dev/admin/users/{user_id} do NOT roll the counter back —
# so spots effectively don't free up automatically. If founder ever
# wants to open more spots they either bump the singleton `total`
# directly in Mongo or reach out to the waitlist manually.

_WAITLIST_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# `check_rate_limit_async` is a per-minute sliding window (60s hard-
# coded). We pick 5/min per IP → a generous 300/hour ceiling that
# still blocks abusive scripted flooding while leaving humans headroom.
# Idempotent upsert handles duplicate submits from the same address.
WAITLIST_RATE_PER_IP_MIN = int(os.environ.get("WAITLIST_RATE_PER_IP_MIN", "5"))


class _WaitlistBody(BaseModel):
    email: str


@router.post("/promo/first50/waitlist")
async def first50_waitlist(
    body: _WaitlistBody,
    request: Request,
) -> dict:
    """Public — capture email when the promo is sold out. Idempotent,
    disposable-blocked, per-IP rate-limited. Never leaks whether an
    email is already on the list (prevents enumeration)."""
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")

    email = (body.email or "").strip().lower()
    if not email or not _WAITLIST_RE.match(email) or len(email) > 254:
        raise HTTPException(400, {
            "error":   "invalid_email",
            "message": "Please enter a valid email address.",
        })

    # Disposable-email block (reuse the signup allow-list for parity).
    if _disposable_hit(email):
        raise HTTPException(400, {
            "error":   "disposable_email",
            "message": ("Please use a permanent email address — "
                        "disposable inboxes are not accepted."),
        })

    # Per-IP rate limit — 5/minute (~300/hour ceiling). Reuses the same
    # Redis-backed limiter every other endpoint uses.
    client_ip = client_ip_from_request(request)
    if not await check_rate_limit_async(
        f"waitlist-ip:{client_ip}", WAITLIST_RATE_PER_IP_MIN,
    ):
        raise HTTPException(429, {
            "error":   "too_many_requests",
            "message": (
                "Too many waitlist submissions from this network. "
                "Try again in a minute."
            ),
        })

    now = _now()
    # Upsert so a repeat submission is idempotent (bumps `last_touch_at`
    # + a click_count without spawning duplicate rows). `created_at`
    # sticks via $setOnInsert.
    await db.promo_first50_waitlist.update_one(
        {"email": email},
        {
            "$setOnInsert": {
                "email":       email,
                "created_at":  now,
                "signup_ip":   client_ip,
                "converted":   False,
            },
            "$set": {"last_touch_at": now},
            "$inc": {"touch_count": 1},
        },
        upsert=True,
    )
    return {"ok": True}
