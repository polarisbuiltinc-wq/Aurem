"""
services/password_reset_email.py — 2026-08-19

Sends the "reset your password" link. Mirrors the Resend pattern in
services/onboarding_email.py (same provider, same shared HTTP wrapper).
Best-effort: a failed send must never raise into the request path that
triggered it (forgot-password always returns a generic 200).
"""
from __future__ import annotations
import logging
import os

logger = logging.getLogger(__name__)

_SUBJECT = "Reset your AUREM password"


def _reset_url(token: str) -> str:
    base = os.environ.get("FRONTEND_URL") or "https://auremcto.com"
    return f"{base.rstrip('/')}/reset-password?token={token}"


def _render_text(url: str) -> str:
    return (
        "Someone requested a password reset for your AUREM account.\n\n"
        f"Reset your password: {url}\n\n"
        "This link expires in 1 hour. If you didn't request this, you can "
        "safely ignore this email — your password will not change."
    )


def _render_html(url: str) -> str:
    return (
        "<p>Someone requested a password reset for your AUREM account.</p>"
        f'<p><a href="{url}">Reset your password</a></p>'
        "<p style=\"color:#888;font-size:12px\">This link expires in 1 hour. "
        "If you didn't request this, you can safely ignore this email — "
        "your password will not change.</p>"
    )


async def send_reset_email(to_email: str, token: str) -> tuple[bool, str | None]:
    key = os.environ.get("RESEND_API_KEY") or ""
    if not key:
        logger.warning("send_reset_email: RESEND_API_KEY not configured")
        return False, "RESEND_API_KEY not configured"
    sender = os.environ.get("RESEND_FROM_EMAIL") or "AUREM <ora@aurem.live>"
    url = _reset_url(token)
    try:
        from services.http import ext_request, ExternalCallError
        try:
            r = await ext_request(
                "resend", "POST",
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type":   "application/json"},
                json={
                    "from":    sender,
                    "to":      [to_email],
                    "subject": _SUBJECT,
                    "text":    _render_text(url),
                    "html":    _render_html(url),
                },
                raise_for_status=False,
            )
            if r.status_code in (200, 201, 202):
                return True, None
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except ExternalCallError as e:
            return False, f"{e.dep}: {e}"
    except Exception as e:                                    # noqa: BLE001
        logger.warning("send_reset_email failed: %r", e)
        return False, f"{type(e).__name__}: {e}"
