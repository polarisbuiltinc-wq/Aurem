"""Iter 362 — Guard 19 (process auto-recovery) regression locks.

Charter locks: forced crash → restart confirmed (supervisor
autorestart, verified from pod conf); restart-loop threshold trips a
CRITICAL alert in the EXISTING topup_alerts banner; QA row exposes
restarts (7d) / last reason / loop trips; heartbeat surfaced on
/api/healthz.
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import process_recovery as pr


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs


class FakeCollection:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": len(self.docs)})()

    async def count_documents(self, query):
        return sum(1 for d in self.docs if self._match(d, query))

    async def find_one(self, query, proj=None, sort=None):
        rows = [d for d in self.docs if self._match(d, query)]
        if sort:
            key, direction = sort[0]
            rows.sort(key=lambda d: d.get(key, 0), reverse=direction < 0)
        return rows[0] if rows else None

    async def update_one(self, query, update):
        n = 0
        for d in self.docs:
            if self._match(d, query):
                self._apply(d, update)
                n = 1
                break
        return type("R", (), {"modified_count": n})()

    async def update_many(self, query, update):
        n = 0
        for d in self.docs:
            if self._match(d, query):
                self._apply(d, update)
                n += 1
        return type("R", (), {"modified_count": n})()

    @staticmethod
    def _apply(d, update):
        for k, v in update.get("$set", {}).items():
            d[k] = v
        for k, v in update.get("$inc", {}).items():
            d[k] = d.get(k, 0) + v

    @staticmethod
    def _match(d, query):
        for k, cond in query.items():
            v = d.get(k)
            if isinstance(cond, dict):
                if "$gte" in cond and not (v is not None and v >= cond["$gte"]):
                    return False
            elif v != cond:
                return False
        return True


class FakeDB:
    def __init__(self):
        self.process_boots = FakeCollection()
        self.process_loop_trips = FakeCollection()
        self.topup_alerts = FakeCollection()


class TestSupervisorConfig:
    def test_pod_supervisor_has_autorestart(self):
        """The pod's supervisor conf (read-only) must keep backend on
        autorestart=true — the OS-level half of G19."""
        import glob
        confs = glob.glob("/etc/supervisor/conf.d/*.conf")
        text = "".join(open(c).read() for c in confs)
        assert "[program:backend]" in text
        seg = text.split("[program:backend]")[1].split("[program:")[0]
        assert "autorestart=true" in seg


class TestHeartbeat:
    def test_beat_updates_age(self):
        pr.beat()
        assert pr.heartbeat_age_s() < 1.0
        assert pr.last_heartbeat_iso().endswith("+00:00")


class TestBootRecording:
    @pytest.mark.asyncio
    async def test_single_boot_records_no_loop(self):
        db = FakeDB()
        out = await pr.record_boot(db, reason="test_boot")
        assert out["recorded"] is True
        assert out["boots_in_window"] == 1
        assert out["loop_detected"] is False
        assert len(db.process_boots.docs) == 1

    @pytest.mark.asyncio
    async def test_restart_loop_trips_critical_alert(self, monkeypatch):
        monkeypatch.setattr(pr, "LOOP_THRESHOLD", 3)
        monkeypatch.setattr(pr, "LOOP_WINDOW_S", 600)
        db = FakeDB()
        out = None
        for _ in range(3):
            out = await pr.record_boot(db, reason="crash")
        assert out["loop_detected"] is True
        # loop-trip logged
        assert len(db.process_loop_trips.docs) == 1
        # CRITICAL alert raised in the EXISTING banner collection
        alert = await db.topup_alerts.find_one(
            {"integration_id": "process_recovery", "status": "active"})
        assert alert is not None
        assert alert["severity"] == "critical"
        assert "Restart loop" in alert["summary"]

    @pytest.mark.asyncio
    async def test_loop_alert_deduped_not_duplicated(self, monkeypatch):
        monkeypatch.setattr(pr, "LOOP_THRESHOLD", 3)
        db = FakeDB()
        for _ in range(5):
            await pr.record_boot(db, reason="crash")
        active = [d for d in db.topup_alerts.docs
                  if d.get("integration_id") == "process_recovery"
                  and d.get("status") == "active"]
        assert len(active) == 1

    @pytest.mark.asyncio
    async def test_db_none_is_safe_noop(self):
        out = await pr.record_boot(None, reason="x")
        assert out["recorded"] is False


class TestResolveStable:
    @pytest.mark.asyncio
    async def test_resolves_when_boots_settle(self, monkeypatch):
        monkeypatch.setattr(pr, "LOOP_THRESHOLD", 3)
        monkeypatch.setattr(pr, "LOOP_WINDOW_S", 600)
        db = FakeDB()
        for _ in range(3):
            await pr.record_boot(db, reason="crash")
        # Age out every boot beyond the window.
        for d in db.process_boots.docs:
            d["ts"] -= 10_000
        resolved = await pr.resolve_if_stable(db)
        assert resolved is True
        active = await db.topup_alerts.find_one(
            {"integration_id": "process_recovery", "status": "active"})
        assert active is None


class TestQARow:
    @pytest.mark.asyncio
    async def test_recovery_status_shape(self):
        db = FakeDB()
        await pr.record_boot(db, reason="supervisor_start")
        st = await pr.recovery_status(db)
        assert st["guard"] == "G19"
        assert st["supervisor_autorestart"] is True
        assert st["restarts_7d"] == 1
        assert st["last_boot"]["reason"] == "supervisor_start"
        assert st["loop_trips_7d"] == 0
        assert "heartbeat_age_s" in st

    def test_endpoint_registered_and_admin_gated(self):
        from routers.admin_qa import router
        paths = [r.path for r in router.routes]
        assert "/admin/qa/guard19-recovery" in paths
        assert any(d.dependency.__name__ == "require_admin_dep"
                   for d in router.dependencies)


class TestHealthzHeartbeat:
    @pytest.mark.asyncio
    async def test_healthz_exposes_heartbeat(self):
        import main
        out = await main.healthz()
        assert out["ok"] is True
        assert "last_cron_heartbeat" in out
        assert "heartbeat_age_s" in out
