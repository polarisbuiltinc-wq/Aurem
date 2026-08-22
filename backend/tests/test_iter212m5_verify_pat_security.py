"""test_iter212m5_verify_pat_security.py — REWRITTEN 2026-06 (PAT-removal).

The original suite exercised the stateless PAT-verification endpoint.
PATs are permanently retired (founder directive); the endpoint now
returns a uniform honest rejection. These tests lock that behavior in.
"""
import os

import pytest
import requests

API = (os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")
       .rstrip("/") + "/api/aurem-dev")


def _login():
    r = requests.post(f"{API}/auth/login", json={
        "email": "test@aurem.dev", "password": "AuremTest2026!"}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


@pytest.fixture(scope="module")
def token():
    return _login()


def test_verify_pat_always_rejects(token):
    r = requests.post(
        f"{API}/cto/projects/verify-pat",
        headers={"Authorization": f"Bearer {token}"},
        json={"pat": "ghp_" + "x" * 36,
              "github_url": "https://github.com/someone/some-repo"},
        timeout=15,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["error"] == "pat_not_supported"
    assert "GitHub App" in data["detail"]


def test_verify_pat_requires_auth():
    r = requests.post(
        f"{API}/cto/projects/verify-pat",
        json={"pat": "ghp_" + "x" * 36,
              "github_url": "https://github.com/someone/some-repo"},
        timeout=15,
    )
    assert r.status_code in (401, 403)
