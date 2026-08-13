"""test_iter388ae_stripe_webhook_compat.py — Iter 388-ae (2026-02-14).

Regression net for the Stripe legacy-path compatibility alias.

Root-cause context (short version):
- Prod tail 2026-02-14 showed ~7 hits of
  `POST /api/stripe/webhook HTTP/1.1" 404 Not Found` in one minute.
- Stripe's dashboard endpoint URL was configured to
  `/api/stripe/webhook`, but our real handler lived at
  `/api/aurem-dev/payments/webhook`. Every real payment webhook 404'd.
- Consequence: cto_payments never transitioned from "pending" →
  "paid", so #35 Payments Accuracy showed $0 revenue on prod despite
  68 ledger rows.

This test asserts that BOTH the canonical path and the legacy alias
are registered on the FastAPI app, so a future refactor can't
silently break the alias.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from main import app


def _post_paths(app) -> set[str]:
    """Return the set of POST paths registered on the app."""
    out = set()
    for r in app.routes:
        methods = getattr(r, "methods", None) or set()
        path    = getattr(r, "path", None)
        if not path or not methods:
            continue
        if "POST" in methods:
            out.add(path)
    return out


def test_canonical_webhook_path_still_registered():
    paths = _post_paths(app)
    assert "/api/aurem-dev/payments/webhook" in paths, (
        "Canonical Stripe webhook path missing — payments.py refactor "
        "would break every current integration."
    )


def test_legacy_alias_path_registered():
    """Iter 388-ae — the new legacy compat path `/api/stripe/webhook`
    MUST be present so Stripe dashboards configured with that URL
    still deliver webhook events."""
    paths = _post_paths(app)
    assert "/api/stripe/webhook" in paths, (
        "Legacy Stripe alias `/api/stripe/webhook` missing — the "
        "Payments $0 root-cause fix is regressed. See "
        "routers/stripe_webhook_compat.py."
    )


def test_alias_returns_same_error_shape_as_canonical():
    """Both endpoints must reject unsigned payloads identically — same
    status, same detail string. This proves the alias truly delegates
    to the canonical handler rather than diverging in behaviour."""
    with TestClient(app) as c:
        r_alias     = c.post("/api/stripe/webhook", json={})
        r_canonical = c.post("/api/aurem-dev/payments/webhook", json={})

    # Both should reject with 400 (missing/invalid signature) — the
    # exact status code depends on whether STRIPE_WEBHOOK_SECRET is
    # configured in the test env (503) or set (400). Either way, the
    # two responses must MATCH.
    assert r_alias.status_code == r_canonical.status_code, (
        f"alias={r_alias.status_code} canonical={r_canonical.status_code}"
    )
    assert r_alias.json() == r_canonical.json(), (
        f"alias={r_alias.json()!r} canonical={r_canonical.json()!r}"
    )
