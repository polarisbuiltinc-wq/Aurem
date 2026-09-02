"""test_ora_ip_allowlist_2026_09_08.py — 2026 audit Decision 2.

/ora-chat/pin-login had NO auth at all (confirmed live: no
Authorization header required — the frontend's <PrivateRoute> only
gates the page render, not this API). The 4-digit PIN was the ONLY
gate against a remote brute-force. Cheap, high-leverage hardening:
an IP allowlist checked BEFORE the PIN itself, so a disallowed caller
never even gets to try a PIN.

Left fail-open (ORA_ALLOWED_IPS unset) by default so this can't lock
the founder out sight-unseen — the founder sets their own IP(s) when
ready. This test proves the allowlist actually gates BEFORE the PIN
check when it IS set, and that the endpoint still fails open when
it's not.
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from main import app

BASE = "/api/aurem-dev/ora-chat"


def test_t_ora_ip_allowlist_blocks(monkeypatch):
    """A disallowed IP gets 403 BEFORE the PIN is even checked."""
    monkeypatch.setenv("ORA_ALLOWED_IPS", "10.50.50.50")
    with TestClient(app) as c:
        r = c.post(f"{BASE}/pin-login", json={"pin": "not-even-checked"},
                   headers={"X-Forwarded-For": "10.99.99.99"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error"] == "ip_not_allowed"


def test_allowlist_permits_a_listed_ip_through_to_the_pin_check(monkeypatch):
    """A LISTED ip is NOT blocked by the allowlist — it proceeds to
    the normal PIN comparison (a wrong PIN there still 401s, proving
    this isn't a blanket allow, just a pass-through to the real gate)."""
    monkeypatch.setenv("ORA_ALLOWED_IPS", "10.50.50.50,10.60.60.60")
    with TestClient(app) as c:
        r = c.post(f"{BASE}/pin-login", json={"pin": "definitely-wrong"},
                   headers={"X-Forwarded-For": "10.50.50.50"})
        assert r.status_code != 403, (
            "listed IP was blocked by the allowlist — should have "
            "reached the PIN check instead"
        )
        assert r.status_code in (401, 429, 503), r.text


def test_allowlist_unset_fails_open(monkeypatch):
    """Default/current behavior preserved: with ORA_ALLOWED_IPS unset,
    no IP is blocked by the allowlist (any IP reaches the PIN check)."""
    monkeypatch.delenv("ORA_ALLOWED_IPS", raising=False)
    with TestClient(app) as c:
        r = c.post(f"{BASE}/pin-login", json={"pin": "definitely-wrong"},
                   headers={"X-Forwarded-For": "10.1.2.3"})
        assert r.status_code != 403 or r.json().get("detail", {}).get("error") != "ip_not_allowed"
        assert r.status_code in (401, 429, 503), r.text
