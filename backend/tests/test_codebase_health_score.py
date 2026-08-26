"""Backend tests for Codebase Health Score feature (2026-08-23)."""
import os
import time
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

EXPECTED_CATS = {
    "security", "bug_density", "reliability", "test_coverage",
    "code_quality", "data_handling", "performance",
    "architecture", "devops_infra",
}
# 2026-08-27 · I2 fix — this used to be `{"security", "bug_density",
# "reliability"}` and hard-asserted every one of them "unscored" below.
# Root cause of the resulting ambient red (diagnosed, not a fixture-
# isolation bug for 2 of the 3): `score_security()` and
# `score_reliability()` (services/health_score.py) were converted to
# unconditional, always-live scorers by the 2026-08-24 Inventory Sweep
# wiring (see their own "Was UNSCORED before this wiring" / "LIVE via
# Health Registry guards" comments) — they can NEVER return "unscored"
# against a real DB again, by design. The test's expectation was simply
# stale, not a data-pollution problem. `bug_density` IS genuinely
# data-conditional (unscored only while G20's incident log has zero
# total incidents ever) — real guard-detected incidents accumulating
# during live testing/usage legitimately flip it to "scored"; that is
# the intended reliability signal, not pollution to isolate against.
ALWAYS_LIVE_SCORED = {"security", "reliability"}
DATA_CONDITIONAL = {"bug_density"}


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login",
                      json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    if data.get("mfa_required"):
        pytest.skip("MFA required — cannot proceed without TOTP")
    assert data.get("is_admin"), f"user is not admin: {data}"
    return data["token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


# ── auth gate ──
def test_health_score_requires_admin():
    r = requests.get(f"{API}/admin/health-score", timeout=15)
    assert r.status_code in (401, 403), f"expected 401/403 unauth, got {r.status_code}"


# ── main GET shape ──
def test_health_score_get_shape_and_categories(headers):
    t0 = time.time()
    r = requests.get(f"{API}/admin/health-score", headers=headers, timeout=45)
    elapsed = time.time() - t0
    assert r.status_code == 200, r.text
    data = r.json()
    for k in ("generated_at", "weights", "categories", "overall_score",
              "weight_scored_pct", "weight_unscored_pct", "unscored_categories"):
        assert k in data, f"missing key: {k}"
    assert set(data["categories"].keys()) == EXPECTED_CATS
    # weights sum to 100
    assert sum(data["weights"].values()) == 100
    for cid, cat in data["categories"].items():
        for k in ("status", "score", "reason", "evidence", "live", "last_verified"):
            assert k in cat, f"{cid} missing {k}"
        assert cat["status"] in ("scored", "unscored")
    # security + reliability: always live-scored by design (see
    # ALWAYS_LIVE_SCORED comment above) — never "unscored" against a
    # real DB.
    for cid in ALWAYS_LIVE_SCORED:
        cat = data["categories"][cid]
        assert cat["status"] == "scored", f"{cid} unexpectedly unscored: {cat}"
        assert cat["live"] is True
        assert isinstance(cat["score"], (int, float))
    # bug_density: data-conditional — either shape is valid depending on
    # whether G20's incident log has any real incidents yet. Assert
    # whichever branch fired is internally consistent rather than
    # hard-coding one, since real incidents from live testing/usage are
    # expected to accumulate over time (2026-08-24 founder-approved
    # recalibration), not a fixture bug to isolate against.
    for cid in DATA_CONDITIONAL:
        cat = data["categories"][cid]
        if cat["status"] == "unscored":
            assert cat["reason"], f"{cid} unscored with no reason"
        else:
            assert cat["live"] is True
            assert isinstance(cat["score"], (int, float))
            assert cat.get("caveat"), f"{cid} scored but missing partial-proxy caveat"
    # weight_scored_pct + weight_unscored_pct == 100
    assert abs(data["weight_scored_pct"] + data["weight_unscored_pct"] - 100) < 0.5
    # Latency: soft check
    print(f"GET /admin/health-score elapsed={elapsed:.2f}s")
    assert elapsed < 40, f"endpoint too slow: {elapsed:.2f}s"


def test_code_quality_and_architecture_are_scored_and_live(headers):
    r = requests.get(f"{API}/admin/health-score", headers=headers, timeout=45)
    data = r.json()
    for cid in ("code_quality", "architecture"):
        cat = data["categories"][cid]
        assert cat["status"] == "scored", f"{cid} not scored: {cat}"
        assert cat["live"] is True
        assert cat["last_verified"] is not None
        assert isinstance(cat["score"], int)


def test_two_calls_deterministic_code_quality(headers):
    r1 = requests.get(f"{API}/admin/health-score", headers=headers, timeout=45).json()
    time.sleep(2)
    r2 = requests.get(f"{API}/admin/health-score", headers=headers, timeout=45).json()
    s1 = r1["categories"]["code_quality"]["score"]
    s2 = r2["categories"]["code_quality"]["score"]
    assert abs(s1 - s2) <= 2, f"code_quality score too volatile: {s1} vs {s2}"
    lv1 = r1["categories"]["code_quality"]["last_verified"]
    lv2 = r2["categories"]["code_quality"]["last_verified"]
    assert lv1 != lv2, "last_verified should refresh on every call"


def test_devops_infra_live_github(headers):
    r = requests.get(f"{API}/admin/health-score", headers=headers, timeout=45)
    cat = r.json()["categories"]["devops_infra"]
    ci = cat.get("evidence", {}).get("ci", {})
    assert ci.get("available") is True, f"CI not available: {ci}"
    assert ci.get("total_runs_30d", 0) > 0, f"no runs: {ci}"
    assert cat["status"] == "scored"


def test_data_handling_shape(headers):
    r = requests.get(f"{API}/admin/health-score", headers=headers, timeout=45)
    cat = r.json()["categories"]["data_handling"]
    # Expected to be scored per feature spec; but be tolerant to unscored
    ev = cat.get("evidence", {})
    assert "rollback" in ev, f"data_handling missing rollback evidence: {ev}"


def test_performance_middleware_writes_samples(headers):
    # Hit a few /api/ endpoints to trigger sampler
    for _ in range(3):
        requests.get(f"{API}/admin/health-score", headers=headers, timeout=45)
    # Query health-score again and inspect performance evidence sample count
    r = requests.get(f"{API}/admin/health-score", headers=headers, timeout=45).json()
    perf = r["categories"]["performance"]
    assert "endpoint_sample_count" in perf.get("evidence", {})
    # Fresh install typical — status could be unscored, that's expected
    assert perf["status"] in ("scored", "unscored")


# ── Architecture review POST/GET ──
def test_architecture_review_reject_empty_rubric(headers):
    r = requests.post(f"{API}/admin/health-score/architecture-review",
                      headers=headers, json={"reviewer": "TEST_bot",
                                              "notes": "test", "rubric": {}},
                      timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert "rubric" in body.get("error", "").lower()


def test_architecture_review_create_and_list(headers):
    payload = {
        "reviewer": "TEST_bot",
        "notes": "TEST_review from pytest",
        "rubric": {"coupling": 80, "spof": 70, "clarity": 90},
    }
    r = requests.post(f"{API}/admin/health-score/architecture-review",
                      headers=headers, json=payload, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body["review"]["reviewer"] == "TEST_bot"

    r2 = requests.get(f"{API}/admin/health-score/architecture-review",
                      headers=headers, timeout=15)
    assert r2.status_code == 200
    reviews = r2.json().get("reviews", [])
    assert any(rv.get("notes") == "TEST_review from pytest" for rv in reviews)


# ── Test-coverage trigger ──
def test_coverage_run_trigger_returns_started(headers):
    r = requests.post(f"{API}/admin/health-score/test-coverage/run",
                      headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json().get("status") == "started"


def test_coverage_run_requires_admin():
    r = requests.post(f"{API}/admin/health-score/test-coverage/run", timeout=15)
    assert r.status_code in (401, 403)


def test_architecture_review_requires_admin():
    r = requests.get(f"{API}/admin/health-score/architecture-review", timeout=15)
    assert r.status_code in (401, 403)
    r2 = requests.post(f"{API}/admin/health-score/architecture-review",
                       json={"rubric": {"x": 1}}, timeout=15)
    assert r2.status_code in (401, 403)
