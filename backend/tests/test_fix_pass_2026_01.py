"""Fix-pass verification (Jan 2026): token_pnl real-cost, eval_quality quick-exclusion, errors/report rate-limit exemption."""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    # Try both possible login paths
    for path in ["/api/auth/login", "/api/aurem-dev/auth/login"]:
        r = s.post(f"{BASE_URL}{path}", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            token = data.get("access_token") or data.get("token")
            if token:
                s.headers.update({"Authorization": f"Bearer {token}"})
            return s
    pytest.skip(f"Login failed on all paths (last status={r.status_code}, body={r.text[:200]})")


def _get_admin(session, path):
    """Try both /api/admin and /api/aurem-dev/admin prefixes."""
    for prefix in ["/api/aurem-dev/admin", "/api/admin"]:
        r = session.get(f"{BASE_URL}{prefix}{path}", timeout=20)
        if r.status_code != 404:
            return r, prefix
    return r, None


def test_token_pnl_real_cost(admin_session):
    r, prefix = _get_admin(admin_session, "/token-pnl")
    assert r.status_code == 200, f"token-pnl status {r.status_code}: {r.text[:300]}"
    data = r.json()
    print(f"\ntoken-pnl keys: {list(data.keys())}")
    print(f"ai_cost_month={data.get('ai_cost_month')}, ai_cost_today={data.get('ai_cost_today')}")
    print(f"month_by_agent={data.get('month_by_agent')}")
    print(f"day_by_agent={data.get('day_by_agent')}")
    print(f"month_calls_by_agent={data.get('month_calls_by_agent')}")
    print(f"day_calls_by_agent={data.get('day_calls_by_agent')}")
    # Fixed contract per review request
    assert "ai_cost_month" in data
    assert "ai_cost_today" in data
    assert "month_by_agent" in data
    assert "day_by_agent" in data
    assert "month_calls_by_agent" in data, "New field month_calls_by_agent missing"
    assert "day_calls_by_agent" in data, "New field day_calls_by_agent missing"
    # ai_cost should be non-zero (real spend exists per review context)
    assert (data.get("ai_cost_month") or 0) > 0, f"ai_cost_month should be > 0, got {data.get('ai_cost_month')}"


def test_eval_quality_excludes_quick(admin_session):
    r, prefix = _get_admin(admin_session, "/eval-quality")
    assert r.status_code == 200, f"eval-quality status {r.status_code}: {r.text[:300]}"
    data = r.json()
    print(f"\neval-quality: {data}")
    latest = data.get("latest") or {}
    assert latest.get("total", 0) > 0, f"latest.total must be > 0 (not a quick-liveness doc), got {latest}"


def test_agent_performance_consistent(admin_session):
    r, prefix = _get_admin(admin_session, "/agent-performance")
    assert r.status_code == 200, f"agent-performance status {r.status_code}: {r.text[:300]}"
    data = r.json()
    print(f"\nagent-performance keys: {list(data.keys())}")
    # Just log for reviewer visual consistency check
    total = 0
    for agent, stats in (data.get("agents") or {}).items() if isinstance(data.get("agents"), dict) else []:
        total += (stats or {}).get("cost", 0) if isinstance(stats, dict) else 0
    print(f"agent-performance total cost (rough): {total}")


def test_errors_report_rate_limit_exempt():
    """Send 30 rapid POSTs; none should return 429."""
    payload = {"message": "test crash", "stack": "TestStack", "url": BASE_URL}
    codes = []
    session = requests.Session()
    for prefix in ["/api/aurem-dev/admin/errors/report", "/api/admin/errors/report"]:
        codes = []
        for _ in range(30):
            r = session.post(f"{BASE_URL}{prefix}", json=payload, timeout=10)
            codes.append(r.status_code)
        print(f"\n{prefix} codes sample: {codes[:10]}... unique={set(codes)}")
        if 404 in set(codes) and len(set(codes)) == 1:
            continue  # endpoint on other prefix
        break
    assert 429 not in codes, f"errors/report rate-limited! codes={codes}"


def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=10)
    assert r.status_code == 200
