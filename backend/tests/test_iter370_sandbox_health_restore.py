"""
Iter 370 regression — deployment-stability fixes:
  1. sandbox_runner.run_python_check / run_tests_in_sandbox now run e2b
     via asyncio.to_thread (event-loop-blocking fix). Verify return
     shape unchanged and no event-loop starvation under load.
  2. HEAD /api/health now returns 200 (was 405).
  3. db_restore.restore_to_scratch restores into PREFIXED COLLECTIONS
     inside DB_NAME (Atlas-scoped user, no second DB). Verify
     drill-now works + no _restore_scratch_* leftovers.
"""
from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

import pytest
import requests

# Allow importing backend services
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://launch-pad-237.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=20,
    )
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    tok = r.json().get("token") or r.json().get("access_token")
    if not tok:
        pytest.skip(f"No token in login response: {r.json()}")
    return tok


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ================================================================ HEAD / GET /api/health
class TestHealthEndpoint:
    def test_get_health_200(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "status" in data or "ok" in data or "commit_sha" in data

    def test_head_health_200(self):
        # Bug fix: HEAD used to be 405
        r = requests.head(f"{BASE_URL}/api/health", timeout=10, allow_redirects=False)
        assert r.status_code == 200, (
            f"HEAD /api/health returned {r.status_code}; expected 200. "
            f"body={r.text[:200]}"
        )


# ================================================================ sandbox_runner
class TestSandboxRunner:
    """Verify asyncio.to_thread refactor didn't change return shape."""

    def test_run_python_check_shape(self):
        from services.sandbox_runner import run_python_check
        result = asyncio.run(run_python_check("print(2+3)"))
        assert isinstance(result, dict)
        assert "ok" in result
        # if skipped, still ok=True
        if result.get("skipped"):
            pytest.skip(f"Sandbox skipped: {result.get('reason')}")
        # Expected keys per contract
        for key in ("ok", "stdout", "stderr", "exit_code"):
            assert key in result, f"missing key {key} in {result}"
        assert result["ok"] is True
        assert "5" in result["stdout"]
        assert result["exit_code"] == 0

    def test_run_python_check_syntax_error(self):
        from services.sandbox_runner import run_python_check
        result = asyncio.run(run_python_check("def broken(:\n"))
        if result.get("skipped"):
            pytest.skip(f"Sandbox skipped: {result.get('reason')}")
        assert result["ok"] is False
        assert result["exit_code"] == 1
        assert result["stderr"]

    def test_event_loop_not_blocked_by_sandbox(self):
        """While a sandbox check runs, /api/health latency should stay low.
        Before the fix this would spike to seconds / time out."""
        from services.sandbox_runner import run_python_check

        latencies = []
        health_url = f"{BASE_URL}/api/health"
        session = requests.Session()

        async def hammer_health():
            end = time.time() + 8  # up to 8s of polling
            while time.time() < end:
                t0 = time.perf_counter()
                try:
                    resp = await asyncio.to_thread(
                        session.get, health_url, timeout=5,
                    )
                    dt = (time.perf_counter() - t0) * 1000
                    latencies.append((dt, resp.status_code))
                except Exception as e:
                    latencies.append((99999.0, f"ERR:{e!r}"))
                await asyncio.sleep(0.25)

        async def run_both():
            sandbox_task = asyncio.create_task(
                run_python_check("import time; time.sleep(2); print('done')")
            )
            hammer_task = asyncio.create_task(hammer_health())
            sbx_result = await sandbox_task
            # keep hammering a bit after sandbox finishes
            await hammer_task
            return sbx_result

        sbx_result = asyncio.run(run_both())
        if sbx_result.get("skipped"):
            pytest.skip(f"Sandbox skipped: {sbx_result.get('reason')}")

        ok_latencies = [dt for dt, sc in latencies if sc == 200]
        errors = [(dt, sc) for dt, sc in latencies if sc != 200]

        assert len(ok_latencies) >= 5, (
            f"Too few successful health polls: {len(ok_latencies)}, "
            f"errors={errors[:5]}"
        )
        assert not errors, f"Non-200 during sandbox run: {errors[:5]}"

        p50 = statistics.median(ok_latencies)
        p95 = sorted(ok_latencies)[int(len(ok_latencies) * 0.95) - 1]
        max_l = max(ok_latencies)
        print(f"\n  /api/health during sandbox: n={len(ok_latencies)}, "
              f"p50={p50:.0f}ms p95={p95:.0f}ms max={max_l:.0f}ms")
        # Ingress round-trip is ~50ms baseline; blocking bug caused >1000ms.
        assert p95 < 1500, f"p95 latency {p95:.0f}ms suggests event-loop blocking"
        assert max_l < 3000, f"max latency {max_l:.0f}ms suggests event-loop blocking"


# ================================================================ Restore-drill
class TestRestoreDrill:

    def test_drill_now_same_db_no_leftovers(self, admin_headers):
        # Run the drill (5-10s typical)
        r = requests.post(
            f"{BASE_URL}/api/aurem-dev/admin/backups/drill-now",
            headers=admin_headers,
            timeout=180,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "ok" in data
        if not data.get("ok"):
            # Could be no successful backup yet; accept but flag
            pytest.skip(f"drill-now returned ok:false — {data}")

        # Expected shape
        for key in ("ok", "r2_key", "restored_total_docs",
                    "restored_collections", "collection_coverage",
                    "duration_ms"):
            assert key in data, f"missing key {key} in {data}"
        assert data["restored_total_docs"] > 0, data
        assert data["restored_collections"] > 0, data
        assert data["collection_coverage"] >= 0.9, (
            f"low coverage {data['collection_coverage']}: {data}"
        )
        print(f"\n  drill-now: {data['restored_total_docs']} docs / "
              f"{data['restored_collections']} colls, "
              f"coverage={data['collection_coverage']}, "
              f"took={data['duration_ms']}ms")

        # Verify NO leftover _restore_scratch_* collections in DB
        # (fix: prefixed collections should be dropped after drill)
        asyncio.run(self._assert_no_scratch_leftovers())

    async def _assert_no_scratch_leftovers(self):
        # Give a moment for the finally-block cleanup to complete
        await asyncio.sleep(1.0)
        # Load env
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo_url = os.environ["MONGO_URL"]
        db_name = os.environ["DB_NAME"]
        client = AsyncIOMotorClient(mongo_url)
        try:
            names = await client[db_name].list_collection_names()
            leftovers = [n for n in names if n.startswith("_restore_scratch_")]
            assert not leftovers, (
                f"Found leftover scratch collections after drill: {leftovers}"
            )
        finally:
            client.close()

    def test_drill_history_shape(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/backups/drill-history?limit=5",
            headers=admin_headers,
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        for key in ("ok", "history", "last_ok_at", "last_fail_at", "last_result"):
            assert key in data, f"missing {key}: {data}"
        assert data["ok"] is True
        assert isinstance(data["history"], list)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
