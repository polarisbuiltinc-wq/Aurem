"""
services/first50_campaign.py — First-50 drip campaign (2026-02-12).

Automated 3-stage drip for existing users:
  Stage 0 · Day 0 — initial First-50 offer (unverified users)
  Stage 3 · Day 3 — reminder (only if still no task created)
  Stage 7 · Day 7 — final push (only if still no task created)

Reuses:
  · Resend (via same pattern as services/onboarding_email.py)
  · funnel_events collection (signup + verify signals)
  · cto_tasks collection (task-created stop condition)
  · email_unsubscribes collection (CAN-SPAM / GDPR compliance)

State collection: first50_campaign_state
  { user_id, email, stage_sent: [0,3,7], sent_at: [{stage, ts, message_id}],
    stopped_reason: null|"task_created"|"unsubscribed", updated_at }
"""
from __future__ import annotations

import hashlib
import hmac
import html as _html
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from cto_services.db import get_db

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────
CAMPAIGN         = "first50_drip"
PUBLIC_BASE      = os.environ.get("PUBLIC_APP_URL", "https://auremcto.com").rstrip("/")
UNSUB_SECRET     = os.environ.get("UNSUBSCRIBE_SECRET", "aurem-unsub-secret-rotate-me")
VIDEO_URL        = os.environ.get("HOW_TO_START_VIDEO_URL", "").strip()
SIGNOFF          = "— Tejinder Sandhu, Founder, Aurem"

STAGE_HOURS = {0: 0, 3: 72, 7: 168}

SUBJECTS = {
    0: "You're 1 click away from 30 days of Pro on ORA (First-50)",
    3: "Stuck on your first ORA task? 60-second walkthrough",
    7: "Your ORA Pro window closes soon",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def unsub_token(email: str) -> str:
    return hmac.new(UNSUB_SECRET.encode(), email.lower().strip().encode(),
                    hashlib.sha256).hexdigest()[:16]


def unsub_url(email: str) -> str:
    return (f"{PUBLIC_BASE}/api/aurem-dev/unsubscribe"
            f"?t={unsub_token(email)}&e={quote(email)}")


# ── Templates ────────────────────────────────────────────────────────
def _footer_html(email: str) -> str:
    url = unsub_url(email)
    return (f'<p style="color:#666;font-size:11px;margin-top:32px;'
            f'padding-top:16px;border-top:1px solid #eee">'
            f'You\'re receiving this because you signed up at '
            f'<a href="{PUBLIC_BASE}" style="color:#666">auremcto.com</a>. '
            f'<a href="{_html.escape(url)}" style="color:#666;'
            f'text-decoration:underline">Unsubscribe</a> — one click, '
            f'no login required.</p>')


def _footer_text(email: str) -> str:
    return (f"\n\n—\nYou're receiving this because you signed up at "
            f"auremcto.com.\nUnsubscribe (one click, no login): "
            f"{unsub_url(email)}")


def render_stage(stage: int, user: dict, *,
                 promo_remaining: Optional[int] = None) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body) for the given stage."""
    name  = user.get("name") or (user.get("email") or "").split("@")[0] or "there"
    email = user.get("email") or ""
    subject = SUBJECTS[stage]

    if stage == 0:
        remaining_line = (
            f"Only {promo_remaining} of 50 founder spots remain — "
            f"lock yours in one click:"
            if promo_remaining is not None and promo_remaining > 0
            else "Verify your email to activate your account:"
        )
        text = (
            f"Hey {name},\n\n"
            "Quick note — your ORA account is still unverified, which means "
            "you haven't claimed the First-50 offer yet: 30 days of Pro "
            "(300 tasks/mo, 50k tokens, Maxx mode) on the house.\n\n"
            f"{remaining_line}\n"
            f"  {PUBLIC_BASE}/verify?resend=1\n\n"
            "Once verified, drop your first repo and ask ORA anything — "
            "'find security issues', 'add a test for X', 'refactor Y'. "
            "It ships the commit.\n\n"
            f"{SIGNOFF}"
        )
        html = (
            f'<div style="font-family:-apple-system,sans-serif;max-width:560px;'
            f'margin:0 auto;padding:32px 24px;color:#111">'
            f'<p>Hey {_html.escape(name)},</p>'
            f'<p>Quick note — your ORA account is still unverified, which '
            f'means you haven\'t claimed the <b>First-50 offer</b> yet: '
            f'30 days of Pro (300 tasks/mo, 50k tokens, Maxx mode) on the '
            f'house.</p>'
            f'<p>{_html.escape(remaining_line)}</p>'
            f'<p style="margin:24px 0"><a href="{PUBLIC_BASE}/verify?resend=1" '
            f'style="background:#eab308;color:#000;padding:12px 24px;'
            f'text-decoration:none;border-radius:8px;font-weight:600;'
            f'display:inline-block">Verify + claim Pro</a></p>'
            f'<p>Once verified, drop your first repo and ask ORA anything — '
            f'\'find security issues\', \'add a test for X\', \'refactor Y\'. '
            f'It ships the commit.</p>'
            f'<p>{_html.escape(SIGNOFF)}</p>'
            f'{_footer_html(email)}</div>'
        )

    elif stage == 3:
        video_line_text = (f"60-second walkthrough: {VIDEO_URL}\n\n"
                           if VIDEO_URL else "")
        video_line_html = (f'<p><a href="{_html.escape(VIDEO_URL)}" '
                           f'style="color:#eab308">60-second walkthrough</a></p>'
                           if VIDEO_URL else "")
        text = (
            f"Hey {name},\n\n"
            "Noticed you haven't kicked off an ORA task yet. Most people who "
            "get stuck at this step just don't know what to ask first.\n\n"
            f"{video_line_text}"
            "Simplest first prompt that always works:\n"
            "  \"Scan my repo for security issues and open a PR to fix "
            "the top one.\"\n\n"
            "Paste that in and ORA does the rest.\n\n"
            f"{PUBLIC_BASE}/chat\n\n"
            f"{SIGNOFF}"
        )
        html = (
            f'<div style="font-family:-apple-system,sans-serif;max-width:560px;'
            f'margin:0 auto;padding:32px 24px;color:#111">'
            f'<p>Hey {_html.escape(name)},</p>'
            f'<p>Noticed you haven\'t kicked off an ORA task yet. Most '
            f'people who get stuck at this step just don\'t know what to '
            f'ask first.</p>'
            f'{video_line_html}'
            f'<p>Simplest first prompt that always works:</p>'
            f'<pre style="background:#f5f5f5;padding:12px;border-radius:6px;'
            f'font-family:monospace;font-size:13px">Scan my repo for '
            f'security issues and open a PR to fix the top one.</pre>'
            f'<p>Paste that in and ORA does the rest.</p>'
            f'<p style="margin:24px 0"><a href="{PUBLIC_BASE}/chat" '
            f'style="background:#eab308;color:#000;padding:12px 24px;'
            f'text-decoration:none;border-radius:8px;font-weight:600;'
            f'display:inline-block">Open ORA chat</a></p>'
            f'<p>{_html.escape(SIGNOFF)}</p>'
            f'{_footer_html(email)}</div>'
        )

    else:  # stage 7
        pro_exp = user.get("pro_expires_at")
        days_left = None
        if hasattr(pro_exp, "timestamp"):
            days_left = round((pro_exp.timestamp() - time.time()) / 86400)
        elif isinstance(pro_exp, (int, float)):
            days_left = round((pro_exp - time.time()) / 86400)
        if days_left is not None and days_left > 0:
            subject = f"Your ORA Pro window closes in {days_left} days"
            headline = (f"Your First-50 Pro window ends in {days_left} days. "
                        "After that your account keeps working — just on the "
                        "free tier (10 tasks/mo instead of 300).")
        else:
            headline = ("This is the last automated email from me on this "
                        "sequence. No pressure — hit reply anytime if there\'s "
                        "something ORA could do that would actually be useful.")
        text = (
            f"Hey {name},\n\n"
            f"{headline}\n\n"
            "If there's a reason ORA didn't click for you — the setup, the "
            "output, the pricing, anything — hit reply. Real founder, real "
            "inbox.\n\n"
            f"{PUBLIC_BASE}\n\n"
            f"{SIGNOFF}\n\n"
            "P.S. This is the last email in this sequence."
        )
        html = (
            f'<div style="font-family:-apple-system,sans-serif;max-width:560px;'
            f'margin:0 auto;padding:32px 24px;color:#111">'
            f'<p>Hey {_html.escape(name)},</p>'
            f'<p>{_html.escape(headline)}</p>'
            f'<p>If there\'s a reason ORA didn\'t click for you — the setup, '
            f'the output, the pricing, anything — hit reply. Real founder, '
            f'real inbox.</p>'
            f'<p>{_html.escape(SIGNOFF)}</p>'
            f'<p style="color:#888;font-size:12px"><i>P.S. This is the last '
            f'email in this sequence.</i></p>'
            f'{_footer_html(email)}</div>'
        )

    return subject, text + _footer_text(email), html


# ── DB helpers ───────────────────────────────────────────────────────
async def is_unsubscribed(db, email: str) -> bool:
    doc = await db.email_unsubscribes.find_one(
        {"email": (email or "").lower().strip()}, {"_id": 1},
    )
    return doc is not None


async def already_sent(db, user_id: str, stage: int) -> bool:
    doc = await db.first50_campaign_state.find_one(
        {"user_id": user_id, "stage_sent": stage}, {"_id": 1},
    )
    return doc is not None


async def has_created_task(db, user_id: str) -> bool:
    n = await db.cto_tasks.count_documents({"user_id": user_id})
    return n > 0


async def record_send(db, user_id: str, email: str, stage: int,
                       ok: bool, error: Optional[str],
                       message_id: Optional[str], dry_run: bool) -> None:
    entry = {"stage": stage, "ts": _now(), "ok": ok,
             "error": error, "message_id": message_id, "dry_run": dry_run}
    await db.first50_campaign_state.update_one(
        {"user_id": user_id},
        {"$setOnInsert": {"user_id": user_id, "email": email,
                          "created_at": _now()},
         "$addToSet":    {"stage_sent": stage},
         "$push":        {"sent_at": entry},
         "$set":         {"updated_at": _now()}},
        upsert=True,
    )


# ── Eligibility ──────────────────────────────────────────────────────
async def eligible_for_stage(db, stage: int, *, limit: int = 500) -> list[dict]:
    """Return users eligible for the given stage. Applies all stop-conditions."""
    cutoff_hours_ago = STAGE_HOURS[stage]
    cutoff_ts = time.time() - cutoff_hours_ago * 3600

    if stage == 0:
        # Unverified users only
        cursor = db.dev_users.find(
            {"email_verified": {"$ne": True},
             "email": {"$exists": True, "$ne": ""}},
            {"_id": 0, "user_id": 1, "email": 1, "name": 1,
             "created_at": 1, "email_verified": 1,
             "promo_first50_claimed": 1, "pro_expires_at": 1}
        )
    else:
        # Stage 3/7: signup was ≥N hours ago, must NOT have created a task yet
        cursor = db.dev_users.find(
            {"created_at": {"$lte": cutoff_ts},
             "email": {"$exists": True, "$ne": ""}},
            {"_id": 0, "user_id": 1, "email": 1, "name": 1,
             "created_at": 1, "email_verified": 1,
             "promo_first50_claimed": 1, "pro_expires_at": 1}
        )

    out: list[dict] = []
    async for u in cursor:
        u["_will_send"] = True
        u["_skip_reason"] = None
        if await is_unsubscribed(db, u["email"]):
            u["_will_send"] = False; u["_skip_reason"] = "unsubscribed"
        elif await already_sent(db, u["user_id"], stage):
            u["_will_send"] = False; u["_skip_reason"] = "already_sent_this_stage"
        elif stage in (3, 7) and await has_created_task(db, u["user_id"]):
            u["_will_send"] = False; u["_skip_reason"] = "task_created"
        out.append(u)
        if len(out) >= limit:
            break
    return out


# ── Send ─────────────────────────────────────────────────────────────
async def _resend_send(to_email: str, *, subject: str,
                        text: str, html: str) -> tuple[bool, Optional[str], Optional[str]]:
    """Returns (ok, error, message_id)."""
    key = os.environ.get("RESEND_API_KEY") or ""
    if not key:
        return False, "RESEND_API_KEY not configured", None
    sender = os.environ.get("RESEND_FROM_EMAIL") or "AUREM <ora@aurem.live>"
    try:
        from services.http import ext_request, ExternalCallError
        try:
            r = await ext_request(
                "resend", "POST", "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={"from": sender, "to": [to_email],
                      "subject": subject, "text": text, "html": html},
                raise_for_status=False,
            )
            if r.status_code in (200, 201, 202):
                try:
                    return True, None, r.json().get("id")
                except Exception:
                    return True, None, None
            return False, f"HTTP {r.status_code}: {r.text[:200]}", None
        except ExternalCallError as e:
            return False, f"{e.dep}: {e}", None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", None


async def send_one(user: dict, stage: int, *,
                    dry_run: bool = True,
                    promo_remaining: Optional[int] = None) -> dict:
    """Send stage-N to a single user. Idempotent via first50_campaign_state."""
    db = get_db()
    email = (user.get("email") or "").strip()
    user_id = user.get("user_id") or ""
    if not email or not user_id:
        return {"ok": False, "skip_reason": "no_email_or_user_id"}
    if await is_unsubscribed(db, email):
        return {"ok": False, "skip_reason": "unsubscribed"}
    if await already_sent(db, user_id, stage):
        return {"ok": False, "skip_reason": "already_sent_this_stage"}
    if stage in (3, 7) and await has_created_task(db, user_id):
        return {"ok": False, "skip_reason": "task_created"}

    subject, text, html = render_stage(stage, user, promo_remaining=promo_remaining)
    if dry_run:
        return {"ok": True, "dry_run": True, "subject": subject,
                "text_len": len(text), "html_len": len(html)}
    ok, err, mid = await _resend_send(email, subject=subject, text=text, html=html)
    await record_send(db, user_id, email, stage, ok, err, mid, dry_run=False)
    return {"ok": ok, "error": err, "message_id": mid, "subject": subject}
