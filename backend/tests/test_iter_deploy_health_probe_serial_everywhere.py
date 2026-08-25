"""2026-08-26 deploy fix — every LIVE app code path that refreshes the
integration-health snapshot must use `run_all_probes_serial()`, never
the concurrent `run_all_probes()`.

Root cause (CONFIRMED from live prod deploy logs): the concurrent
13-probe burst (Stripe, e2b sandbox boot, LLM completion, TLS x11...)
starves the single-worker event loop via GIL contention, causing
nginx `upstream timed out ... GET /health` errors and failing the
Kubernetes deployment health check. Iter 336b already fixed the
periodic cron (`integration_health_cron.py`) but THREE other call
sites still used the unsafe concurrent variant: the admin cold-start
path (`GET /admin/integrations/health` when no snapshot exists yet —
exactly the state a fresh deploy is in), the manual refresh endpoint
(`POST /admin/integrations/refresh`), and the daily digest job.
"""
from pathlib import Path

ADMIN_OPS_SRC = Path("/app/backend/routers/admin_ops_config.py").read_text()
DIGEST_SRC = Path("/app/backend/services/daily_digest.py").read_text()


def test_admin_cold_start_path_uses_serial_probes():
    seg = ADMIN_OPS_SRC.split('@router.get("/integrations/health")')[1]
    seg = seg.split("@router.")[0]
    assert "run_all_probes_serial" in seg
    assert "await run_all_probes()" not in seg


def test_admin_manual_refresh_uses_serial_probes():
    seg = ADMIN_OPS_SRC.split('@router.post("/integrations/refresh")')[1]
    seg = seg.split("@router.")[0]
    assert "run_all_probes_serial" in seg
    assert "await run_all_probes()" not in seg


def test_daily_digest_uses_serial_probes():
    seg = DIGEST_SRC.split("auto-refresh the integration-health snapshot")[1]
    seg = seg[:1000]
    assert "run_all_probes_serial" in seg
    assert "await run_all_probes()" not in seg
