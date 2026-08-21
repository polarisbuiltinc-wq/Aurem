"""Regression: chat send/stream + codebase_health access after batch fixes.

Covers:
- Login smoke (test@aurem.dev)
- POST /api/chat/send with no repo/citations returns an answer
- GET /api/codebase-health/last-scan is accessible to a normal logged-in user
  (relaxed from require_admin -> current_dev)
"""
import os
import json
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/aurem-dev/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Login failed {r.status_code}: {r.text[:200]}")
    data = r.json()
    tok = data.get("access_token") or data.get("token")
    if not tok:
        pytest.skip(f"No token in login response: {list(data.keys())}")
    return tok


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_login_ok(token):
    assert isinstance(token, str) and len(token) > 10


def test_chat_send_plain_prompt_no_repo(auth_headers):
    """Plain chat with no repo, no citations - should return a real answer."""
    payload = {
        "prompt": "In one short sentence, what is 2+2?",
        "task_type": "chat",
    }
    r = requests.post(f"{BASE_URL}/api/aurem-dev/chat/send", json=payload, headers=auth_headers, timeout=90)
    assert r.status_code in (200, 201), f"status={r.status_code} body={r.text[:400]}"
    data = r.json()
    # Should have some form of assistant content
    text_blob = json.dumps(data).lower()
    assert any(k in data for k in ("content", "message", "answer", "response", "text", "assistant")) or "4" in text_blob, (
        f"Unexpected chat response shape: {list(data.keys())[:10]}"
    )


def test_codebase_health_last_scan_accessible_to_normal_user(auth_headers):
    """/last should be accessible (not 403) after admin gate relaxation."""
    r = requests.get(f"{BASE_URL}/api/aurem-dev/codebase-health/last", headers=auth_headers, timeout=30, params={"project_id": "p_demo_a"})
    assert r.status_code != 403, f"Still admin-gated: {r.text[:200]}"
    # 200 with data, or 404 if no scan yet, both are acceptable
    assert r.status_code in (200, 404), f"Unexpected status {r.status_code}: {r.text[:200]}"


def test_sessions_list_endpoint_for_switcher(auth_headers):
    """Session-switcher UI depends on this endpoint returning the user's sessions."""
    r = requests.get(f"{BASE_URL}/api/aurem-dev/chat/sessions", headers=auth_headers, timeout=30, params={"project_id": "p_demo_a"})
    assert r.status_code == 200, f"status={r.status_code} body={r.text[:300]}"
    data = r.json()
    assert isinstance(data, (list, dict))
