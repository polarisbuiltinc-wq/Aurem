"""test_slice_a_bi_cockpit.py — Slice A BI endpoint regression.

Guards the shape + honesty of the 3 /admin/bi/* endpoints so a future
refactor doesn't silently break the cockpit or start returning
catalog-projection numbers where LIVE Stripe data is expected.

Preview-only: uses the seed admin (`test@aurem.dev`) that lives in the
preview DB. On prod these tests are skipped (they hit the local
supervisor-managed backend on port 8001, so a prod run would just
return "backend not reachable" and skip cleanly).
"""
from __future__ import annotations

import os
import httpx
import pytest


BACKEND = "http://localhost:8001"
API = f"{BACKEND}/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


def _skip_if_backend_down() -> None:
    try:
        r = httpx.get(f"{BACKEND}/api/aurem-dev/health", timeout=2.0)
        if r.status_code >= 500:
            pytest.skip("backend not healthy")
    except Exception:
        pytest.skip("backend not reachable on localhost:8001")


def _login() -> str:
    _skip_if_backend_down()
    r = httpx.post(
        f"{API}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=10.0,
    )
    if r.status_code == 401:
        pytest.skip("preview admin credentials not present — expected in prod-like envs")
    r.raise_for_status()
    data = r.json()
    token = data.get("token") or data.get("access_token") or ""
    assert token, f"login returned no token: {data}"
    return token


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─── /admin/bi/stripe-metrics ─────────────────────────────────────
def test_stripe_metrics_shape():
    """Endpoint must return the full contract even when Stripe is empty
    or the key is missing. This is what prevents the front-end from
    silently rendering $0 MRR with no explanation."""
    token = _login()
    r = httpx.get(f"{API}/admin/bi/stripe-metrics",
                  headers=_headers(token), timeout=15.0)
    assert r.status_code == 200, r.text
    d = r.json()
    # Contract fields — every one MUST exist on every response so the
    # UI never has to defensively `?? 0`.
    for k in (
        "status", "error", "mode", "mrr_usd", "arr_usd",
        "active_subs", "trialing_subs", "past_due_subs",
        "new_30d", "canceled_30d", "arpu_usd", "generated_at",
    ):
        assert k in d, f"missing key: {k}"
    assert d["status"] in {"ok", "error", "missing_key"}, d["status"]
    # Type sanity
    assert isinstance(d["mrr_usd"], (int, float))
    assert isinstance(d["arr_usd"], (int, float))
    assert isinstance(d["active_subs"], int)
    # ARR must equal MRR × 12 (rounded), otherwise the projection card lies.
    assert abs(d["arr_usd"] - d["mrr_usd"] * 12) < 0.5, \
        f"ARR/MRR contract broken: mrr={d['mrr_usd']} arr={d['arr_usd']}"


# ─── /admin/bi/inference-metrics ──────────────────────────────────
def test_inference_metrics_shape():
    token = _login()
    r = httpx.get(f"{API}/admin/bi/inference-metrics",
                  headers=_headers(token), timeout=15.0)
    assert r.status_code == 200, r.text
    d = r.json()
    for k in (
        "today_usd", "month_usd", "budget", "daily_series_30d",
        "by_model", "by_route", "generated_at",
    ):
        assert k in d, f"missing key: {k}"
    # Budget must include the mode + caps that the /message router
    # uses to gate LLM calls — a drift here would let a call through
    # that should have been forced to the economy route.
    b = d["budget"]
    for k in ("mode", "day_cap_usd", "spike_cap_usd", "month_cap_usd",
              "day_spent_usd", "month_spent_usd"):
        assert k in b, f"budget missing: {k}"
    assert b["mode"] in {"normal", "warning", "economy", "spike_hard_stop"}
    # daily_series is a list of dicts with day/cost/calls/tokens.
    for row in d["daily_series_30d"]:
        for k in ("day", "cost", "calls", "tokens"):
            assert k in row
    for row in d["by_model"]:
        for k in ("model", "cost", "calls", "tokens"):
            assert k in row


# ─── /admin/bi/summary ────────────────────────────────────────────
def test_summary_combines_stripe_and_inference():
    """Summary is the atomic payload the front-end hits on load. Must
    contain both blocks + net-margin projection."""
    token = _login()
    r = httpx.get(f"{API}/admin/bi/summary",
                  headers=_headers(token), timeout=20.0)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "stripe" in d and "inference" in d
    for k in ("projected_month_infer_usd", "net_margin_usd",
              "net_margin_pct", "generated_at"):
        assert k in d
    # Net-margin math: mrr - projected_month_infer.
    mrr = float(d["stripe"].get("mrr_usd") or 0)
    proj = float(d["projected_month_infer_usd"] or 0)
    expected_net = round(mrr - proj, 2)
    assert abs(d["net_margin_usd"] - expected_net) < 0.01, \
        f"net-margin math drifted: got {d['net_margin_usd']}, expected {expected_net}"


# ─── Auth ──────────────────────────────────────────────────────────
def test_bi_endpoints_reject_anonymous():
    """The router is founder-only via router-level Depends. Anon → 401/403."""
    _skip_if_backend_down()
    for path in ("/admin/bi/stripe-metrics",
                 "/admin/bi/inference-metrics",
                 "/admin/bi/summary"):
        r = httpx.get(f"{API}{path}", timeout=10.0)
        assert r.status_code in (401, 403), \
            f"{path} allowed anonymous access! status={r.status_code}"
