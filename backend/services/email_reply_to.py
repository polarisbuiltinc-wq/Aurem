"""
services/email_reply_to.py — Central source for the Reply-To header
on every outbound Resend email.

Why: transactional emails go out as `From: AUREM <ora@aurem.live>`.
If a recipient hits Reply in Gmail the message hits aurem.live's MX
(Cloudflare Email Routing). If that routing rule is broken/absent the
reply bounces. Setting an explicit `Reply-To:` header bypasses the
`From:` domain entirely — Gmail sends the reply straight to the
inbox named here.

Configuration: `REPLY_TO_EMAIL` env var. If unset, no Reply-To
header is added (safe fallback to legacy behavior).
"""
from __future__ import annotations

import os
from typing import Optional


def get_reply_to() -> Optional[str]:
    """Return the configured Reply-To address, or None if unset.

    Callers should conditionally include the header:
        payload = {"from": ..., "to": [...], "subject": ..., ...}
        rt = get_reply_to()
        if rt:
            payload["reply_to"] = rt
    """
    v = (os.environ.get("REPLY_TO_EMAIL") or "").strip()
    return v or None
