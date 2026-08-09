"""
services/verification_email.py — Track 3 (item #31)

Sends single-use email-verification link to fresh signups.
Reuses the existing Resend integration (same pattern as
`services/onboarding_email.py`). Token is a random UUID stored in
Mongo (`email_verifications` collection) with a 24h expiry — NOT a
JWT — because the founder's requirements call for single-use
enforcement + audit-trail on click, both trivially done with a
Mongo row and a `used_at` timestamp.

Public surface:
    create_verification_token(db, user_id, email)  → str token
    send_verification_email(user, token)           → dict result
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────
CAMPAIGN         = "email_verification"
TOKEN_TTL_HOURS  = 24
PUBLIC_BASE      = os.environ.get(
    "PUBLIC_APP_URL", "https://auremcto.com",
).rstrip("/")
SUBJECT          = "Verify your Aurem email — 60 seconds"
SIGNOFF          = "— Tejinder Sandhu, Founder, Aurem"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _first_name(user: dict) -> str:
    name = (user.get("name") or "").strip()
    if name:
        return name.split()[0]
    email = (user.get("email") or "").strip()
    return (email.split("@", 1)[0] or "there").split(".", 1)[0]


def verify_url(token: str) -> str:
    """Public verification link — points at the backend GET endpoint
    which marks the token used and 302-redirects to the frontend
    `/verify` success page. GET is deliberate: email clients only
    follow anchor hrefs on click."""
    return f"{PUBLIC_BASE}/api/aurem-dev/auth/verify?token={quote(token)}"


async def create_verification_token(db, user_id: str, email: str) -> str:
    """Insert a fresh single-use token row. Returns the token string.

    Any previously-unused token for the same user is invalidated so a
    resend can't leave two live tokens in the wild."""
    now = _now()
    # Invalidate older unused tokens for the same user so only ONE is
    # ever live at a time.
    try:
        await db.email_verifications.update_many(
            {"user_id": user_id, "used_at": None},
            {"$set": {"invalidated_at": now}},
        )
    except Exception as e:                    # noqa: BLE001
        logger.warning("email_verifications invalidate failed: %r", e)

    token = uuid.uuid4().hex
    await db.email_verifications.insert_one({
        "token":       token,
        "user_id":     user_id,
        "email":       email,
        "created_at":  now,
        "expires_at":  now + timedelta(hours=TOKEN_TTL_HOURS),
        "used_at":     None,
        "invalidated_at": None,
    })
    return token


def render_text(user: dict, token: str) -> str:
    first = _first_name(user)
    link = verify_url(token)
    return (
        f"Hey {first},\n"
        "\n"
        "One quick step to activate your Aurem account: verify your email.\n"
        "\n"
        f"Verify → {link}\n"
        "\n"
        f"This link expires in {TOKEN_TTL_HOURS} hours. If you didn't sign\n"
        "up for Aurem, you can ignore this email.\n"
        "\n"
        f"{SIGNOFF}\n"
    )


def render_html(user: dict, token: str) -> str:
    first = _first_name(user)
    link = verify_url(token)
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#0b0b0b;color:#e8e8e8;
font-family:'Helvetica Neue',Arial,sans-serif;line-height:1.55;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"
         width="100%" style="background:#0b0b0b;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             width="560" style="max-width:560px;background:#141414;
                                 border:1px solid rgba(234,179,8,0.18);
                                 border-radius:12px;padding:32px;">
        <tr><td style="color:#e8e8e8;font-size:15px;">
          Hey {first},<br><br>
          One quick step to activate your Aurem account: verify your email.<br><br>
          <a href="{link}"
             style="display:inline-block;padding:12px 22px;background:#eab308;
                    color:#0b0b0b;text-decoration:none;font-weight:600;
                    border-radius:8px;font-size:14px;"
             data-testid="verify-cta">
            Verify my email &rarr;
          </a><br><br>
          <span style="color:#888;font-size:13px;">
            This link expires in {TOKEN_TTL_HOURS} hours. If you didn't sign
            up for Aurem, you can ignore this email.
          </span><br><br>
          <span style="color:#aaa;font-size:13px;">{SIGNOFF}</span>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


async def _resend_send(to_email: str, *, text: str, html: str) -> tuple[bool, Optional[str]]:
    """POST to Resend. Returns (ok, error_text)."""
    key = os.environ.get("RESEND_API_KEY") or ""
    if not key:
        return False, "RESEND_API_KEY not configured"
    # Test-mode short-circuit: never call Resend for the well-known
    # reserved `@example.com` / `@example.org` / `@example.net`
    # domains. Keeps the pytest suite from firing real API calls
    # while still exercising the full DB + router path.
    _lower = (to_email or "").lower()
    if _lower.endswith("@example.com") or _lower.endswith("@example.org") \
            or _lower.endswith("@example.net"):
        return True, None
    sender = (
        os.environ.get("RESEND_FROM_EMAIL")
        or "AUREM <ora@aurem.live>"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type":   "application/json"},
                json={
                    "from":    sender,
                    "to":      [to_email],
                    "subject": SUBJECT,
                    "text":    text,
                    "html":    html,
                },
            )
            if r.status_code in (200, 201, 202):
                return True, None
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:                              # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


async def _record_send(
    db, user_id: str, email: str, sent_ok: bool, error: Optional[str],
) -> None:
    """Insert one audit row per send attempt into `onboarding_emails`
    (same collection used by the existing t24/t72 nudge — one place
    for the admin panel to render). `campaign` distinguishes rows."""
    try:
        await db.onboarding_emails.insert_one({
            "user_id":    user_id,
            "email":      email,
            "campaign":   CAMPAIGN,
            "stage":      "verify",
            "sent_at":    _now(),
            "sent_ok":    bool(sent_ok),
            "error":      error,
            "dry_run":    False,
            "clicked_at": None,
            "click_count": 0,
        })
    except Exception as e:                              # noqa: BLE001
        logger.warning("onboarding_emails (verify) insert failed: %r", e)


async def send_verification_email(
    db, user: dict, *, token: Optional[str] = None,
) -> dict:
    """Send the verification email for `user`. If `token` is not
    passed the caller expects us to mint one. Returns
    `{ok, error, token}` — safe to await from a background task.

    NEVER raises: signup UX must not crash on Resend failure. The
    audit row + a resend endpoint (future) let the user retry."""
    email = (user.get("email") or "").strip()
    user_id = user.get("user_id")
    if not (email and user_id):
        return {"ok": False, "error": "missing user identity"}
    if not token:
        try:
            token = await create_verification_token(db, user_id, email)
        except Exception as e:                          # noqa: BLE001
            return {"ok": False, "error": f"token mint failed: {e!r}"}

    text = render_text(user, token)
    html = render_html(user, token)
    sent_ok, err = await _resend_send(email, text=text, html=html)
    await _record_send(db, user_id, email, sent_ok, err)
    if not sent_ok:
        logger.warning("verification email send failed uid=%s err=%s",
                       user_id, err)
    return {"ok": bool(sent_ok), "error": err, "token": token}
