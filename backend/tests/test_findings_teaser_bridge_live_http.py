"""Live-HTTP validation of findings teaser bridge (Phase 1).

Complements the direct-call tests in test_findings_teaser_bridge_2026_08_23.py
by hitting the real FastAPI routing + JWT auth path through the public preview
URL so we catch middleware/routing/auth wiring regressions.
"""
from __future__ import annotations

import os
import time
import asyncio
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else None
if not BASE_URL:
    # Fallback for backend-side execution: try frontend/.env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break
    except Exception:
        pass

API = f"{BASE_URL}/api/aurem-dev" if BASE_URL else None

TEST_EMAIL = "test@aurem.dev"
TEST_PASS = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    if not API:
        pytest.skip("No REACT_APP_BACKEND_URL")
    # Try common login endpoints
    for path, payload in [
        ("/auth/login", {"email": TEST_EMAIL, "password": TEST_PASS}),
        ("/founder/login", {"email": TEST_EMAIL, "password": TEST_PASS}),
    ]:
        try:
            r = requests.post(f"{API}{path}", json=payload, timeout=15)
            if r.status_code == 200:
                data = r.json()
                tok = data.get("token") or data.get("access_token") or data.get("jwt")
                if tok:
                    return tok
        except Exception:
            continue
    pytest.skip("Login failed via known endpoints")


@pytest.fixture(scope="module")
def user_id(token):
    r = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if r.status_code != 200:
        pytest.skip(f"auth/me failed: {r.status_code}")
    d = r.json()
    return (d.get("user") or {}).get("user_id") or d.get("user_id") or d.get("id")


@pytest.fixture(scope="module")
def seeded(user_id):
    """Seed a project + open critical finding directly in Mongo for isolation."""
    import motor.motor_asyncio
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ.get("DB_NAME", "aurem_dev")

    async def _seed():
        client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        pid = f"live_teaser_proj_{int(time.time())}"
        fid = f"ora_chat_audit::auth.py:42:live-teaser-{int(time.time())}"
        await db.cto_projects.insert_one({
            "project_id": pid, "user_id": user_id,
            "github_owner": "octocat", "github_repo": "Hello-World",
            "github_branch": "main",
        })
        await db.cto_open_findings.insert_one({
            "user_id": user_id, "project_id": pid,
            "finding_id": fid, "rule_id": "hardcoded-jwt-secret",
            "severity": "critical", "status": "open",
            "file": "auth.py", "line": 42,
            "title": "Live test hardcoded JWT secret",
            "message": "JWT secret fallback",
            "fix_hint": "Require env var.",
            "scanner": "ora_chat_audit", "exposure_count": 0,
        })
        cnt = await db.cto_open_findings.count_documents({"project_id": pid})
        proj = await db.cto_projects.count_documents({"project_id": pid})
        print(f"SEED-DEBUG: pid={pid}, uid={user_id}, findings={cnt}, projects={proj}, db={db.name}, host={client.HOST}")
        client.close()
        return pid, fid

    async def _cleanup(pid):
        client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        await db.cto_open_findings.delete_many({"project_id": pid})
        await db.cto_projects.delete_many({"project_id": pid})
        await db.cto_notification_dismissals.delete_many({"project_id": pid})
        client.close()

    pid, fid = asyncio.run(_seed())
    yield pid, fid
    asyncio.run(_cleanup(pid))


def test_backlog_matched_live_http(token, seeded):
    pid, fid = seeded
    r = requests.get(
        f"{API}/findings/backlog",
        params={"project_id": pid, "ids": fid},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    print("DEBUG:", d)
    assert d.get("ok") is True
    matched = d.get("matched") or []
    assert len(matched) == 1, f"expected 1 matched, got {matched}"
    row = matched[0]
    assert row["finding_id"] == fid
    assert row["rule_id"] == "hardcoded-jwt-secret"
    assert row["file"] == "auth.py"
    assert row["line"] == 42
    assert row["severity"] == "critical"
    assert d["tracked_status"][fid] == "open"


def test_backlog_idor_404_live_http(token):
    r = requests.get(
        f"{API}/findings/backlog",
        params={"project_id": "nonexistent_pytest_project"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code == 404, f"got {r.status_code}: {r.text}"


def test_backlog_idor_403_live_http(token, seeded):
    """Seed a project owned by a different user, then try to access it."""
    import motor.motor_asyncio
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ.get("DB_NAME", "aurem_dev")

    async def _seed_other():
        client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        pid = f"live_teaser_other_{int(time.time())}"
        await db.cto_projects.insert_one({
            "project_id": pid, "user_id": "someone_else_totally",
            "github_owner": "x", "github_repo": "y", "github_branch": "main",
        })
        client.close()
        return pid

    async def _cleanup(pid):
        client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        await db.cto_projects.delete_many({"project_id": pid})
        client.close()

    pid = asyncio.run(_seed_other())
    try:
        r = requests.get(
            f"{API}/findings/backlog",
            params={"project_id": pid},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        assert r.status_code == 403, f"IDOR: expected 403, got {r.status_code}: {r.text}"
    finally:
        asyncio.run(_cleanup(pid))


def test_dismiss_endpoint_live_http(token, seeded):
    pid, fid = seeded
    batch_id = f"chat_teaser::{fid}"
    r = requests.post(
        f"{API}/findings/dismiss",
        json={"project_id": pid, "finding_batch_id": batch_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code in (200, 201), f"dismiss failed: {r.status_code} {r.text}"
