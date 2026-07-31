"""
Session 5 · Item 5 live-verify · Regression guard for founder_alerts
Cloudflare-1010 bug.

BUG (2026-07-31, discovered during Resend key rotation live-verify):
`services/founder_alerts._send_via_resend` was posting to
`https://api.resend.com/emails` via `urllib.request.urlopen`. urllib's
default `User-Agent: Python-urllib/3.11` header is on Cloudflare's bot
blocklist, so ALL G10 founder alerts were silently returning HTTP 403
(CF error 1010) in production. The `_send_via_resend` caller swallowed
the HTTPError and just logged a warning — the outage was invisible.

FIX: send a named `User-Agent: AUREM-Guardian/1.0 (+https://aurem.live)`
header on every request. Verified by three live sends from the founder
brief (Resend message IDs af7cfc11, 286306c4, 1011ed68).

This test is a STATIC assertion — it reads the source and asserts the
UA header is set. That way, any future refactor that drops it is caught
before it silently kills founder-alert delivery again.
"""
from __future__ import annotations

import inspect

from services import founder_alerts


def test_send_via_resend_sends_named_user_agent_header():
    """`_send_via_resend` MUST send a non-default User-Agent header.
    Cloudflare returns 403 error 1010 for `Python-urllib/*` UAs and
    that silent failure took down every G10 alert prior to this fix."""
    src = inspect.getsource(founder_alerts._send_via_resend)
    assert "User-Agent" in src, (
        "founder_alerts._send_via_resend MUST set a User-Agent header — "
        "urllib's default UA is Cloudflare-blocked (error 1010) on "
        "api.resend.com. Do NOT remove this header."
    )
    # Belt-and-suspenders: ensure it's a plausibly branded UA, not just
    # a placeholder that an over-eager cleanup could delete as unused.
    assert "AUREM" in src or "Guardian" in src, (
        "The named UA must identify AUREM/Guardian so Cloudflare's "
        "bot filter allows it through."
    )


def test_send_via_resend_still_posts_json_to_correct_endpoint():
    """Basic shape guard so the fix didn't accidentally break the send."""
    src = inspect.getsource(founder_alerts._send_via_resend)
    assert "api.resend.com/emails" in src
    assert "Authorization" in src
    assert "application/json" in src


def test_g10_config_uses_verified_aurem_live_domain():
    """The default `FOUNDER_ALERT_FROM` used to point at
    `alerts@auremcto.com` which is `not_started` in Resend. The .env
    now pins the sender to `alerts@aurem.live` which IS verified.
    Live check that the env value uses aurem.live (not the unverified
    domain) so future .env rewrites don't silently degrade delivery."""
    import os
    from dotenv import dotenv_values
    env = dotenv_values("/app/backend/.env")
    from_ = env.get("FOUNDER_ALERT_FROM") or os.environ.get("FOUNDER_ALERT_FROM", "")
    assert "aurem.live" in from_, (
        f"FOUNDER_ALERT_FROM must use the verified aurem.live domain, "
        f"got {from_!r}. auremcto.com is not_started in Resend and "
        f"sends would 403."
    )
