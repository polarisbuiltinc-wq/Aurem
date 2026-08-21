"""
services/payment_recovery_email.py — 2026-08-22

Customer-facing "your card failed" email. Founder ask: the founder
already gets an alert (services/founder_alerts.py, guard=
"stripe_dunning"), but the CUSTOMER themselves must also know their
own card failed — not just find out when the subscription eventually
lapses. Fires from routers/payments.py's `invoice.payment_failed`
webhook branch.

Same Resend shipping pattern as services/welcome_email.py (shared
codebase convention): plain-text + dark-themed HTML, reserved
@example.* domains short-circuited for tests, one audit row per send
in its own collection.

Dedup key is the Stripe INVOICE id, not the user — Stripe fires this
event once per retry attempt on the SAME invoice during a dunning
cycle, and we only want ONE "your card failed" email per invoice
(Stripe's own account-level retry emails, if enabled, cover the
reminder cadence — this is our one-time heads-up + direct link to fix
it in-app).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SUBJECT = "Your AUREM payment didn't go through"
SIGNOFF = "— Tejinder Sandhu, Founder, Aurem"


def _first_name(user: dict) -> str:
    name = (user.get("name") or "").strip()
    if name:
        return name.split()[0]
    email = (user.get("email") or "").strip()
    return (email.split("@", 1)[0] or "there").split(".", 1)[0]


def render_text(
    user: dict, *, plan: str, amount_due: float, portal_url: str,
    next_attempt_at: Optional[datetime] = None,
) -> str:
    first = _first_name(user)
    return (
        f"Hey {first},\n"
        "\n"
        f"We just tried to charge your card for your AUREM {plan} plan "
        f"(${amount_due:.2f}) and it didn't go through.\n"
        "\n"
        f"Your subscription is still active for now. {_grace_period_line(next_attempt_at)} "
        "To avoid any interruption, update your card here:\n"
        "\n"
        f"  {portal_url}\n"
        "\n"
        "If this was expected (e.g. you meant to cancel), you can do "
        "that from the same link.\n"
        "\n"
        f"{SIGNOFF}\n"
    )


def _grace_period_line(next_attempt_at: Optional[datetime]) -> str:
    """2026-08-22 — founder ask: a clear, EXACT deadline (not a vague
    'couple of weeks') motivates faster action. We deliberately only
    ever quote the real `next_payment_attempt` Stripe gives us for
    THIS invoice — that's the one number we can state with 100%
    certainty. We don't invent a "total dunning window ends on X"
    figure, since the account's exact Smart-Retries schedule isn't
    something we can verify via API."""
    if next_attempt_at is None:
        return (
            "This was Stripe's last scheduled automatic retry, so "
            "there's no more time to wait —"
        )
    days = max((next_attempt_at.date() - datetime.now(timezone.utc).date()).days, 0)
    when = next_attempt_at.strftime("%B %-d")
    if days <= 0:
        return f"Stripe will automatically try again later today ({when}) —"
    day_word = "day" if days == 1 else "days"
    return f"Stripe will automatically try again in {days} {day_word} (on {when}) —"


def render_html(
    user: dict, *, plan: str, amount_due: float, portal_url: str,
    next_attempt_at: Optional[datetime] = None,
) -> str:
    first = _first_name(user)
    grace_line = _grace_period_line(next_attempt_at)
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#0b0b0b;color:#e8e8e8;
font-family:'Helvetica Neue',Arial,sans-serif;line-height:1.6;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"
         width="100%" style="background:#0b0b0b;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             width="560" style="max-width:560px;background:#141414;
                                 border:1px solid rgba(239,68,68,0.25);
                                 border-radius:12px;padding:32px;">
        <tr><td style="color:#e8e8e8;font-size:15px;">
          <div style="font-size:20px;font-weight:700;margin-bottom:8px;
                       color:#fca5a5;">
            Hey {first} — your payment didn&rsquo;t go through.
          </div>
          <div style="color:#c8c8c8;">
            We just tried to charge your card for your AUREM
            <b>{plan}</b> plan (<b>${amount_due:.2f}</b>) and it
            didn&rsquo;t go through.
          </div>
          <div data-testid="payment-recovery-email-grace-line"
               style="color:#fbbf24;margin-top:12px;font-weight:600;">
            {grace_line}
          </div>
          <div style="color:#c8c8c8;margin-top:4px;">
            to avoid any interruption, update your card below.
          </div>

          <div style="text-align:center;margin:28px 0 8px;">
            <a href="{portal_url}"
               style="display:inline-block;padding:14px 28px;
                      background:#eab308;color:#0b0b0b;
                      text-decoration:none;font-weight:600;
                      border-radius:8px;font-size:15px;"
               data-testid="payment-recovery-email-cta">
              Update your card &rarr;
            </a>
          </div>
          <div style="text-align:center;color:#888;font-size:13px;
                       margin-bottom:24px;">
            Same link also lets you cancel, if that was the plan.
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


async def _resend_send(to_email: str, *, subject: str, text: str, html: str) -> tuple[bool, Optional[str]]:
    """POST to Resend. Returns (ok, error_text). Short-circuits reserved
    @example.com/.org/.net domains so the test suite never fires real
    API calls (same guard as welcome_email.py)."""
    key = os.environ.get("RESEND_API_KEY") or ""
    if not key:
        return False, "RESEND_API_KEY not configured"
    _lower = (to_email or "").lower()
    if _lower.endswith("@example.com") or _lower.endswith("@example.org") \
            or _lower.endswith("@example.net"):
        return True, None
    sender = os.environ.get("RESEND_FROM_EMAIL") or "AUREM <ora@aurem.live>"
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
                    "subject": subject,
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


async def _already_sent(db, invoice_id: str) -> bool:
    """Dedup per Stripe INVOICE id — one email per invoice, even if
    Stripe retries the SAME invoice multiple times during a dunning
    cycle."""
    doc = await db.payment_recovery_emails.find_one(
        {"invoice_id": invoice_id}, {"_id": 1},
    )
    return doc is not None


async def _record_send(
    db, user_id: str, email: str, invoice_id: str, sent_ok: bool,
    error: Optional[str],
) -> None:
    try:
        await db.payment_recovery_emails.insert_one({
            "user_id":    user_id,
            "email":      email,
            "invoice_id": invoice_id,
            "sent_at":    datetime.now(timezone.utc),
            "sent_ok":    bool(sent_ok),
            "error":      error,
        })
    except Exception as e:                              # noqa: BLE001
        logger.warning("payment_recovery_emails insert failed: %r", e)


async def send_payment_recovery_email(
    db, user: dict, *, invoice_id: str, plan: str, amount_due: float,
    portal_url: str, next_attempt_at: Optional[datetime] = None,
) -> dict:
    """Send the "your card failed" email to the CUSTOMER. Idempotent
    per Stripe invoice id via `_already_sent`. Non-raising by design
    so the webhook handler can fire this without risking a 500 that
    would make Stripe retry the whole webhook delivery."""
    email = (user.get("email") or "").strip()
    user_id = user.get("user_id")
    if not (email and user_id and invoice_id):
        return {"ok": False, "error": "missing user identity or invoice id"}

    if await _already_sent(db, invoice_id):
        return {"ok": True, "skipped": "already_sent"}

    text = render_text(user, plan=plan, amount_due=amount_due, portal_url=portal_url,
                        next_attempt_at=next_attempt_at)
    html = render_html(user, plan=plan, amount_due=amount_due, portal_url=portal_url,
                        next_attempt_at=next_attempt_at)
    sent_ok, err = await _resend_send(email, subject=SUBJECT, text=text, html=html)
    await _record_send(db, user_id, email, invoice_id, sent_ok, err)
    if not sent_ok:
        logger.warning("payment recovery email send failed uid=%s err=%s", user_id, err)
    return {"ok": bool(sent_ok), "error": err}


# ---------------------------------------------------------------------------
# 2026-08-22 — Recovery CONFIRMATION email ("you're all set"). Founder
# ask: close the loop once a previously-failed subscription's card
# succeeds again, so the customer isn't left wondering. Separate
# collection (`payment_recovered_emails`) from the failure email's
# dedup table — the invoice that finally succeeds is very often the
# SAME Stripe invoice id that earlier failed, so it can't share a
# unique index with the failure-email dedup table.
# ---------------------------------------------------------------------------

RECOVERED_SUBJECT = "You're all set — your AUREM payment went through"


def render_recovered_text(user: dict, *, plan: str) -> str:
    first = _first_name(user)
    return (
        f"Hey {first},\n"
        "\n"
        f"Good news — your card went through and your AUREM {plan} "
        "plan is fully active again. No further action needed.\n"
        "\n"
        f"{SIGNOFF}\n"
    )


def render_recovered_html(user: dict, *, plan: str) -> str:
    first = _first_name(user)
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#0b0b0b;color:#e8e8e8;
font-family:'Helvetica Neue',Arial,sans-serif;line-height:1.6;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"
         width="100%" style="background:#0b0b0b;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             width="560" style="max-width:560px;background:#141414;
                                 border:1px solid rgba(34,197,94,0.25);
                                 border-radius:12px;padding:32px;">
        <tr><td style="color:#e8e8e8;font-size:15px;">
          <div style="font-size:20px;font-weight:700;margin-bottom:8px;
                       color:#86efac;">
            You&rsquo;re all set, {first}.
          </div>
          <div style="color:#c8c8c8;">
            Your card went through and your AUREM <b>{plan}</b> plan
            is fully active again. No further action needed.
          </div>
          <div style="border-top:1px solid rgba(255,255,255,0.06);
                       padding-top:16px;margin-top:20px;color:#aaa;
                       font-size:13px;">
            {SIGNOFF}
          </div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


async def _already_sent_recovered(db, invoice_id: str) -> bool:
    doc = await db.payment_recovered_emails.find_one(
        {"invoice_id": invoice_id}, {"_id": 1},
    )
    return doc is not None


async def _record_recovered_send(
    db, user_id: str, email: str, invoice_id: str, sent_ok: bool,
    error: Optional[str],
) -> None:
    try:
        await db.payment_recovered_emails.insert_one({
            "user_id":    user_id,
            "email":      email,
            "invoice_id": invoice_id,
            "sent_at":    datetime.now(timezone.utc),
            "sent_ok":    bool(sent_ok),
            "error":      error,
        })
    except Exception as e:                              # noqa: BLE001
        logger.warning("payment_recovered_emails insert failed: %r", e)


async def send_payment_recovered_email(
    db, user: dict, *, invoice_id: str, plan: str,
) -> dict:
    """Send the "you're all set" confirmation to the CUSTOMER once a
    previously-failed subscription's card succeeds again. Only call
    this when the subscription was ACTUALLY in `payment_failed` state
    beforehand (caller's job — see routers/payments.py's
    `invoice.paid` branch) so a normal first-try renewal never gets
    a pointless "your payment recovered" email."""
    email = (user.get("email") or "").strip()
    user_id = user.get("user_id")
    if not (email and user_id and invoice_id):
        return {"ok": False, "error": "missing user identity or invoice id"}

    if await _already_sent_recovered(db, invoice_id):
        return {"ok": True, "skipped": "already_sent"}

    text = render_recovered_text(user, plan=plan)
    html = render_recovered_html(user, plan=plan)
    sent_ok, err = await _resend_send(email, subject=RECOVERED_SUBJECT, text=text, html=html)
    await _record_recovered_send(db, user_id, email, invoice_id, sent_ok, err)
    if not sent_ok:
        logger.warning("payment recovered email send failed uid=%s err=%s", user_id, err)
    return {"ok": bool(sent_ok), "error": err}

