"""
services/support_email.py — Email notification when admin replies to a
support ticket.

The core failure mode this fixes: admin replies were being written to
`cto_support_messages` in Mongo but the user had ZERO surface where
the reply appeared (no polling, no badge, no email). Support was a
black hole.

This module sends a plain-text + HTML email via Resend whenever
`admin_reply()` runs. Email contains:
  · The admin's message inline (so the user can read without clicking)
  · A "View thread & reply" link to /support/thread/{ticket_id}?t=…&e=…
    (public, HMAC-token-verified — no login required, same pattern as
    the existing /support?t=…&e=… campaign-email link)

Reuses `support_token()` from first50_campaign so a single token
verifies both the composer link and the thread-view link.

Fire-and-forget: send failures are logged but never block the admin's
reply (the reply is still in Mongo and shows in the admin panel).
"""
from __future__ import annotations

import html as _html
import logging
import os
from typing import Optional
from urllib.parse import quote

from services.first50_campaign import support_token

logger = logging.getLogger(__name__)

PUBLIC_BASE = os.environ.get("PUBLIC_APP_URL", "https://auremcto.com").rstrip("/")
SIGNOFF = "— Tejinder Sandhu, Founder, Aurem"


def thread_url(ticket_id: str, email: str) -> str:
    """Public HMAC-verified thread-view URL — no login required."""
    return (f"{PUBLIC_BASE}/support/thread/{ticket_id}"
            f"?t={support_token(email)}&e={quote(email)}")


def _render(ticket_id: str, admin_message: str, user_email: str,
            user_name: Optional[str]) -> tuple[str, str, str]:
    """Return (subject, text, html)."""
    short = ticket_id.replace("tkt_", "")[:8]
    subject = f"Re: [Support #{short}] — Reply from Aurem"

    url = thread_url(ticket_id, user_email)
    greeting = f"Hi {user_name}," if user_name else "Hi,"

    text = (
        f"{greeting}\n\n"
        f"You have a new reply on your support ticket (#{short}):\n\n"
        f"─────────────────────────────────\n"
        f"{admin_message}\n"
        f"─────────────────────────────────\n\n"
        f"View the full conversation and reply here:\n"
        f"{url}\n\n"
        f"(No login required — the link is signed and personal to you.)\n\n"
        f"{SIGNOFF}\n"
    )

    safe_msg = _html.escape(admin_message).replace("\n", "<br>")
    safe_greet = _html.escape(greeting)
    html = (
        f'<div style="font-family:-apple-system,BlinkMacSystemFont,'
        f'\'Segoe UI\',Helvetica,Arial,sans-serif;max-width:560px;'
        f'margin:0 auto;padding:24px;color:#111;line-height:1.6">'
        f'<p style="margin:0 0 16px">{safe_greet}</p>'
        f'<p style="margin:0 0 16px">You have a new reply on your '
        f'support ticket <b>#{short}</b>:</p>'
        f'<div style="background:#f7f7f7;border-left:3px solid #eab308;'
        f'padding:16px 20px;margin:16px 0;border-radius:4px;'
        f'color:#333">{safe_msg}</div>'
        f'<p style="margin:24px 0"><a href="{_html.escape(url)}" '
        f'style="display:inline-block;background:#eab308;color:#000;'
        f'padding:12px 24px;border-radius:6px;text-decoration:none;'
        f'font-weight:600">View thread & reply</a></p>'
        f'<p style="margin:16px 0;font-size:12px;color:#666">'
        f'No login required — the link is signed and personal to you.</p>'
        f'<p style="margin:32px 0 0;color:#666;font-size:13px">'
        f'{_html.escape(SIGNOFF)}</p>'
        f'</div>'
    )
    return subject, text, html


async def send_reply_notification(
    *, user_email: str, user_name: Optional[str],
    ticket_id: str, admin_message: str,
) -> tuple[bool, Optional[str]]:
    """Send admin-reply notification email. Returns (ok, error_str).

    Fire-and-forget usage from admin_reply(): failures are logged and
    surfaced in the return tuple but must NEVER raise upstream — the
    reply itself is already durable in Mongo.
    """
    email = (user_email or "").strip()
    if not email:
        return False, "missing user_email"

    key = os.environ.get("RESEND_API_KEY") or ""
    if not key:
        logger.warning("support_email: RESEND_API_KEY not configured — "
                       "skipping notification for ticket %s", ticket_id)
        return False, "RESEND_API_KEY not configured"

    sender = os.environ.get("RESEND_FROM_EMAIL") or "AUREM <ora@aurem.live>"
    subject, text, html = _render(ticket_id, admin_message, email, user_name)

    try:
        from services.http import ext_request, ExternalCallError
        from services.email_reply_to import get_reply_to
        rt = get_reply_to()
        r = await ext_request(
            "resend", "POST", "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"from": sender, "to": [email],
                  "subject": subject, "text": text, "html": html,
                  **({"reply_to": rt} if rt else {})},
            raise_for_status=False,
        )
        if r.status_code in (200, 201, 202):
            logger.info("support_email: reply-notification sent "
                        "ticket=%s to=%s", ticket_id, email)
            return True, None
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except ExternalCallError as e:
        return False, f"{e.dep}: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
