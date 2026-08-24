"""Phase 2c coverage wave — backend/routers/admin_analytics.py (2026-08-23).

Same in-process TestClient approach as
test_phase2c_codebase_health_router.py (see that file's docstring for
the root-cause writeup on why `requests`-against-live-server tests
don't move pytest-cov's coverage.json).

This file additionally ships a small generic Mongo-aggregation
interpreter (`_run_aggregate`) because most of this router's 42
endpoints are dashboard aggregations — a purely empty-passthrough
fake (fine for codebase_health.py) would leave nearly every
processing branch in this file uncovered (0 rows in -> loop bodies
never execute). It supports $match/$project($subtract only)/$group
($sum/$avg/$max/$min/$push/$addToSet/$cond)/$sort/$limit/$count —
covers every pipeline shape actually used in this file. It is a
coverage tool, not a correctness oracle: exact grouping math is not
asserted anywhere here (documented as a known, scoped gap, same
posture as codebase_health.py's scanner_feedback wave).
"""
from __future__ import annotations

import re as _re
import time
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch


# ═════════════════════════════════════════════════════════════════════
# Mini Mongo-aggregation interpreter (coverage tool, not an oracle)
# ═════════════════════════════════════════════════════════════════════

def _resolve(doc, expr):
    if isinstance(expr, str) and expr.startswith("$"):
        return doc.get(expr[1:])
    if isinstance(expr, dict):
        if "$subtract" in expr:
            a, b = expr["$subtract"]
            av, bv = _resolve(doc, a), _resolve(doc, b)
            return (av - bv) if (av is not None and bv is not None) else None
        # Unsupported nested expression (e.g. $max/$map combo) — treated
        # as missing rather than raw-copied, so downstream $avg/$sum
        # accumulators skip it cleanly instead of summing a dict.
        return None
    return expr


def _eval_bool(doc, expr):
    if isinstance(expr, str) and expr.startswith("$"):
        return bool(_resolve(doc, expr))
    if isinstance(expr, dict) and "$eq" in expr:
        a, b = expr["$eq"]
        bv = _resolve(doc, b) if isinstance(b, str) and b.startswith("$") else b
        return _resolve(doc, a) == bv
    return bool(expr)


def _matches(doc, query):
    for k, v in (query or {}).items():
        if isinstance(v, dict):
            if "$gte" in v and not (doc.get(k) is not None and doc[k] >= v["$gte"]):
                return False
            if "$gt" in v and not (doc.get(k) is not None and doc[k] > v["$gt"]):
                return False
            if "$lt" in v and not (doc.get(k) is not None and doc[k] < v["$lt"]):
                return False
            if "$lte" in v and not (doc.get(k) is not None and doc[k] <= v["$lte"]):
                return False
            if "$ne" in v and doc.get(k) == v["$ne"]:
                return False
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
            if "$exists" in v and (k in doc) != v["$exists"]:
                return False
            if "$regex" in v and not _re.search(v["$regex"], doc.get(k) or ""):
                return False
            continue
        if doc.get(k) != v:
            return False
    return True


def _run_aggregate(rows, pipeline):
    docs = [dict(r) for r in rows]
    for stage in pipeline:
        if "$match" in stage:
            docs = [d for d in docs if _matches(d, stage["$match"])]
        elif "$project" in stage:
            out = []
            for d in docs:
                nd = dict(d)
                for k, v in stage["$project"].items():
                    if k == "_id" and v == 0:
                        continue
                    nd[k] = _resolve(d, v) if isinstance(v, (str, dict)) else v
                out.append(nd)
            docs = out
        elif "$group" in stage:
            grp = stage["$group"]
            gid_expr = grp["_id"]
            buckets: dict = {}
            order: list = []
            for d in docs:
                key = _resolve(d, gid_expr) if gid_expr is not None else None
                if key not in buckets:
                    buckets[key] = []
                    order.append(key)
                buckets[key].append(d)
            out = []
            for key in order:
                gdocs = buckets[key]
                row_out = {"_id": key}
                for fname, acc in grp.items():
                    if fname == "_id":
                        continue
                    if "$sum" in acc:
                        expr = acc["$sum"]
                        if expr == 1:
                            row_out[fname] = len(gdocs)
                        elif isinstance(expr, dict) and "$cond" in expr:
                            cond = expr["$cond"]
                            row_out[fname] = sum(
                                (cond[1] if _eval_bool(d, cond[0]) else cond[2])
                                for d in gdocs
                            )
                        else:
                            row_out[fname] = sum((_resolve(d, expr) or 0) for d in gdocs)
                    elif "$avg" in acc:
                        vals = [v for v in (_resolve(d, acc["$avg"]) for d in gdocs) if v is not None]
                        row_out[fname] = (sum(vals) / len(vals)) if vals else None
                    elif "$max" in acc:
                        vals = [v for v in (_resolve(d, acc["$max"]) for d in gdocs) if v is not None]
                        row_out[fname] = max(vals) if vals else None
                    elif "$min" in acc:
                        vals = [v for v in (_resolve(d, acc["$min"]) for d in gdocs) if v is not None]
                        row_out[fname] = min(vals) if vals else None
                    elif "$push" in acc:
                        row_out[fname] = [_resolve(d, acc["$push"]) for d in gdocs]
                    elif "$addToSet" in acc:
                        row_out[fname] = list({_resolve(d, acc["$addToSet"]) for d in gdocs})
                out.append(row_out)
            docs = out
        elif "$sort" in stage:
            for k, direction in reversed(list(stage["$sort"].items())):
                docs.sort(key=lambda d: (d.get(k) is None, d.get(k)), reverse=(direction < 0))
        elif "$limit" in stage:
            docs = docs[: stage["$limit"]]
        elif "$count" in stage:
            docs = [{stage["$count"]: len(docs)}]
    return docs


# ═════════════════════════════════════════════════════════════════════
# Fakes
# ═════════════════════════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    async def __aiter__(self):
        for r in self._rows:
            yield r

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        return _matches(row, query)

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def count_documents(self, query=None):
        return sum(1 for r in self.rows if self._match(r, query))

    async def delete_many(self, query=None):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not self._match(r, query)]
        import types
        return types.SimpleNamespace(deleted_count=before - len(self.rows))

    async def estimated_document_count(self):
        return len(self.rows)

    async def distinct(self, field, query=None):
        vals = {r.get(field) for r in self.rows if self._match(r, query) and r.get(field) is not None}
        return list(vals)

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [r for r in self.rows if self._match(r, query)]
        if sort:
            key, direction = sort[0]
            matched.sort(key=lambda r: (r.get(key) is None, r.get(key, 0)), reverse=(direction < 0))
        return dict(matched[0]) if matched else None

    def find(self, query=None, projection=None, sort=None, limit=None):
        matched = [dict(r) for r in self.rows if self._match(r, query)]
        if sort:
            for key, direction in reversed(list(sort)):
                matched.sort(key=lambda r: (r.get(key) is None, r.get(key, 0)), reverse=(direction < 0))
        if limit:
            matched = matched[:limit]
        return _FakeCursor(matched)

    def aggregate(self, pipeline):
        return _FakeCursor(_run_aggregate(self.rows, pipeline))


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]

    def __getitem__(self, name):
        return self.__getattr__(name)

    async def list_collection_names(self):
        return list(object.__getattribute__(self, "_cols").keys())


ADMIN_USER = {"user_id": "admin-1", "is_admin": True, "tier": "founder",
             "email": "founder@aurem.dev", "is_unlimited": True}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def client(fake_db):
    from routers import admin_analytics as router_mod
    from cto_services import db as _dbmod
    from fastapi import Header
    _dbmod.set_db(fake_db)

    async def _fake_require_admin_dep(authorization: str = Header(None)):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return ADMIN_USER

    app = FastAPI()
    app.dependency_overrides[router_mod.require_admin_dep] = _fake_require_admin_dep
    app.include_router(router_mod.router, prefix="/api/aurem-dev")

    with patch("routers._admin_common.current_dev", AsyncMock(return_value=ADMIN_USER)):
        c = TestClient(app)
        yield c

    _dbmod.set_db(None)
    app.dependency_overrides.clear()


AUTH = {"Authorization": "Bearer admin-1"}


# ═════════════════════════════════════════════════════════════════════
# Router-level auth gate — spot-check a couple of endpoints; the
# Depends(require_admin_dep) gate is shared by all 42, so this proves
# the gate itself, not each individual route.
# ═════════════════════════════════════════════════════════════════════

class TestAuthGate:
    def test_dashboard_unauthenticated_401(self, client):
        r = client.get("/api/aurem-dev/admin/dashboard")
        assert r.status_code == 401

    def test_pulse_unauthenticated_401(self, client):
        r = client.get("/api/aurem-dev/admin/pulse")
        assert r.status_code == 401


# ═════════════════════════════════════════════════════════════════════
# Simple dashboards
# ═════════════════════════════════════════════════════════════════════

class TestDashboardAndAudit:
    def test_cleanup_e2e_sessions(self, client, fake_db):
        fake_db.chat_sessions.rows.append({"session_id": "e2e_abc"})
        r = client.post("/api/aurem-dev/admin/qa/cleanup-e2e-sessions", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_audit_feed(self, client):
        with patch("services.audit_log.list_turns", AsyncMock(return_value=[{"turn": 1}])):
            r = client.get("/api/aurem-dev/admin/audit", headers=AUTH, params={"limit": 10})
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_dashboard(self, client, fake_db):
        fake_db.dev_users.rows.append({"user_id": "u1"})
        fake_db.cto_tasks.rows.append({"status": "done", "created_at": time.time()})
        fake_db.cto_projects.rows.append({"project_id": "p1"})
        fake_db.chat_sessions.rows.append({"session_id": "s1"})
        r = client.get("/api/aurem-dev/admin/dashboard", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["total_users"] == 1
        assert body["success_rate"] == 100.0


class TestPulse:
    def test_pulse_success(self, client, fake_db):
        now = time.time()
        fake_db.dev_users.rows.append({
            "user_id": "u1", "tier": "pro", "created_at": now,
            "github": {"access_token": "tok"},
        })
        r = client.get("/api/aurem-dev/admin/pulse", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert "raw" in body and "organic" in body
        assert body["raw"]["total_users"] == 1

    def test_pulse_timeout_returns_504(self, client, fake_db):
        import asyncio as _aio
        with patch("asyncio.wait_for", AsyncMock(side_effect=_aio.TimeoutError)):
            r = client.get("/api/aurem-dev/admin/pulse", headers=AUTH)
        assert r.status_code == 504


class TestSystemStats:
    def test_system_stats_empty(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/system-stats", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "parliament" in body and "intent_gateway" in body

    def test_system_stats_with_data(self, client, fake_db):
        now = time.time()
        fake_db.parliament_log.rows.append({
            "event": "aggregate", "status": "success", "council": "A",
            "winner": "A1-conservative", "ts": now, "scores": [{"score": 0.9}],
        })
        fake_db.intent_classifications.rows.append({
            "tier": "agentic", "confidence": 0.8, "method": "llm", "ts": now,
        })
        fake_db.quality_scores.rows.append({"timestamp_ts": now, "score": 0.9})
        r = client.get("/api/aurem-dev/admin/system-stats", headers=AUTH,
                       params={"window_hours": 24})
        assert r.status_code == 200
        body = r.json()
        assert body["parliament"]["total_runs"] == 1
        assert body["intent_gateway"]["tier_distribution"]["agentic"] == 1


class TestCouncil:
    def test_council_stats(self, client, fake_db):
        fake_db.ora_council_logs.rows.append({"mode": "C", "claude_corrected": True})
        r = client.get("/api/aurem-dev/admin/council/stats", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["mode_c"] == 1

    def test_council_health(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/council/health", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["council"] == "A"

    def test_council_health_alias(self, client):
        r = client.get("/api/aurem-dev/admin/council-health", headers=AUTH)
        assert r.status_code == 200

    def test_council_reprobe_success(self, client):
        import routers.admin_analytics as router_mod
        router_mod._COUNCIL_REPROBE_LAST_AT = 0.0
        with patch("services.llm.probe_longcat_availability", AsyncMock(return_value=True)):
            r = client.post("/api/aurem-dev/admin/council/reprobe", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_council_reprobe_throttled(self, client):
        import routers.admin_analytics as router_mod
        router_mod._COUNCIL_REPROBE_LAST_AT = time.time()
        r = client.post("/api/aurem-dev/admin/council/reprobe", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["throttled"] is True


class TestOraLearning:
    def test_weekly_summary(self, client, fake_db):
        fake_db.ora_learning_logs.rows.append({"reason": "low_confidence", "ts": time.time()})
        r = client.get("/api/aurem-dev/admin/ora-learning/weekly-summary", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["total"] == 1


class TestTokenPnl:
    def test_token_pnl(self, client, fake_db):
        now = time.time()
        fake_db.cto_tasks.rows.append({
            "created_at": now, "status": "done", "agent_used": "deepseek", "tokens_used": 1000,
        })
        fake_db.cto_payments.rows.append({
            "created_at": now, "payment_status": "paid", "amount": 50.0,
        })
        r = client.get("/api/aurem-dev/admin/token-pnl", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["revenue_month"] == 50.0
        assert body["month_by_agent"]["deepseek"] == 1000


class TestAgentTokens:
    def test_agent_tokens_7d(self, client, fake_db):
        now = time.time()
        fake_db.cto_tasks.rows.append({
            "created_at": now, "status": "done", "agent_used": "claude",
            "tokens_used": 500, "claude_corrected": True,
        })
        r = client.get("/api/aurem-dev/admin/agent-tokens", headers=AUTH, params={"range": "7d"})
        assert r.status_code == 200
        body = r.json()
        assert body["totals_tokens"]["claude"] == 500
        assert body["claude_corrections"] == 1

    def test_agent_tokens_invalid_range_falls_back(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/agent-tokens", headers=AUTH, params={"range": "bogus"})
        assert r.status_code == 200
        assert r.json()["range"] == "7d"


class TestDigestAndLearningHealth:
    def test_digest(self, client):
        with patch("services.daily_digest.build_digest", AsyncMock(return_value={"ok": True})):
            r = client.get("/api/aurem-dev/admin/digest", headers=AUTH)
        assert r.status_code == 200

    def test_learning_health_empty(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/learning-health", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["status"] == "empty"

    def test_learning_health_green(self, client, fake_db):
        fake_db.project_brains.rows.append({
            "project_id": "p1", "updated_at": datetime.now(timezone.utc),
        })
        r = client.get("/api/aurem-dev/admin/learning-health", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["status"] == "green"


class TestOraCouncilAliases:
    def test_ora_stats(self, client):
        with patch("services.ora_council_logger.get_council_stats",
                  AsyncMock(return_value={"total": 5})):
            r = client.get("/api/aurem-dev/admin/ora/stats", headers=AUTH)
        assert r.status_code == 200

    def test_ora_stats_v2(self, client):
        with patch("services.ora_council_logger.get_council_stats",
                  AsyncMock(return_value={"total": 5})):
            r = client.get("/api/aurem-dev/admin/ora-stats", headers=AUTH)
        assert r.status_code == 200

    def test_ora_export(self, client):
        with patch("services.ora_council_logger.export_daily_jsonl",
                  AsyncMock(return_value={"exported": 3})):
            r = client.post("/api/aurem-dev/admin/ora/export", headers=AUTH)
        assert r.status_code == 200


class TestSkills:
    def test_web_search(self, client):
        with patch("routers.admin_analytics._run_skill", AsyncMock(return_value={"ok": True})):
            r = client.post("/api/aurem-dev/admin/skills/web-search", headers=AUTH,
                            json={"query": "hi"})
        assert r.status_code == 200

    def test_fetch_url(self, client):
        with patch("routers.admin_analytics._run_skill", AsyncMock(return_value={"ok": True})):
            r = client.post("/api/aurem-dev/admin/skills/fetch-url", headers=AUTH,
                            json={"url": "https://x.com"})
        assert r.status_code == 200

    def test_search_and_summarize(self, client):
        with patch("routers.admin_analytics._run_skill", AsyncMock(return_value={"ok": True})):
            r = client.post("/api/aurem-dev/admin/skills/search-and-summarize", headers=AUTH,
                            json={"query": "hi"})
        assert r.status_code == 200

    def test_firecrawl_scrape(self, client):
        with patch("routers.admin_analytics._run_skill", AsyncMock(return_value={"ok": True})):
            r = client.post("/api/aurem-dev/admin/skills/firecrawl-scrape", headers=AUTH,
                            json={"url": "https://x.com"})
        assert r.status_code == 200

    def test_firecrawl_crawl(self, client):
        with patch("routers.admin_analytics._run_skill", AsyncMock(return_value={"ok": True})):
            r = client.post("/api/aurem-dev/admin/skills/firecrawl-crawl", headers=AUTH,
                            json={"url": "https://x.com"})
        assert r.status_code == 200

    def test_status(self, client):
        r = client.get("/api/aurem-dev/admin/skills/status", headers=AUTH)
        assert r.status_code == 200
        assert "web_search" in r.json()["skills"]


class TestEvalAndMode:
    def test_eval_quality_no_runs(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/eval-quality", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["totals"]["runs"] == 0

    def test_eval_quality_with_runs(self, client, fake_db):
        fake_db.ora_eval_runs.rows.append({
            "ts": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "passed": 9, "total": 10, "hard_fails": 0, "ok": True,
        })
        r = client.get("/api/aurem-dev/admin/eval-quality", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["totals"]["runs"] == 1

    def test_mode_telemetry(self, client, fake_db):
        fake_db.mode_classifications.rows.append({
            "mode": "agentic", "needs_confirm": True, "f12_forced": False,
            "confidence": 0.7, "ts": time.time(),
        })
        r = client.get("/api/aurem-dev/admin/mode-telemetry", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["total"] == 1


class TestProductAnalytics:
    def test_product_analytics(self, client, fake_db):
        now = time.time()
        fake_db.chat_sessions.rows.append({"user_id": "u1", "updated_at": now})
        fake_db.dev_users.rows.append({"user_id": "u1", "tier": "pro", "created_at": now})
        fake_db.cto_tasks.rows.append({
            "created_at": now, "status": "done", "tokens_used": 200, "maxx_mode": True,
        })
        fake_db.ora_council_logs.rows.append({"mode": "C", "ts": now})
        r = client.get("/api/aurem-dev/admin/product-analytics", headers=AUTH,
                       params={"days": 30})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["users"]["dau"] == 1
        assert len(body["trend"]["dau_14d"]) == 14

    def test_product_analytics_clamps_days(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/product-analytics", headers=AUTH,
                       params={"days": 999999})
        assert r.status_code == 200
        assert r.json()["period_days"] == 365


class TestVanguardAndSkillsUsage:
    def test_vanguard_stats(self, client, fake_db):
        with patch("services.vanguard_audit.weekly_stats",
                  AsyncMock(return_value={"total": 0})):
            r = client.get("/api/aurem-dev/admin/vanguard/stats", headers=AUTH)
        assert r.status_code == 200

    def test_vanguard_recent_returns_null(self, client):
        # Existing quirk: handler has no return statement (pre-existing,
        # not introduced by this test wave) — documents current behavior.
        r = client.get("/api/aurem-dev/admin/vanguard/recent", headers=AUTH)
        assert r.status_code == 200
        assert r.json() is None

    def test_skills_usage(self, client, fake_db):
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        fake_db.ora_skill_usage.rows.append({
            "tool": "web_search", "ts": cutoff_iso, "ok": True, "elapsed_ms": 120,
        })
        r = client.get("/api/aurem-dev/admin/skills-usage", headers=AUTH, params={"days": 14})
        assert r.status_code == 200
        body = r.json()
        assert body["total_calls"] == 1
        assert body["skills"][0]["tool"] == "web_search"


class TestOverviewMetrics:
    def test_overview_metrics(self, client, fake_db):
        now = time.time()
        fake_db.cto_tasks.rows.append({
            "user_id": "u1", "created_at": now, "status": "done",
            "finished_at": now + 30, "mode": "pro", "project_id": "p1",
        })
        fake_db.api_keys.rows.append({"last_used_at": now})
        fake_db.warm_start_jobs.rows.append({"created_at": now, "status": "done"})
        fake_db.post_task_scans.rows.append({"created_at": now, "severity": "critical"})
        fake_db.cto_projects.rows.append({"project_id": "p1", "name": "Widgets"})
        fake_db.cto_payments.rows.append({"created_at": now, "payment_status": "paid", "amount": 25.0})
        r = client.get("/api/aurem-dev/admin/overview-metrics", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["active_users_today"] == 1
        assert body["revenue_30d"] == 25.0
        assert body["most_active_project"]["name"] == "Widgets"


class TestMcpWarmGraphAgent:
    def test_mcp_usage(self, client, fake_db):
        fake_db.api_keys.rows.append({
            "user_id": "u1", "key": "sk-abcdef123456", "created_at": time.time(),
        })
        r = client.get("/api/aurem-dev/admin/mcp-usage", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["rows"][0]["key_tail"] == "123456"

    def test_warm_start_stats(self, client, fake_db):
        now = time.time()
        fake_db.warm_start_jobs.rows.append({
            "created_at": now, "status": "done", "finished_at": now + 5,
        })
        r = client.get("/api/aurem-dev/admin/warm-start-stats", headers=AUTH)
        assert r.status_code == 200
        assert "breakdown_7d" in r.json()

    def test_graph_status(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "name": "Widgets", "graph_built_at": time.time(),
        })
        r = client.get("/api/aurem-dev/admin/graph-status", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["rows"][0]["has_graph"] is True

    def test_agent_performance(self, client, fake_db):
        now = time.time()
        # 2026-08-26 — updated for the admin_analytics.py root-cause fix:
        # the endpoint was reading `cto_tasks.model` (a field that never
        # existed on any real cto_tasks doc) and now reads the real
        # per-call usage ledger `customer_chat_cost` instead.
        fake_db.customer_chat_cost.rows.append({
            "ts": now, "model": "claude-sonnet-5",
            "cost_usd": 0.02, "input_tokens": 100, "output_tokens": 50,
        })
        r = client.get("/api/aurem-dev/admin/agent-performance", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["per_model_30d"][0]["model"] == "claude-sonnet-5"


class TestSeoRun:
    def test_seo_run(self, client):
        with patch("services.seo.run_seo_fixes", AsyncMock(return_value={"ok": True})):
            r = client.post("/api/aurem-dev/admin/seo/run", headers=AUTH,
                            json={"project_id": "p1", "dry_run": True})
        assert r.status_code == 200


class TestLoopMetrics:
    def test_loop_metrics(self, client, fake_db):
        now = datetime.now(timezone.utc)
        fake_db.loop_sessions.rows.append({
            "state": "failed", "created_at": now, "user_id": "u1",
            "phase": "ship", "error_summary": "boom",
        })
        fake_db.dev_users.rows.append({"user_id": "u1", "email": "user@example.com"})
        r = client.get("/api/aurem-dev/admin/loop-metrics", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["current"]["failed"] == 1
        assert body["failed_sample"][0]["classification"] == "user"

    def test_loop_token_metrics(self, client, fake_db):
        now = datetime.now(timezone.utc).timestamp()
        fake_db.ora_chat_usage.rows.append({
            "ts": now, "route": "loop.plan", "input_tokens": 100,
            "output_tokens": 50, "cost_usd": 0.01, "session_id": "s1",
        })
        r = client.get("/api/aurem-dev/admin/loop-token-metrics", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["current"]["total_calls"] == 1

    def test_loop_inspect_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/loop-inspect/does-not-exist", headers=AUTH)
        assert r.status_code == 404

    def test_loop_inspect_success(self, client, fake_db):
        fake_db.loop_sessions.rows.append({
            "loop_id": "loop-1", "user_id": "admin-1", "context": {},
        })
        r = client.get("/api/aurem-dev/admin/loop-inspect/loop-1", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["loop_id"] == "loop-1"

    def test_loop_inspect_not_your_loop(self, client, fake_db):
        fake_db.loop_sessions.rows.append({
            "loop_id": "loop-2", "user_id": "someone-else", "context": {},
        })
        with patch("routers._admin_common.current_dev",
                  AsyncMock(return_value={"user_id": "admin-1", "is_admin": True,
                                          "tier": "pro"})):
            r = client.get("/api/aurem-dev/admin/loop-inspect/loop-2", headers=AUTH)
        assert r.status_code == 403

    def test_speed_diagnostic(self, client):
        with patch("services.loop_speed_diagnostic.compute_speed_report",
                  AsyncMock(return_value={"ok": True})):
            r = client.get("/api/aurem-dev/admin/speed-diagnostic", headers=AUTH)
        assert r.status_code == 200

    def test_scope_drift_audit(self, client, fake_db):
        fake_db.loop_events.rows.append({
            "kind": "scope_drift", "ts": datetime.now(timezone.utc).isoformat(),
            "loop_id": "loop-1", "extras": ["extra_file.py"], "frozen": ["a.py"],
        })
        r = client.get("/api/aurem-dev/admin/scope-drift-audit", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["total_drift_events"] == 1
        assert body["distinct_loops"] == 1
