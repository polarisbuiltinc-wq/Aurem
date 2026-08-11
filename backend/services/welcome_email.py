"""
services/welcome_email.py — Track 3 (item #33) · Post-verification welcome.

Fires ONCE per user, immediately on verification-click. Reuses the
existing Resend integration (same shipping pattern as
`services/onboarding_email.py` and `services/verification_email.py`)
plus the same `onboarding_emails` audit collection so the admin
panel renders every campaign uniformly.

Trigger point: `routers/promo_first50.py::verify_email` — after
`email_verified` is flipped to True, fires this as a safe_bg task.
Founders never see it (they auto-verify at signup; there is no
click event to hang the campaign off).

Founder-locked content contract (Session 4 close):
  - Security-first messaging (Vanguard scan, Citation Guard, verify-
    gate) in plain language.
  - 60-90s demo video thumbnail linking to a hosted URL
    (Loom/YouTube). NO iframe embed — email clients strip it. URL
    comes from env `WELCOME_DEMO_VIDEO_URL`; when unset we render
    the CTA without the video block (graceful degrade).
  - Scannable features list (Plan/Execute/Verify/Scan/Ship, Ask
    Advisor, Rollback).
  - Single "Connect your first project" CTA.
  - Mobile-responsive HTML (inline styles only).
  - NO scarcity language — spot counter belongs on the landing page.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────
CAMPAIGN         = "signup_welcome"
PUBLIC_BASE      = os.environ.get(
    "PUBLIC_APP_URL", "https://auremcto.com",
).rstrip("/")
DEMO_VIDEO_URL   = (os.environ.get("WELCOME_DEMO_VIDEO_URL") or "").strip()
# Optional custom thumbnail. Falls back to a generic ORA-branded
# thumbnail served from the frontend if unset.
DEMO_VIDEO_THUMB = (
    os.environ.get("WELCOME_DEMO_VIDEO_THUMB")
    or f"{PUBLIC_BASE}/ora-icon.png"
)
SUBJECT          = "You're in — here's what ORA can do next"
SIGNOFF          = "— Tejinder Sandhu, Founder, Aurem"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _first_name(user: dict) -> str:
    name = (user.get("name") or "").strip()
    if name:
        return name.split()[0]
    email = (user.get("email") or "").strip()
    return (email.split("@", 1)[0] or "there").split(".", 1)[0]


def cta_url(user_id: str) -> str:
    """Tracked CTA URL — reuses the existing `/onboarding/click`
    endpoint (campaign-agnostic click logger + redirect to the
    connect-repo wizard)."""
    return (
        f"{PUBLIC_BASE}/api/aurem-dev/onboarding/click"
        f"?uid={quote(user_id)}&c={CAMPAIGN}"
    )


def render_text(user: dict) -> str:
    first = _first_name(user)
    cta = cta_url(user.get("user_id", ""))
    video_line = (
        f"\nWatch a 60-second demo → {DEMO_VIDEO_URL}\n"
        if DEMO_VIDEO_URL else ""
    )
    return (
        f"Hey {first},\n"
        "\n"
        "Your email is verified. Aurem is ready to write, verify, and\n"
        "ship code to your GitHub repo.\n"
        "\n"
        "Why teams trust ORA with a repo on day one:\n"
        "  - Vanguard scan runs on every commit — 25 security patterns\n"
        "    (secrets, injection, XSS, JWT replay) blocked before push.\n"
        "  - Citation Guard flags any code ORA writes without a source\n"
        "    reference — no invented APIs, no hallucinated file paths.\n"
        "  - Verify-gate: ruff + eslint + type checks must pass BEFORE\n"
        "    any ship. Nothing lands in your branch broken.\n"
        f"{video_line}"
        "\n"
        "What you get once a repo is connected:\n"
        "  - Plan / Execute / Verify / Scan / Ship — five-phase Loop\n"
        "  - Ask Advisor — architectural review without editing files\n"
        "  - One-click Rollback — revert-commit any ship, audit-safe\n"
        "\n"
        f"Connect your first project → {cta}\n"
        "\n"
        "Takes about 2 minutes.\n"
        "\n"
        f"{SIGNOFF}\n"
    )


def render_html(user: dict) -> str:
    first = _first_name(user)
    cta = cta_url(user.get("user_id", ""))
    video_block = ""
    if DEMO_VIDEO_URL:
        video_block = f"""
          <a href="{DEMO_VIDEO_URL}"
             style="display:block;margin:24px 0;text-decoration:none;
                    text-align:center;"
             data-testid="welcome-video-cta">
            <div style="position:relative;display:inline-block;
                        max-width:100%;border-radius:10px;overflow:hidden;
                        border:1px solid rgba(234,179,8,0.28);">
              <img src="{DEMO_VIDEO_THUMB}" alt="Watch ORA in 60 seconds"
                   width="480"
                   style="display:block;width:100%;max-width:480px;
                          height:auto;background:#1a1a1a;" />
              <div style="position:absolute;inset:0;display:flex;
                          align-items:center;justify-content:center;
                          background:rgba(0,0,0,0.35);">
                <span style="display:inline-block;padding:10px 18px;
                             background:#eab308;color:#0b0b0b;
                             border-radius:999px;font-weight:600;
                             font-size:13px;">
                  &#9654; 60-second demo
                </span>
              </div>
            </div>
          </a>
        """
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#0b0b0b;color:#e8e8e8;
font-family:'Helvetica Neue',Arial,sans-serif;line-height:1.6;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"
         width="100%" style="background:#0b0b0b;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             width="560" style="max-width:560px;background:#141414;
                                 border:1px solid rgba(234,179,8,0.18);
                                 border-radius:12px;padding:32px;">
        <tr><td style="color:#e8e8e8;font-size:15px;">
          <div style="font-size:20px;font-weight:700;margin-bottom:8px;">
            Hey {first} — you&rsquo;re in.
          </div>
          <div style="color:#c8c8c8;">
            Your email is verified. Aurem is ready to write, verify, and
            ship code to your GitHub repo.
          </div>

          <div style="margin:24px 0 12px;font-weight:600;color:#eab308;
                       font-size:13px;letter-spacing:.04em;
                       text-transform:uppercase;">
            Why teams trust ORA with a repo on day one
          </div>
          <ul style="padding-left:20px;margin:0;color:#d8d8d8;">
            <li style="margin-bottom:8px;">
              <b>Vanguard scan</b> runs on every commit — 25 security
              patterns (secrets, injection, XSS, JWT replay) blocked
              before push.
            </li>
            <li style="margin-bottom:8px;">
              <b>Citation Guard</b> flags any code ORA writes without a
              source reference — no invented APIs, no hallucinated
              file paths.
            </li>
            <li>
              <b>Verify-gate</b>: ruff + eslint + type checks must pass
              BEFORE any ship. Nothing lands in your branch broken.
            </li>
          </ul>

          {video_block}

          <div style="margin:24px 0 12px;font-weight:600;color:#eab308;
                       font-size:13px;letter-spacing:.04em;
                       text-transform:uppercase;">
            What you get once a repo is connected
          </div>
          <ul style="padding-left:20px;margin:0;color:#d8d8d8;">
            <li style="margin-bottom:8px;">
              <b>Plan / Execute / Verify / Scan / Ship</b> — five-phase
              Loop, you approve each step.
            </li>
            <li style="margin-bottom:8px;">
              <b>Ask Advisor</b> — architectural review without editing
              files.
            </li>
            <li>
              <b>One-click Rollback</b> — revert-commit any ship,
              audit-safe.
            </li>
          </ul>

          <div style="text-align:center;margin:28px 0 8px;">
            <a href="{cta}"
               style="display:inline-block;padding:14px 28px;background:#eab308;
                      color:#0b0b0b;text-decoration:none;font-weight:600;
                      border-radius:8px;font-size:15px;"
               data-testid="welcome-connect-repo-cta">
              Connect your first project &rarr;
            </a>
          </div>
          <div style="text-align:center;color:#888;font-size:13px;
                       margin-bottom:24px;">
            Takes about 2 minutes.
          </div>

          <div style="border-top:1px solid rgba(255,255,255,0.06);
                       padding-top:16px;color:#aaa;font-size:13px;">
            {SIGNOFF}
          </div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


async def _resend_send(to_email: str, *, text: str, html: str) -> tuple[bool, Optional[str]]:
    """POST to Resend. Returns (ok, error_text). Short-circuits reserved
    @example.com/.org/.net domains so the test suite never fires real
    API calls."""
    key = os.environ.get("RESEND_API_KEY") or ""
    if not key:
        return False, "RESEND_API_KEY not configured"
    _lower = (to_email or "").lower()
    if _lower.endswith("@example.com") or _lower.endswith("@example.org") \
            or _lower.endswith("@example.net"):
        return True, None
    sender = (
        os.environ.get("RESEND_FROM_EMAIL")
        or "AUREM <ora@aurem.live>"
    )
    try:
        # 2026-02-11 · Phase 1 hotspot dedup — shared HTTP wrapper.
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
                    "subject": SUBJECT,
                    "text":    text,
                    "html":    html,
                },
                raise_for_status=False,
            )
            if r.status_code in (200, 201, 202):
                return True, None
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except ExternalCallError as e:
            return False, f"{e.dep}: {e}"
    except Exception as e:                              # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


async def _already_sent(db, user_id: str) -> bool:
    """True if `user_id` already received the welcome. Prevents dupes
    if the verify endpoint is hit twice (defensive — verify itself is
    idempotent, but we still guard here so a hypothetical future
    caller can't spam the inbox)."""
    doc = await db.onboarding_emails.find_one(
        {"user_id": user_id, "campaign": CAMPAIGN},
        {"_id": 1},
    )
    return doc is not None


async def _record_send(
    db, user_id: str, email: str, sent_ok: bool, error: Optional[str],
) -> None:
    """Insert one audit row per send attempt. Same schema as the
    connect-repo nudge so the admin panel renders every campaign
    without knowing which module produced it."""
    try:
        await db.onboarding_emails.insert_one({
            "user_id":    user_id,
            "email":      email,
            "campaign":   CAMPAIGN,
            "stage":      "welcome",
            "sent_at":    _now(),
            "sent_ok":    bool(sent_ok),
            "error":      error,
            "dry_run":    False,
            "clicked_at": None,
            "click_count": 0,
        })
    except Exception as e:                              # noqa: BLE001
        logger.warning("onboarding_emails (welcome) insert failed: %r", e)


async def send_welcome_email(db, user: dict) -> dict:
    """Send the post-verification welcome email. Idempotent per user
    via `_already_sent`. Non-raising by design so the verify handler
    can fire this in a background task without risking the 302."""
    email = (user.get("email") or "").strip()
    user_id = user.get("user_id")
    if not (email and user_id):
        return {"ok": False, "error": "missing user identity"}

    if await _already_sent(db, user_id):
        return {"ok": True, "skipped": "already_sent"}

    text = render_text(user)
    html = render_html(user)
    sent_ok, err = await _resend_send(email, text=text, html=html)
    await _record_send(db, user_id, email, sent_ok, err)
    if not sent_ok:
        logger.warning("welcome email send failed uid=%s err=%s",
                       user_id, err)
    return {"ok": bool(sent_ok), "error": err}
