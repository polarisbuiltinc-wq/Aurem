"""
Tests for AUREM CTO Production-Readiness Pillars 2-6 backend changes (2026-08-24).
Covers:
  - Pillar 5: JWT error message hardening (no raw exception leak)
  - Pillar 4: health-score with all 9 categories + caveats
  - Pillar 3: CI ingest heartbeat endpoint
  - Pillar 6: founder-summary generate + retrieve two-view
"""
import os
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE_URL:
    try:
        for _ln in open("/app/frontend/.env"):
            if _ln.strip().startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = _ln.split("=", 1)[1].strip().rstrip("/")
                break
    except FileNotFoundError:
        pass
API = f"{BASE_URL}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    tok = data.get("token") or data.get("access_token")
    assert tok, f"no token in login response: {data}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# --- Regression: normal login still works ---
def test_login_returns_token():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body.get("token") or body.get("access_token")


# --- Pillar 5: invalid token returns clean generic message ---
def test_invalid_token_returns_generic_401():
    # A malformed token that will trigger jwt.InvalidTokenError path
    bad = "this.is.not-a-valid-jwt"
    r = requests.get(f"{API}/admin/health-score", headers={"Authorization": f"Bearer {bad}"}, timeout=15)
    assert r.status_code == 401, f"expected 401 got {r.status_code}: {r.text[:200]}"
    body = r.json()
    detail = body.get("detail", "")
    assert detail == "Invalid token", f"expected 'Invalid token', got: {detail!r}"
    # No raw exception leak
    assert "codec" not in detail.lower()
    assert "decode" not in detail.lower()
    assert "invalid header" not in detail.lower()


def test_garbage_bytes_token_no_leak():
    # A token containing non-utf8-safe chars — used to leak codec errors
    bad = "!!!\x80\x81not-a-jwt.at.all"
    r = requests.get(f"{API}/admin/health-score", headers={"Authorization": f"Bearer {bad}"}, timeout=15)
    assert r.status_code == 401
    detail = r.json().get("detail", "")
    assert detail == "Invalid token" or "authorization" in detail.lower() or "format" in detail.lower(), \
        f"unexpected detail: {detail!r}"
    assert "codec" not in detail.lower() and "traceback" not in detail.lower()


# --- Pillar 4: health-score with 9 categories & caveats ---
def test_health_score_all_9_categories(auth_headers):
    r = requests.get(f"{API}/admin/health-score", headers=auth_headers, timeout=45)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
    data = r.json()
    assert "categories" in data
    cats = data["categories"]
    # cats is a dict keyed by category name
    assert isinstance(cats, dict)
    assert len(cats) >= 9, f"expected >=9 categories, got {len(cats)}"
    assert data.get("weight_scored_pct") == 100, f"weight_scored_pct={data.get('weight_scored_pct')}"

    security = cats.get("security")
    reliability = cats.get("reliability")
    bug_density = cats.get("bug_density")
    assert security is not None, "missing security"
    assert reliability is not None, "missing reliability"
    assert bug_density is not None, "missing bug_density"

    for name, c in [("security", security), ("reliability", reliability), ("bug_density", bug_density)]:
        assert c.get("score") is not None, f"{name} score is None"
        # 'evidence' key OR equivalent evidence signals (live/last_verified)
        has_evidence = (
            c.get("evidence") is not None
            or (c.get("live") is True and c.get("last_verified"))
        )
        assert has_evidence, f"{name} has no evidence signal: {c}"

    assert isinstance(reliability.get("caveat"), str) and reliability["caveat"], "reliability missing caveat"
    assert isinstance(bug_density.get("caveat"), str) and bug_density["caveat"], "bug_density missing caveat"
    assert "caveat" not in security or security.get("caveat") in (None, ""), \
        f"security should not have caveat, has: {security.get('caveat')!r}"


# --- Pillar 3: heartbeat endpoint ---
def test_heartbeat_endpoint(auth_headers):
    r = requests.get(f"{API}/admin/synthetic-checks/heartbeat", headers=auth_headers, timeout=15)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
    data = r.json()
    assert "available" in data
    assert "checked_at" in data
    assert "expected_max_gap_hours" in data
    assert "kinds" in data
    kinds = data["kinds"]
    for k in ("g1_route_sweep", "g15_dep_scan"):
        assert k in kinds, f"missing kind {k}"
        entry = kinds[k]
        assert "last_seen_at" in entry
        assert "age_hours" in entry
        assert "stale" in entry
        assert isinstance(entry["stale"], bool)


# --- Pillar 6: founder-summary generate + retrieve ---
def test_founder_summary_generate_and_retrieve(auth_headers):
    payload = {
        "source": "test",
        "event_id": "test-run-1",
        "technical_event": {
            "commit_sha": "abc123",
            "files_changed": ["x.py"],
            "error": "a bug in the login flow",
            "fix_summary": "added a null check",
        },
    }
    r = requests.post(f"{API}/admin/founder-summary/generate",
                      headers=auth_headers, json=payload, timeout=45)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
    data = r.json()
    assert "founder_view" in data
    assert "technical_view" in data
    fv = data["founder_view"]
    for k in ("what_changed", "what_to_verify", "risk"):
        assert k in fv, f"founder_view missing {k}"

    # No jargon in founder_view values (skip generation_error field if fallback)
    banned = ["abc123", "x.py", ".py", "commit_sha"]
    fv_text = " ".join(str(v) for k, v in fv.items() if k != "generation_error").lower()
    for b in banned:
        assert b.lower() not in fv_text, f"founder_view contains jargon {b!r}: {fv_text[:300]}"

    tv = data["technical_view"]
    assert tv.get("commit_sha") == "abc123"
    assert tv.get("files_changed") == ["x.py"]

    # Retrieve founder view
    r2 = requests.get(f"{API}/admin/founder-summary/test-run-1?view=founder",
                      headers=auth_headers, timeout=15)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2.get("view") == "founder"
    assert "what_changed" in d2.get("data", {})

    # Retrieve technical view
    r3 = requests.get(f"{API}/admin/founder-summary/test-run-1?view=technical",
                      headers=auth_headers, timeout=15)
    assert r3.status_code == 200
    d3 = r3.json()
    assert d3.get("view") == "technical"
    assert d3.get("data", {}).get("commit_sha") == "abc123"
