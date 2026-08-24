"""Medium batch iter — 2026-01 — Intent Gateway, CI lints, DORA metrics."""
import os
import asyncio
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


# ---------- Intent Gateway heuristic ----------
def test_intent_gateway_agentic_verb_anywhere():
    from core.intent_gateway import classify_heuristic_sync as c
    r = c("Please proceed to make the edit and show me the ship confirmation.")
    assert r["tier"] == "agentic", r

def test_intent_gateway_regression_ship_next():
    from core.intent_gateway import classify_heuristic_sync as c
    assert c("what should I ship next")["tier"] == "query"

def test_intent_gateway_regression_how_do_i_ship():
    from core.intent_gateway import classify_heuristic_sync as c
    assert c("How do I ship this?")["tier"] == "query"

def test_intent_gateway_regression_show_leads():
    from core.intent_gateway import classify_heuristic_sync as c
    assert c("show me my leads")["tier"] == "query"


# ---------- Chat classify_intent LOOP_PHASE strip ----------
def test_chat_strips_loop_phase_prefix_source():
    src = open("/app/backend/routers/chat.py", encoding="utf-8").read()
    # Confirm strip regex present near classify_intent call
    assert 'LOOP_PHASE:' in src
    assert 'r"^LOOP_PHASE:\\w+\\s*\\n"' in src
    # And it wraps body.prompt before classify call
    idx = src.find('_intent_probe_text = re.sub')
    idx_classify = src.find('_classify_intent(', idx)
    assert 0 < idx < idx_classify

def test_chat_strip_regex_behavior():
    import re
    stripped = re.sub(r"^LOOP_PHASE:\w+\s*\n", "", "LOOP_PHASE:plan\nShip a change", count=1)
    assert stripped == "Ship a change"


# ---------- CI raw-exception-leak lint ----------
def test_raw_exception_leak_lint_clean():
    import subprocess
    r = subprocess.run(
        ["python3", "scripts/ci_check_raw_exception_leak.py"],
        cwd="/app/backend", capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK — no raw-exception-leak violations found." in r.stdout

def test_raw_exception_leak_lint_flags_synthetic(tmp_path):
    import subprocess, textwrap, shutil, os as _os
    scratch = "/app/backend/routers/_scratch_leak_test"
    _os.makedirs(scratch, exist_ok=True)
    try:
        p = _os.path.join(scratch, "scratch.py")
        with open(p, "w") as f:
            f.write(textwrap.dedent("""
                async def _log(a,b,c): pass
                async def do_it(t):
                    try: pass
                    except Exception as e:
                        await _log(t, f'boom {e}', 'error')
            """))
        r = subprocess.run(
            ["python3", "scripts/ci_check_raw_exception_leak.py"],
            cwd="/app/backend", capture_output=True, text=True,
        )
        assert r.returncode == 1, r.stdout
        assert "raw-exception-leak violation" in r.stdout
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

def test_raw_exception_leak_lint_ignores_founder_alert():
    """send_founder_alert with f'{e}' should NOT be flagged (intentional)."""
    src = open("/app/backend/services/loop_engine.py", encoding="utf-8").read()
    # sanity: file contains send_founder_alert
    assert "send_founder_alert" in src
    # And the lint stays clean — covered by test_raw_exception_leak_lint_clean.


# ---------- CI coverage ratchet output ----------
def test_coverage_ratchet_prints_tiered_floor_and_diff_coverage():
    import subprocess
    r = subprocess.run(
        ["python3", "backend/scripts/ci_check_coverage_ratchet.py",
         "a260adb~1", "a260adb", "backend/coverage.json"],
        cwd="/app", capture_output=True, text=True,
    )
    out = r.stdout + r.stderr
    assert "HIGH-RISK" in out, out
    assert "diff-coverage:" in out, out
    # Must have parsed added lines (>0) for chat.py
    assert "new statement line(s)" in out


# ---------- DORA metrics endpoint ----------
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


def _seed_dora_data():
    """Insert 2 deploy_events + 1 rollback + 1 resolved incident.
    Returns (mongo_ids_dict, cleanup_fn)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]

    async def _do():
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        now = datetime.now(timezone.utc)
        d1_ts = (now - timedelta(hours=6)).isoformat()
        d2_ts = (now - timedelta(hours=3)).isoformat()
        d1_commit = (now - timedelta(hours=8)).isoformat()  # 2h lead time
        d2_commit = (now - timedelta(hours=5)).isoformat()  # 2h lead time
        d1 = {"tag": "TEST_dora_1", "env": "production",
              "timestamp": d1_ts, "commit_timestamp": d1_commit}
        d2 = {"tag": "TEST_dora_2", "env": "production",
              "timestamp": d2_ts, "commit_timestamp": d2_commit}
        await db.deploy_events.insert_many([d1, d2])
        # rollback within 24h window of d1
        rb_ts = (now - timedelta(hours=5)).isoformat()
        await db.rollback_attempts.insert_one({"tag": "TEST_dora_rb", "timestamp": rb_ts})
        # resolved incident with mttr
        inc_ts = (now - timedelta(hours=4)).isoformat()
        res_ts = (now - timedelta(hours=3)).isoformat()
        await db.incidents.insert_one({
            "tag": "TEST_dora_inc", "status": "resolved",
            "detected_at_iso": inc_ts, "resolved_at_iso": res_ts,
            "mttr_s": 3600,  # 1 hour
        })
        client.close()
    asyncio.run(_do())

    def cleanup():
        async def _cl():
            client = AsyncIOMotorClient(mongo_url)
            db = client[db_name]
            await db.deploy_events.delete_many({"tag": {"$in": ["TEST_dora_1", "TEST_dora_2"]}})
            await db.rollback_attempts.delete_many({"tag": "TEST_dora_rb"})
            await db.incidents.delete_many({"tag": "TEST_dora_inc"})
            client.close()
        asyncio.run(_cl())
    return cleanup


def test_dora_metrics_endpoint(admin_session):
    cleanup = _seed_dora_data()
    try:
        r = admin_session.get(
            f"{BASE_URL}/api/aurem-dev/admin/insights/dora"
            "?period_days=30&env=production", timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
        data = r.json()
        for k in ("deployment_frequency", "lead_time_for_changes",
                  "change_failure_rate", "mttr"):
            assert k in data, data
        # Basic sanity: at least our 2 seeded deploys, both flagged as failed.
        assert data["deployment_frequency"]["count"] >= 2
        assert data["change_failure_rate"]["total_deploys"] >= 2
        assert data["change_failure_rate"]["failed_deploys"] >= 1
        assert data["mttr"]["sample_size"] >= 1
    finally:
        cleanup()


def test_dora_metrics_admin_only():
    r = requests.get(
        f"{BASE_URL}/api/aurem-dev/admin/insights/dora?period_days=30",
        timeout=15,
    )
    assert r.status_code in (401, 403), r.status_code
