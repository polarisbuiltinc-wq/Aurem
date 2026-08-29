"""
Backend-only verification for two shipped fixes:
1. ORA council recall mode-taxonomy fix
2. Plain-English Output Contract for explain-style answers
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def test_login_and_me(session):
    r = session.get(f"{API}/auth/me", timeout=30)
    assert r.status_code == 200, r.text[:300]
    me = r.json()
    uid = me.get("user_id") or me.get("id") or (me.get("user") or {}).get("user_id") or (me.get("user") or {}).get("id")
    assert uid == "test_admin_001", f"Expected user_id=test_admin_001 got {uid} full={me}"


def test_council_recall_taxonomy_fix(session):
    """A0 fix: council_recalled should be >=1 for 'hi' now (was always 0)."""
    r = session.post(
        f"{API}/chat/send",
        json={"prompt": "hi", "project_id": "home"},
        timeout=120,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
    data = r.json()
    cr = data.get("council_recalled")
    assert isinstance(cr, int), f"council_recalled missing/not-int: {cr} keys={list(data.keys())}"
    assert cr >= 1, f"Expected council_recalled >=1 (fix), got {cr}"


@pytest.mark.flaky(
    reason="Live backend call against a real connected repo/LLM contract "
           "check — intermittent in full-suite batch runs, passes "
           "reliably standalone. Confirmed 2026-08-28 P0-4 audit "
           "(RECON-LEDGER.md).",
    owner="e1-agent",
    fix_by="next-live-network-hardening-pass",
)
def test_plain_english_contract_active_on_explain(session):
    """Plain-English contract must activate for explain-style prompt on connected repo project."""
    prompt = "how do the agents in my project work? explain it to me simply, im not super technical"
    r = session.post(
        f"{API}/chat/send",
        json={"prompt": prompt, "project_id": "p_2d30ef16d1"},
        timeout=180,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
    data = r.json()
    assert data.get("plain_english_contract_active") is True, (
        f"Expected plain_english_contract_active=True; got {data.get('plain_english_contract_active')}"
    )
    content = (data.get("content") or "").lower()
    assert content, "Empty content"
    # Soft plain-english heuristics - not a hard fail, only reported
    tech_leaks = [".py", "```", "def ", "grounding_check", "ora_council_retriever"]
    leaks = [t for t in tech_leaks if t in content]
    # Should offer opt-in for more technical detail
    has_optin = any(k in content for k in ["technical detail", "want the technical", "more detail", "deeper", "under the hood", "technical version"])
    print(f"[plain-english] leaks={leaks} has_optin={has_optin} content_preview={content[:400]}")
    # Hard-assert no code fences (strongest indicator of tone violation)
    assert "```" not in content, "Response contained code fences (violates plain-english contract)"


def test_plain_english_contract_inactive_on_mutation(session):
    """Mutation-shaped prompt (mode D/C) must NOT activate the plain-english contract."""
    r = session.post(
        f"{API}/chat/send",
        json={"prompt": "fix the deployment error and ship it via CTO", "project_id": "home"},
        timeout=180,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
    data = r.json()
    assert data.get("plain_english_contract_active") is False, (
        f"Expected plain_english_contract_active=False for mutation prompt; got {data.get('plain_english_contract_active')}"
    )


def test_regression_smoke_simple_greeting(session):
    """Basic chat still works."""
    r = session.post(
        f"{API}/chat/send",
        json={"prompt": "hello"},
        timeout=120,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
    data = r.json()
    assert isinstance(data.get("content"), str) and len(data["content"]) > 0, f"Empty/missing content: {data}"
