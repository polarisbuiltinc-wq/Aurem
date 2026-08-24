"""Stage 1+2 batch — 2026-08-26 — SLO declaration + Ambiguity-gate/
Loop wiring (Blueprint Phase 5.3 + 1.3 gaps)."""
import os
import asyncio
import time
from datetime import datetime, timezone, timedelta

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


# ---------- shared ambiguity_gate module (pure unit tests) ----------
def test_ambiguity_gate_vague_phrases():
    from services.ambiguity_gate import is_ambiguous_task
    assert is_ambiguous_task("fix it") is True
    assert is_ambiguous_task("fix bugs") is True
    assert is_ambiguous_task("make it better") is True
    assert is_ambiguous_task("") is True
    assert is_ambiguous_task("do stuff") is True


def test_ambiguity_gate_concrete_phrases():
    from services.ambiguity_gate import is_ambiguous_task
    assert is_ambiguous_task("fix the signup form validation in Signup.jsx") is False
    assert is_ambiguous_task('rename the "Submit" button to "Continue"') is False
    assert is_ambiguous_task("add a code comment explaining the login function") is False


def test_cto_projects_reuses_shared_module_not_a_copy():
    """Guards against the exact drift bug this refactor closes — the
    legacy path must import the shared helper, not keep its own copy."""
    src = open("/app/backend/routers/cto_projects.py", encoding="utf-8").read()
    assert "from services.ambiguity_gate import is_ambiguous_task" in src
    # The old duplicated regex list must be gone from this file.
    assert "_VAGUE_TASK_PATTERNS" not in src


def test_loop_router_wires_ambiguity_gate():
    src = open("/app/backend/routers/loop.py", encoding="utf-8").read()
    assert "from services.ambiguity_gate import is_ambiguous_task" in src
    assert "needs_clarification" in src


# ---------- /loop/start ambiguity gate — real live call ----------
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


def test_loop_start_vague_message_returns_clarification(admin_session):
    r = admin_session.post(
        f"{BASE_URL}/api/aurem-dev/loop/start",
        json={"user_message": "fix it"}, timeout=20,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    data = r.json()
    assert data.get("needs_clarification") is True, data
    assert data.get("loop_id") is None, data
    assert "message" in data and len(data["message"]) > 10


def test_loop_start_concrete_message_does_not_get_clarification_gated(admin_session):
    """A specific message must NOT be caught by the ambiguity gate —
    it should proceed past it (may still fail later for other reasons
    like no connected repo, which is fine/expected here)."""
    r = admin_session.post(
        f"{BASE_URL}/api/aurem-dev/loop/start",
        json={"user_message": "fix the signup form validation in Signup.jsx"},
        timeout=20,
    )
    if r.status_code == 200:
        assert r.json().get("needs_clarification") is not True, r.json()
    # Any non-200 (e.g. 403 no project connected) is fine — we only
    # assert the ambiguity gate itself didn't fire.


# ---------- SLO metrics endpoint ----------
def _seed_slo_data():
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]

    async def _do():
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        now = datetime.now(timezone.utc)
        # 5 chat/send latency samples — p95 should land under 15s good target
        docs = [
            {"path": "/api/chat/send", "method": "POST", "status_code": 200,
             "elapsed_ms": ms, "ts": now - timedelta(hours=1), "tag": "TEST_slo_chat"}
            for ms in (2000, 3000, 4000, 5000, 6000)
        ]
        await db.health_endpoint_latency.insert_many(docs)
        # 3 completed ship tasks, well under 90s good target
        now_epoch = time.time()
        tasks = [
            {"task_id": f"TEST_slo_ship_{i}", "status": "done",
             "created_at": now_epoch - 3600 - dur, "completed_at": now_epoch - 3600,
             "tag": "TEST_slo_ship"}
            for i, dur in enumerate((30, 45, 60))
        ]
        await db.cto_tasks.insert_many(tasks)
        client.close()
    asyncio.run(_do())

    def cleanup():
        async def _cl():
            client = AsyncIOMotorClient(mongo_url)
            db = client[db_name]
            await db.health_endpoint_latency.delete_many({"tag": "TEST_slo_chat"})
            await db.cto_tasks.delete_many({"tag": "TEST_slo_ship"})
            client.close()
        asyncio.run(_cl())
    return cleanup


def test_slo_metrics_endpoint(admin_session):
    cleanup = _seed_slo_data()
    try:
        r = admin_session.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/slo?period_days=7", timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
        data = r.json()
        assert "slos" in data
        chat = data["slos"]["chat_response"]
        ship = data["slos"]["ship_completion"]
        assert chat["sample_size"] >= 5
        assert chat["p95_ms"] is not None
        assert chat["met"] is True  # all seeded samples well under 15s
        assert ship["sample_size"] >= 3
        assert ship["p95_s"] is not None
        assert ship["met"] is True  # all seeded durations well under 90s
    finally:
        cleanup()


def test_slo_metrics_admin_only():
    r = requests.get(
        f"{BASE_URL}/api/aurem-dev/admin/insights/slo?period_days=7", timeout=15,
    )
    assert r.status_code in (401, 403), r.status_code
