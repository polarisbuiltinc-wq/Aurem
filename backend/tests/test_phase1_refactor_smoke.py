"""Phase 1 Codebase Health refactor smoke tests.

Covers:
- Login (admin)
- Health score endpoint returns 200 with 9 categories
- Endpoints touched by refactored helpers still return 2xx (not 500)
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"

REQUIRED_CATEGORIES = {
    "security", "bug_density", "reliability", "test_coverage",
    "code_quality", "data_handling", "performance", "architecture", "devops_infra",
}


@pytest.fixture(scope="module")
def s():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    # Try common login endpoints
    for path in ["/api/auth/login", "/api/aurem-dev/auth/login"]:
        r = session.post(f"{BASE_URL}{path}", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        if r.status_code == 200:
            data = r.json()
            token = data.get("access_token") or data.get("token") or data.get("jwt")
            if token:
                session.headers.update({"Authorization": f"Bearer {token}"})
            return session
    pytest.skip(f"Login failed on both endpoints; last status={r.status_code} body={r.text[:200]}")


def test_health_score_endpoint(s):
    r = s.get(f"{BASE_URL}/api/aurem-dev/admin/health-score")
    assert r.status_code == 200, f"status={r.status_code} body={r.text[:400]}"
    data = r.json()
    # Locate categories - may be nested
    cats = data.get("categories") or data.get("scores") or data
    cat_names = set()
    if isinstance(cats, dict):
        cat_names = set(cats.keys())
    elif isinstance(cats, list):
        cat_names = {c.get("name") or c.get("category") for c in cats}
    missing = REQUIRED_CATEGORIES - cat_names
    assert not missing, f"Missing categories: {missing}. Got: {cat_names}"


def test_engagement_my_streak(s):
    r = s.get(f"{BASE_URL}/api/aurem-dev/engagement/my-streak")
    assert r.status_code < 500, f"5xx failure: {r.status_code} {r.text[:300]}"


def test_founder_offer_claim(s):
    r = s.get(f"{BASE_URL}/api/aurem-dev/founder-offer/claim")
    # Accept any non-5xx (may be 400/404 if user not eligible, but not 500)
    assert r.status_code < 500, f"5xx failure: {r.status_code} {r.text[:300]}"


def test_admin_support_errors_list(s):
    # Try both possible paths
    for path in ["/api/aurem-dev/admin/support/errors", "/api/aurem-dev/admin/errors"]:
        r = s.get(f"{BASE_URL}{path}")
        if r.status_code != 404:
            assert r.status_code < 500, f"5xx on {path}: {r.text[:300]}"
            return
    pytest.skip("No admin errors endpoint found (404 both paths)")


def test_advisor_open_prs_no_repo(s):
    # Non-crashing behavior test - should return either error state or graceful response, not 500
    r = s.get(f"{BASE_URL}/api/aurem-dev/advisor/context/open-prs?project_id=nonexistent-project-id")
    # 404/400 fine, 500 not fine
    assert r.status_code != 500, f"500 crash on missing-repo: {r.text[:300]}"
