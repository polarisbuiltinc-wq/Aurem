"""
test_iter388_reply_to_header.py — regression guard for the 2026-02-12
`Reply-To` header fix that resolved `ora@auremcto.com` (no MX) direct
reply bounces.

Every user-facing Resend `POST /emails` payload MUST carry a
`reply_to` field pointing to whatever `REPLY_TO_EMAIL` env var is
set to. This test monkey-patches `services.http.ext_request` and
asserts the field arrived in the JSON body.

If REPLY_TO_EMAIL is unset, the header must be OMITTED entirely
(not passed as empty string / None) — Resend accepts null but we
prefer not to noise the request.
"""
from __future__ import annotations

import os
import pytest


class _FakeResp:
    def __init__(self):
        self.status_code = 202
        self.text = ""

    def json(self):
        return {"id": "test-msg-id"}


@pytest.mark.asyncio
async def test_first50_campaign_send_includes_reply_to(monkeypatch):
    """`_resend_send` in services.first50_campaign must include a
    `reply_to` field when REPLY_TO_EMAIL is set."""
    monkeypatch.setenv("REPLY_TO_EMAIL", "test-reply@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "test-key")

    captured = {}

    async def fake_ext_request(*args, **kwargs):
        captured["json"] = kwargs.get("json") or {}
        return _FakeResp()

    # Patch the exact symbol the service imports from.
    monkeypatch.setattr("services.http.ext_request", fake_ext_request)

    from services.first50_campaign import _resend_send
    ok, err, mid = await _resend_send(
        "user@example.com",
        subject="hi", text="body", html="<p>body</p>",
    )
    assert ok is True, f"send should succeed, got err={err}"
    assert "reply_to" in captured["json"], (
        "outbound Resend payload is missing the reply_to header — "
        "campaign replies will bounce again"
    )
    assert captured["json"]["reply_to"] == "test-reply@example.com"


@pytest.mark.asyncio
async def test_reply_to_omitted_when_env_unset(monkeypatch):
    """When REPLY_TO_EMAIL is unset, the `reply_to` key must NOT
    appear in the payload (belt+braces: we don't want to send an
    empty string / null to Resend)."""
    monkeypatch.delenv("REPLY_TO_EMAIL", raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "test-key")

    captured = {}

    async def fake_ext_request(*args, **kwargs):
        captured["json"] = kwargs.get("json") or {}
        return _FakeResp()

    monkeypatch.setattr("services.http.ext_request", fake_ext_request)

    from services.first50_campaign import _resend_send
    await _resend_send(
        "user@example.com",
        subject="hi", text="body", html="<p>body</p>",
    )
    assert "reply_to" not in captured["json"], (
        "reply_to must be omitted when REPLY_TO_EMAIL is unset"
    )


def test_email_reply_to_helper_reads_env(monkeypatch):
    from services.email_reply_to import get_reply_to
    monkeypatch.setenv("REPLY_TO_EMAIL", "  foo@bar.com  ")
    assert get_reply_to() == "foo@bar.com"  # stripped
    monkeypatch.setenv("REPLY_TO_EMAIL", "")
    assert get_reply_to() is None
    monkeypatch.delenv("REPLY_TO_EMAIL", raising=False)
    assert get_reply_to() is None


def test_stale_ora_at_auremcto_not_used_in_userfacing_code():
    """Guard against re-introducing the no-MX `ora@auremcto.com`
    address in any user-facing surface (backend routers/services,
    frontend src). Test files are exempt — they intentionally
    assert on the string as absence-guards.

    Whitelist:
      · backend/routers/vercel.py — comment about a Vercel plan
        label ("hobby") that happens to contain the string; not
        an actionable contact.
    """
    import subprocess
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    # Only search product code, skip tests/, dist/, node_modules
    out = subprocess.run(
        ["grep", "-rn", "ora@auremcto.com",
         str(repo / "backend"),
         str(repo / "frontend" / "src"),
         str(repo / "frontend" / "public"),
         "--include=*.py", "--include=*.js", "--include=*.jsx",
         "--include=*.tsx", "--include=*.md", "--include=*.html"],
        capture_output=True, text=True,
    )
    hits = [ln for ln in out.stdout.splitlines()
            if ln.strip()
            and "/tests/" not in ln
            and "/dist/" not in ln
            and "/routers/vercel.py" not in ln]
    assert not hits, (
        "ora@auremcto.com re-introduced in user-facing code (no MX "
        "→ guaranteed bounce). Offending lines:\n" + "\n".join(hits)
    )
