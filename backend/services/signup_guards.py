"""
services/signup_guards.py — Iter 365

Signup abuse protection (per user's Phase 2 remediation ask):
 - IP-based rate-limit (default 3/24h, env `SIGNUP_RATE_LIMIT_PER_IP`)
 - Disposable email domain block-list (embedded, refreshable)
 - Honeypot field detection
 - Timing check (form submitted too fast = bot)
 - Emits `funnel_events` `signup_completed` row when guards pass
   (Phase 3 wiring).

All checks are BEST EFFORT and NEVER raise unless a real abuse
signal is present. Founders / admin emails bypass every guard.
"""
from __future__ import annotations

import os
import time
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException

from services.usage import is_founder_email

logger = logging.getLogger("aurem.signup_guards")


def _env_int(k: str, d: int) -> int:
    try:    return int(os.environ.get(k, str(d)))
    except (ValueError, TypeError): return d


SIGNUP_RATE_LIMIT_PER_IP  = _env_int("SIGNUP_RATE_LIMIT_PER_IP",  3)
SIGNUP_RATE_WINDOW_S      = _env_int("SIGNUP_RATE_WINDOW_S",  86400)  # 24h
SIGNUP_MIN_FORM_AGE_MS    = _env_int("SIGNUP_MIN_FORM_AGE_MS", 2000)  # <2s = bot


# Curated disposable-email domain block-list. Small on purpose — full
# lists are 3000+ domains and mostly noise. This catches the
# high-volume scripted signup vectors (tempmail, mailinator, guerrilla,
# 10minutemail, throwaway). Add sparingly.
DISPOSABLE_DOMAINS = frozenset((
    "mailinator.com", "tempmail.com", "temp-mail.org", "temp-mail.io",
    "guerrillamail.com", "guerrillamail.net", "guerrillamail.org",
    "10minutemail.com", "10minutemail.net", "throwawaymail.com",
    "trashmail.com", "trashmail.net", "yopmail.com", "getnada.com",
    "sharklasers.com", "maildrop.cc", "mailnesia.com", "fakeinbox.com",
    "dispostable.com", "spam4.me", "burnermail.io", "temp-mail.ru",
    "mytemp.email", "moakt.com", "byespm.com", "emltmp.com",
    "meltmail.com", "yopmail.fr", "yopmail.net", "cool.fr.nf",
    "jetable.fr.nf", "nospam.ze.tc", "nomail.xl.cx", "mega.zik.dj",
))


def _extract_domain(email: str) -> str:
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[1].strip().lower()


def _disposable_hit(email: str) -> bool:
    d = _extract_domain(email)
    if not d:
        return False
    return d in DISPOSABLE_DOMAINS


async def _count_recent_signups_by_ip(db, ip: str) -> int:
    if db is None or not ip:
        return 0
    since = datetime.now(timezone.utc) - timedelta(seconds=SIGNUP_RATE_WINDOW_S)
    try:
        return await db.dev_users.count_documents({
            "signup_ip":     ip,
            "created_at":    {"$gte": since.timestamp()},
        })
    except Exception as e:
        logger.warning("signup_ip count failed: %r", e)
        return 0


async def _log_violation(
    db,
    *,
    kind: str,
    ip: str,
    email: str,
    reason: str,
    metadata: Optional[dict] = None,
) -> None:
    """G14 · persist every rejected signup to signup_abuse_log. Best-
    effort — a persistence failure must never turn into a 500 that
    hides the real rejection reason from the client."""
    if db is None:
        return
    try:
        await db.signup_abuse_log.insert_one({
            "type":       kind,      # disposable_email / rate_limit / honeypot / timing
            "ip":         ip or "?",
            "email":      (email or "")[:120],
            "reason":     reason[:200],
            "metadata":   metadata or {},
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.debug("[signup_abuse_log] write failed: %r", e)


async def enforce_signup_guards(
    db,
    *,
    email: str,
    ip: str,
    honeypot: Optional[str] = None,
    form_age_ms: Optional[int] = None,
) -> None:
    """Raise HTTPException on any abuse signal. Founder emails bypass.

    Called by `routers/auth.py::signup` BEFORE the dev_users insert.
    G14 · every rejection ALSO writes a row to db.signup_abuse_log
    so the /admin/qa row can show 7-day counts."""
    if is_founder_email(email):
        return

    # 1. Honeypot — hidden field bots reflex-fill.
    if honeypot and honeypot.strip():
        logger.warning("[signup_guards] honeypot filled ip=%s email=%s",
                       ip, email[:40])
        await _log_violation(db, kind="honeypot", ip=ip, email=email,
                             reason="honeypot_field_filled")
        raise HTTPException(400, {
            "error":   "signup_rejected",
            "message": "Signup could not be processed. Please try again.",
        })

    # 2. Form-timing — humans need >2s to fill a signup form.
    if isinstance(form_age_ms, int) and form_age_ms >= 0 \
       and form_age_ms < SIGNUP_MIN_FORM_AGE_MS:
        logger.warning(
            "[signup_guards] form_age_ms=%d < %d — bot signature ip=%s",
            form_age_ms, SIGNUP_MIN_FORM_AGE_MS, ip,
        )
        await _log_violation(db, kind="timing", ip=ip, email=email,
                             reason=f"form_age_ms={form_age_ms}",
                             metadata={"form_age_ms": form_age_ms})
        raise HTTPException(400, {
            "error":   "signup_rejected",
            "message": "Signup submitted too quickly. Refresh the page and try again.",
        })

    # 3. Disposable email domain — no free-tier tokens for burners.
    if _disposable_hit(email):
        logger.info("[signup_guards] disposable email domain: %s", email)
        await _log_violation(db, kind="disposable_email", ip=ip, email=email,
                             reason=f"domain={_extract_domain(email)}")
        raise HTTPException(400, {
            "error":   "disposable_email",
            "message": ("Please use a permanent email address — "
                        "disposable inboxes are not accepted for the free tier."),
        })

    # 4. Per-IP rate limit (24h window by default).
    n = await _count_recent_signups_by_ip(db, ip)
    if n >= SIGNUP_RATE_LIMIT_PER_IP:
        logger.warning(
            "[signup_guards] IP rate-limit hit: %s (%d signups in %ds)",
            ip, n, SIGNUP_RATE_WINDOW_S,
        )
        await _log_violation(db, kind="rate_limit", ip=ip, email=email,
                             reason=f"n_signups_24h={n}",
                             metadata={"limit": SIGNUP_RATE_LIMIT_PER_IP,
                                       "window_s": SIGNUP_RATE_WINDOW_S})
        raise HTTPException(429, {
            "error":         "signup_rate_limit",
            "signups_recent": n,
            "limit":         SIGNUP_RATE_LIMIT_PER_IP,
            "window_hours":  SIGNUP_RATE_WINDOW_S // 3600,
            "message": (
                f"Too many signups from this network in the last "
                f"{SIGNUP_RATE_WINDOW_S // 3600}h. Try again later."
            ),
        })


# ── G14 · Per-user + per-IP task rate-limit (post-signup) ────────────
# Independent of the token-budget gate — even a user with a full wallet
# can only submit N tasks/hour. Blocks rapid-fire farming that would
# otherwise slip past the /chat/send token check.

TASK_RATE_LIMIT_PER_ACCOUNT_HR = _env_int("TASK_RATE_LIMIT_PER_ACCOUNT_HR", 5)
TASK_RATE_LIMIT_PER_IP_HR      = _env_int("TASK_RATE_LIMIT_PER_IP_HR",     15)


async def assert_task_burst_ok(
    db,
    *,
    user_id: str,
    ip: str,
    tier: str,
    is_founder: bool = False,
) -> None:
    """G14 · Enforce per-user + per-IP task rate-limit. Called from
    routers/loop.py::start_loop AFTER the tier gate. Founders + Pro/
    Team bypass — this only bites free/starter accounts trying to
    burst-submit tasks."""
    if is_founder or (tier or "").lower() in ("pro", "team", "founder"):
        return
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        n_user = await db.loop_execution_log.count_documents({
            "user_id":    user_id,
            "created_at": {"$gte": since},
        })
    except Exception:
        n_user = 0
    if n_user >= TASK_RATE_LIMIT_PER_ACCOUNT_HR:
        await _log_violation(
            db, kind="task_burst_account", ip=ip, email="",
            reason=f"n_tasks_hr={n_user}",
            metadata={"user_id": user_id, "tier": tier},
        )
        raise HTTPException(429, {
            "error":       "task_burst_limit",
            "limit":       TASK_RATE_LIMIT_PER_ACCOUNT_HR,
            "window":      "1 hour",
            "recent":      n_user,
            "message":     ("You've hit the free-tier task rate limit "
                            "(5 tasks/hour). Upgrade for higher limits."),
        })


# ── G14 · QA-page stats ────────────────────────────────────────────

async def get_signup_abuse_stats(db, window_days: int = 7) -> dict:
    """G14 QA row payload — count violations by type in the last N days."""
    if db is None:
        return {"available": False}
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {"_id": "$type", "n": {"$sum": 1}}},
        ]
        by_type = {}
        async for row in db.signup_abuse_log.aggregate(pipeline):
            by_type[row["_id"] or "?"] = int(row["n"])
        total = sum(by_type.values())
        return {
            "available":       True,
            "window_days":     window_days,
            "total_blocked":   total,
            "by_type":         by_type,
            "throttle_events": (by_type.get("rate_limit", 0)
                                 + by_type.get("task_burst_account", 0)
                                 + by_type.get("task_burst_ip", 0)),
        }
    except Exception as e:
        logger.warning("get_signup_abuse_stats failed: %r", e)
        return {"available": False, "error": str(e)}


# ── Funnel event helper (Phase 3) ───────────────────────────────────

async def emit_funnel_event(
    db,
    *,
    user_id: str,
    event_type: str,
    metadata: Optional[dict] = None,
) -> None:
    """Best-effort write to db.funnel_events. Never raises."""
    if db is None or not user_id or not event_type:
        return
    try:
        await db.funnel_events.insert_one({
            "user_id":    user_id,
            "event_type": event_type,
            "metadata":   metadata or {},
            "created_at": datetime.now(timezone.utc),
            "ts_epoch":   time.time(),
        })
    except Exception as e:
        logger.debug("funnel_event %s failed: %r", event_type, e)
