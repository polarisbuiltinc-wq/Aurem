"""Iter 363 — Guard 20 (automated postmortem/incident log) locks.

Charter locks: forced alert from ANY guard → incident entry
auto-created with guard linkage; auto-resolve fills resolution +
MTTR; QA tab shows open count + MTTR (30d); endpoint founder-gated.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import incident_log as il
from tests.test_iter362_guard19_recovery import FakeDB
from services import process_recovery as pr


class TestOpenResolve:
    @pytest.mark.asyncio
    async def test_open_creates_entry_with_guard_linkage(self):
        db = FakeDB()
        db.incidents = _Coll()
        inc = await il.open_incident(
            db, guard="G7", source_key="integration:stripe",
            title="Stripe down", detail="502 from Stripe")
        assert inc["guard"] == "G7"
        assert inc["status"] == "open"
        assert inc["source_key"] == "integration:stripe"
        assert inc["resolved_at"] is None

    @pytest.mark.asyncio
    async def test_open_is_deduped(self):
        db = FakeDB(); db.incidents = _Coll()
        await il.open_incident(db, guard="G7", source_key="k1",
                               title="x", detail="y")
        await il.open_incident(db, guard="G7", source_key="k1",
                               title="x", detail="y")
        rows = await il.list_incidents(db, status="open")
        assert len(rows) == 1
        assert rows[0]["recurrence"] == 1

    @pytest.mark.asyncio
    async def test_resolve_fills_resolution_and_mttr(self):
        db = FakeDB(); db.incidents = _Coll()
        inc = await il.open_incident(db, guard="G19", source_key="k2",
                                     title="loop", detail="d")
        # backdate detection so MTTR is measurable
        db.incidents.docs[0]["detected_at"] -= 120
        ok = await il.resolve_incident(db, source_key="k2",
                                       resolution="stable", root_cause="deploy")
        assert ok is True
        row = (await il.list_incidents(db, status="resolved"))[0]
        assert row["status"] == "resolved"
        assert row["resolution"] == "stable"
        assert row["root_cause"] == "deploy"
        assert row["mttr_s"] >= 120

    @pytest.mark.asyncio
    async def test_resolve_noop_when_no_open(self):
        db = FakeDB(); db.incidents = _Coll()
        assert await il.resolve_incident(db, source_key="ghost",
                                         resolution="x") is False


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_open_and_mttr(self):
        db = FakeDB(); db.incidents = _Coll()
        await il.open_incident(db, guard="G7", source_key="a",
                               title="t", detail="d")
        await il.open_incident(db, guard="G19", source_key="b",
                               title="t", detail="d")
        db.incidents.docs[1]["detected_at"] -= 60
        await il.resolve_incident(db, source_key="b", resolution="r")
        st = await il.incident_stats(db)
        assert st["open"] == 1
        assert st["resolved_30d"] == 1
        assert st["mttr_30d_s"] >= 60
        assert st["total"] == 2


class TestGuardHooks:
    @pytest.mark.asyncio
    async def test_g19_loop_trip_opens_incident(self, monkeypatch):
        monkeypatch.setattr(pr, "LOOP_THRESHOLD", 3)
        db = FakeDB(); db.incidents = _Coll()
        for _ in range(3):
            await pr.record_boot(db, reason="crash")
        rows = await il.list_incidents(db, status="open")
        assert any(r["guard"] == "G19"
                   and r["source_key"] == "process_recovery" for r in rows)

    @pytest.mark.asyncio
    async def test_integration_critical_alert_opens_incident(self):
        from services.topup_alerts import upsert_alerts_from_snapshot

        class _DB:
            pass
        db = _DB()
        db.topup_alerts = _Coll()
        db.incidents = _Coll()
        snap = {"generated_at": __import__("time").time(),
                "results": [{"id": "tavily", "name": "Tavily Search",
                             "status": "broken",
                             "summary": "Credits exhausted (432)",
                             "detail": "HTTP 432", "fix_hint": "top up"}]}
        await upsert_alerts_from_snapshot(db, snap)
        rows = await il.list_incidents(db, status="open")
        assert any(r["source_key"] == "integration:tavily"
                   and r["guard"] == "integration_health" for r in rows)


class TestEndpoint:
    def test_endpoint_registered_and_admin_gated(self):
        from routers.admin_qa import router
        paths = [r.path for r in router.routes]
        assert "/admin/qa/guard20-incidents" in paths
        assert any(d.dependency.__name__ == "require_admin_dep"
                   for d in router.dependencies)


# ── minimal in-memory collection with .find().sort().limit().to_list() ──
class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction):
        self._docs = sorted(self._docs, key=lambda d: d.get(key, 0),
                            reverse=direction < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, n):
        return [dict(d) for d in self._docs[:n]]

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]
        self._i += 1
        return d


class _Coll:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": len(self.docs)})()

    async def count_documents(self, query):
        return sum(1 for d in self.docs if self._match(d, query))

    async def bulk_write(self, ops, ordered=False):
        from pymongo import InsertOne, UpdateMany, UpdateOne
        for op in ops:
            if isinstance(op, InsertOne):
                self.docs.append(dict(op._doc))
            elif isinstance(op, (UpdateOne, UpdateMany)):
                flt, upd = op._filter, op._doc
                many = isinstance(op, UpdateMany)
                for d in self.docs:
                    if self._match(d, flt):
                        for k, v in upd.get("$set", {}).items():
                            d[k] = v
                        for k, v in upd.get("$inc", {}).items():
                            d[k] = d.get(k, 0) + v
                        if not many:
                            break
        return type("R", (), {})()

    async def find_one(self, query, proj=None, sort=None):
        rows = [d for d in self.docs if self._match(d, query)]
        return dict(rows[0]) if rows else None

    def find(self, query, proj=None):
        return _Cursor([d for d in self.docs if self._match(d, query)])

    async def update_one(self, query, update):
        for d in self.docs:
            if self._match(d, query):
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                for k, v in update.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + v
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()

    @staticmethod
    def _match(d, query):
        for k, cond in query.items():
            v = d.get(k)
            if isinstance(cond, dict):
                if "$gte" in cond and not (v is not None and v >= cond["$gte"]):
                    return False
                if "$ne" in cond and v == cond["$ne"]:
                    return False
                if "$in" in cond and v not in cond["$in"]:
                    return False
            elif v != cond:
                return False
        return True
