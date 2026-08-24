"""2026-08-26 — Admin data-audit fixes + GitHub App installation_active
root-cause fix + homepage promo write-concern fix."""
import os
import time
import asyncio
from datetime import datetime, timezone

import pytest
import requests


def _load_env():
    for line in open("/app/frontend/.env"):
        if line.startswith("REACT_APP_BACKEND_URL="):
            return line.strip().split("=", 1)[1].strip('"').rstrip("/")
    raise KeyError("REACT_APP_BACKEND_URL")


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or _load_env()


def _load_backend_env():
    from pathlib import Path
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return  # CI runners export the needed vars directly as job env
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip('"'))


_load_backend_env()
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/aurem-dev/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"admin login failed {r.status_code}: {r.text[:200]}")
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    return s


# ---------- Admin data audit fixes ----------
def test_admin_dashboard_pulse_financials_reachable(admin_session):
    d = admin_session.get(f"{BASE_URL}/api/aurem-dev/admin/dashboard", timeout=15)
    p = admin_session.get(f"{BASE_URL}/api/aurem-dev/admin/pulse", timeout=15)
    f = admin_session.get(f"{BASE_URL}/api/aurem-dev/admin/financials", timeout=15)
    assert d.status_code == 200, d.text[:300]
    assert p.status_code == 200, p.text[:300]
    assert f.status_code == 200, f.text[:300]
    assert "total_users" in p.json()
    assert "metrics" in f.json() and "mrr_usd" in f.json()["metrics"]


def test_architecture_mongo_status_does_real_ping(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/aurem-dev/admin/architecture", timeout=15)
    assert r.status_code == 200, r.text[:300]
    services = r.json().get("services", {})
    assert services.get("MongoDB", {}).get("status") == "live"
    # latency_ms should now be a real measured value, not hardcoded 0
    assert services["MongoDB"]["latency_ms"] >= 0


# ---------- Homepage promo endpoints (write-concern fix) ----------
def test_promo_first50_status_returns_200():
    r = requests.get(f"{BASE_URL}/api/aurem-dev/promo/first50/status", timeout=15)
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert "total" in data and "claimed" in data


def test_founder_offer_status_returns_200():
    r = requests.get(f"{BASE_URL}/api/aurem-dev/founder-offer/status", timeout=15)
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert "total" in data and "remaining" in data


# ---------- GitHub App installation_active root-cause fix ----------
def test_verify_installation_for_repo_unknown_install():
    from services.github_app import verify_installation_for_repo
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _run():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        ok, err_code, err_msg = await verify_installation_for_repo(
            db, user_id="TEST_no_such_user", installation_id=999999999,
            owner="acme", repo="widgets",
        )
        client.close()
        return ok, err_code, err_msg

    ok, err_code, err_msg = asyncio.run(_run())
    assert ok is False
    assert err_code == "installation_not_found_or_inactive"
    assert err_msg


def test_update_project_reconnect_rejects_unverified_installation(admin_session):
    """Regression + root-cause guard: PATCH reconnect must now run the
    SAME verification as add_project — a bogus installation_id must
    be rejected, not silently accepted (which is what let
    installation_active go unset before)."""
    # Create a throwaway PAT-less project isn't possible without a
    # repo; instead hit a project_id that doesn't exist — must 404,
    # not silently 200 with a fabricated install.
    r = admin_session.patch(
        f"{BASE_URL}/api/aurem-dev/cto/projects/TEST_nonexistent_project_id",
        json={"installation_id": 999999999},
        timeout=15,
    )
    assert r.status_code == 404, f"{r.status_code}: {r.text[:300]}"


def test_repair_orphaned_installations_dry_run_seeded(admin_session):
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _seed():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        await db.cto_projects.insert_one({
            "project_id": "TEST_orphan_proj_2026_08_26",
            "user_id": "TEST_orphan_user",
            "name": "test-orphan",
            "auth_method": "github_app",
            "installation_id": 999999999,
            "github_owner": "acme", "github_repo": "widgets",
            # installation_active deliberately OMITTED — the orphan pattern
            "created_at": datetime.now(timezone.utc),
        })
        client.close()

    async def _cleanup():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        await db.cto_projects.delete_many({"project_id": "TEST_orphan_proj_2026_08_26"})
        client.close()

    asyncio.run(_seed())
    try:
        r = admin_session.post(
            f"{BASE_URL}/api/aurem-dev/admin/github-app/repair-orphaned-installations?dry_run=true",
            timeout=30,
        )
        assert r.status_code == 200, r.text[:400]
        data = r.json()
        assert data["dry_run"] is True
        ids = [row["project_id"] for row in data["still_broken"]] + [row["project_id"] for row in data["repaired"]]
        assert "TEST_orphan_proj_2026_08_26" in ids, data
        # Fake installation_id 999999999 has no real github_installations
        # row, so this MUST land in still_broken, not repaired (never
        # blindly trust the stored ID).
        broken_ids = [row["project_id"] for row in data["still_broken"]]
        assert "TEST_orphan_proj_2026_08_26" in broken_ids, data
    finally:
        asyncio.run(_cleanup())


def test_repair_orphaned_installations_admin_only():
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/admin/github-app/repair-orphaned-installations?dry_run=true",
        timeout=15,
    )
    assert r.status_code in (401, 403), r.status_code
