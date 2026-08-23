"""Phase 2c coverage wave 2 — backend/routers/cto_projects.py (2026-08-24).

Founder-approved exception (see memory/code_quality_ledger.md):
`_run_task_via_api` (~2432-3524) and `_run_task_with_git` (~3528-3844)
remain scoped out — same posture as wave 1.

This wave targets the remaining small non-exempt gaps left after
wave 1, all inside `_run_warm_agents`:
  * `agent_structure` / `agent_stack` except branches — unreachable
    via their own `asyncio.gather(..., return_exceptions=True)` (the
    gather itself never raises); the branch only fires when the
    POST-gather `db.warm_start_jobs.update_one` write crashes.
  * `agent_graph`'s `if not existing or age > 3600:` / `build_graph`
    call — wave 1's tests always had `get_graph` return `None`, which
    crashes one line earlier at `existing.get("built_at", ...)`
    (AttributeError on None) before ever reaching this branch.
  * `_bounded`'s generic `except Exception` (as opposed to its
    `except asyncio.TimeoutError` sibling, already covered in wave 1).
  * `get_task`'s Personal-Track diff-stripping try/except.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if row.get(k) != v:
                return False
        return True

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [r for r in self.rows if self._match(r, query)]
        return dict(matched[0]) if matched else None

    async def update_one(self, query, update, upsert=False):
        import types
        for r in self.rows:
            if self._match(r, query):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                for k, v in (update.get("$addToSet") or {}).items():
                    arr = r.setdefault(k, [])
                    if v not in arr:
                        arr.append(v)
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    def find(self, query=None, projection=None, sort=None, limit=None):
        matched = [dict(r) for r in self.rows if self._match(r, query)]
        return _FakeCursor(matched)


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


class _FakeResp:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json = json_body or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        pass


USER = {"user_id": "u1", "email": "user@example.com", "tier": "pro",
       "is_admin": False, "is_unlimited": False, "track": "dev",
       "created_at": time.time()}
AUTH = {"Authorization": "Bearer u1"}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def client(fake_db):
    from routers import cto_projects as router_mod
    from cto_services import db as _dbmod
    _dbmod.set_db(fake_db)

    async def _fake_current_dev(authorization=None):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return USER

    old_current_dev = router_mod.current_dev
    router_mod.current_dev = _fake_current_dev

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    c = TestClient(app)
    yield c

    router_mod.current_dev = old_current_dev
    _dbmod.set_db(None)


# ═════════════════════════════════════════════════════════════════════
# _run_warm_agents — structure/stack post-gather crash + graph
# rebuild branch (direct call, no HTTP layer needed)
# ═════════════════════════════════════════════════════════════════════

class TestWarmStartRemainingBranches:
    @pytest.mark.asyncio
    async def test_structure_and_stack_post_gather_write_crash_still_marks_done(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.warm_start_jobs.rows.append({"job_id": "ws1", "agents_done": []})
        _dbmod.set_db(fake_db)
        real_update_one = fake_db.warm_start_jobs.update_one

        async def _flaky_update_one(query, update, upsert=False):
            s = (update or {}).get("$set") or {}
            if "file_tree" in s or "stack_raw" in s:
                raise RuntimeError("mongo write down")
            return await real_update_one(query, update, upsert=upsert)
        fake_db.warm_start_jobs.update_one = _flaky_update_one

        try:
            with patch("services.project_brain.get_brain_v2",
                      AsyncMock(return_value={"last_scan": 0})), \
                 patch("services.project_brain.build_brain_v2",
                      AsyncMock(return_value={})), \
                 patch("httpx.AsyncClient.get",
                      AsyncMock(return_value=_FakeResp(200, []))), \
                 patch("services.project_brain._gh_list_files",
                      AsyncMock(return_value=["a.py"])), \
                 patch("services.project_brain._gh_read_small",
                      AsyncMock(return_value="flask==2.0")), \
                 patch("services.graph_builder.get_graph",
                      AsyncMock(return_value={"built_at": time.time() - 4000})), \
                 patch("services.graph_builder.build_graph",
                      AsyncMock(return_value=None)):
                await m._run_warm_agents(
                    job_id="ws1", project_id="p1", user_id="u1",
                    gh_token="tok", gh_owner="acme", gh_repo="widgets",
                    branch="main", db=fake_db,
                )
        finally:
            _dbmod.set_db(None)
        job = fake_db.warm_start_jobs.rows[0]
        assert set(job["agents_done"]) == {"brain", "recent", "structure", "stack", "graph"}
        assert job["status"] == "ready"
        # stack/structure write crashed → neither field persisted
        assert "file_tree" not in job
        assert "stack_raw" not in job

    @pytest.mark.asyncio
    async def test_bounded_generic_exception_branch_still_marks_done(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.warm_start_jobs.rows.append({"job_id": "ws1", "agents_done": []})
        _dbmod.set_db(fake_db)
        try:
            with patch("asyncio.wait_for", AsyncMock(side_effect=RuntimeError("wait_for boom"))):
                await m._run_warm_agents(
                    job_id="ws1", project_id="p1", user_id="u1",
                    gh_token="tok", gh_owner="acme", gh_repo="widgets",
                    branch="main", db=fake_db,
                )
        finally:
            _dbmod.set_db(None)
        job = fake_db.warm_start_jobs.rows[0]
        assert set(job["agents_done"]) == {"brain", "recent", "structure", "stack", "graph"}
        assert job["status"] == "ready"


# ═════════════════════════════════════════════════════════════════════
# GET /cto/tasks/{id} — Personal-Track diff-stripping crash swallowed
# ═════════════════════════════════════════════════════════════════════

class TestGetTaskTrackCheckCrash:
    def test_track_check_crash_is_swallowed_and_task_still_returned(self, client, fake_db):
        class _WeirdTask(dict):
            def pop(self, *a, **k):
                raise RuntimeError("pop boom")

        class _Coll:
            async def find_one(self, *a, **k):
                return _WeirdTask({"task_id": "t1", "user_id": "u1",
                                   "status": "done", "edited_files": {"x.py": "code"}})
        fake_db._cols["cto_tasks"] = _Coll()

        async def _personal(authorization=None):
            return {**USER, "track": "personal"}
        from routers import cto_projects as router_mod
        router_mod.current_dev = _personal

        r = client.get("/api/aurem-dev/cto/tasks/t1", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["task"]["task_id"] == "t1"
