"""Phase 2c coverage wave — backend/routers/cto_projects.py (2026-08-24).

Auth/GitHub-App-connect-adjacent — founder's standing rule requires
testing_agent before this wave is considered done (explicitly named,
same as chat.py).

Real baseline (CONFIRMED, measured before writing anything new): 20
pre-existing test files already import `routers.cto_projects` directly
in-process. Running them together with `pytest --cov=routers.cto_projects`:

    routers/cto_projects.py   1652 stmts, 1253 missed, 24% covered
    158 passed, 30 failed (pre-existing — CONFIRMED not caused by this
    wave, zero changes made before this measurement), 12 skipped

All 30 pre-existing failures share one root cause: the same GitHub-App
/PAT-removal migration issue already documented in memory/PRD.md
("GitHub App auth failed (app_installation_missing)") — legacy PAT-only
test fixtures no longer resolve a repo credential now that PAT auth
was removed. Confirmed unrelated to this wave (no chat.py/cto_projects.py
app code was touched, only this new test file was added).

The two heaviest functions in this file, `_run_task_via_api` (CC=166,
~1100 lines) and `_run_task_with_git` (CC=51), are the actual git-worker
pipelines (clone/generate/verify/commit/push) — same posture as
chat.py's `chat_stream` / loop_engine.py's `_do_execute`: deliberately
scoped out as a documented gap, not attempted here.

This wave targets the CRUD/connect-flow endpoints instead: add_project
(all dual-auth branches), list/remove/update project, check/test-pat,
build/get brain, tree/file browsing, submit/rollback/retry task, get
task/scan, project_tasks, plus the 6 pure helper functions.
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
        if sort and matched:
            key, direction = sort[0]
            matched.sort(key=lambda r: r.get(key, 0), reverse=(direction == -1))
        return dict(matched[0]) if matched else None

    async def update_one(self, query, update, upsert=False):
        import types
        for r in self.rows:
            if self._match(r, query):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                for k, v in (update.get("$push") or {}).items():
                    r.setdefault(k, []).append(v)
                for k, v in (update.get("$addToSet") or {}).items():
                    arr = r.setdefault(k, [])
                    if v not in arr:
                        arr.append(v)
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    async def delete_one(self, query):
        import types
        before = len(self.rows)
        self.rows = [r for r in self.rows if not self._match(r, query)]
        return types.SimpleNamespace(deleted_count=before - len(self.rows))

    async def insert_one(self, doc):
        import types
        self.rows.append(dict(doc))
        return types.SimpleNamespace(inserted_id="new")

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


USER = {"user_id": "u1", "email": "user@example.com", "tier": "pro",
       "is_admin": False, "is_unlimited": False, "track": "dev",
       "created_at": time.time()}


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


AUTH = {"Authorization": "Bearer u1"}


class _FakeResp:
    def __init__(self, status_code, json_body=None, headers=None):
        self.status_code = status_code
        self._json = json_body or {}
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=self)


# ═════════════════════════════════════════════════════════════════════
# Pure helper functions
# ═════════════════════════════════════════════════════════════════════

class TestPureHelpers:
    def test_parse_repo_valid_url(self):
        from routers import cto_projects as m
        assert m._parse_repo("https://github.com/acme/widgets") == ("acme", "widgets")

    def test_parse_repo_trailing_slash_and_git_suffix(self):
        from routers import cto_projects as m
        assert m._parse_repo("https://github.com/acme/widgets.git/") == ("acme", "widgets")

    def test_parse_repo_bad_url_raises_400(self):
        from routers import cto_projects as m
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            m._parse_repo("not-a-url")
        assert exc.value.status_code == 400

    def test_frontend_subset_keeps_allowed_extensions(self):
        from routers import cto_projects as m
        out = m._frontend_subset({"app.py": "code", "readme.md": "docs", "logo.png": "binary"})
        assert "app.py" in out and "readme.md" in out
        assert "logo.png" not in out

    def test_frontend_subset_keeps_env_example_special_case(self):
        from routers import cto_projects as m
        out = m._frontend_subset({".env.example": "KEY=value"})
        assert ".env.example" in out

    def test_frontend_subset_drops_oversized_body(self):
        from routers import cto_projects as m
        out = m._frontend_subset({"big.py": "x" * 40_000})
        assert "big.py" not in out

    def test_frontend_subset_caps_at_12_files(self):
        from routers import cto_projects as m
        edits = {f"f{i}.py": "x" for i in range(20)}
        out = m._frontend_subset(edits)
        assert len(out) == 12

    def test_frontend_subset_skips_non_string_body(self):
        from routers import cto_projects as m
        out = m._frontend_subset({"weird.py": {"not": "a string"}})
        assert "weird.py" not in out

    def test_browse_keep_path_empty_path_false(self):
        from routers import cto_projects as m
        assert m._browse_keep_path("", 10) is False

    def test_browse_keep_path_skips_skip_dirs(self):
        from routers import cto_projects as m
        assert m._browse_keep_path("node_modules/foo.js", 10) is False

    def test_browse_keep_path_skips_binary_ext(self):
        from routers import cto_projects as m
        assert m._browse_keep_path("assets/logo.png", 10) is False

    def test_browse_keep_path_skips_oversized(self):
        from routers import cto_projects as m
        assert m._browse_keep_path("src/big.py", 999_999) is False

    def test_browse_keep_path_normal_file_true(self):
        from routers import cto_projects as m
        assert m._browse_keep_path("src/main.py", 100) is True

    def test_classify_phase_read(self):
        from routers import cto_projects as m
        assert m._classify_phase("📡 Reading repo files") == "phase_read"

    def test_classify_phase_think(self):
        from routers import cto_projects as m
        assert m._classify_phase("🧠 Thinking about the fix") == "phase_think"

    def test_classify_phase_write(self):
        from routers import cto_projects as m
        assert m._classify_phase("✏️ Writing changes") == "phase_write"

    def test_classify_phase_verify(self):
        from routers import cto_projects as m
        assert m._classify_phase("🛡 Vanguard verifying") == "phase_verify"

    def test_classify_phase_commit(self):
        from routers import cto_projects as m
        assert m._classify_phase("🚀 Committing and pushing") == "phase_commit"

    def test_classify_phase_none_for_unrecognized(self):
        from routers import cto_projects as m
        assert m._classify_phase("just a random log line") is None

    def test_looks_truncated_empty_body(self):
        from routers import cto_projects as m
        assert m._looks_truncated("x.py", "   ") == "empty file body"

    def test_looks_truncated_placeholder_pattern(self):
        from routers import cto_projects as m
        result = m._looks_truncated("x.py", "def f():\n    ... rest of file\n")
        assert result is not None and "placeholder" in result

    def test_looks_truncated_short_codey_file(self):
        from routers import cto_projects as m
        assert m._looks_truncated("x.py", "pass") is not None

    def test_looks_truncated_normal_file_returns_none(self):
        from routers import cto_projects as m
        body = "\n".join([f"line{i} = {i}" for i in range(10)])
        assert m._looks_truncated("x.py", body) is None

    def test_hallucination_reasons_new_file_allowed(self):
        from routers import cto_projects as m
        out = m._hallucination_reasons({"new.py": "print(1)"}, {})
        assert out == []

    def test_hallucination_reasons_short_original_skipped(self):
        from routers import cto_projects as m
        out = m._hallucination_reasons({"x.py": "a"}, {"x.py": "one\ntwo"})
        assert out == []

    def test_hallucination_reasons_high_keep_ratio_no_flag(self):
        from routers import cto_projects as m
        original = "\n".join([f"line{i}" for i in range(10)])
        out = m._hallucination_reasons({"x.py": original}, {"x.py": original})
        assert out == []

    def test_hallucination_reasons_low_keep_ratio_flags(self):
        from routers import cto_projects as m
        original = "\n".join([f"line{i}" for i in range(10)])
        rewritten = "totally different content\nwith nothing in common\n"
        out = m._hallucination_reasons({"x.py": rewritten}, {"x.py": original})
        assert len(out) == 1
        assert "x.py" in out[0]


# ═════════════════════════════════════════════════════════════════════
# _retry — exponential-backoff wrapper
# ═════════════════════════════════════════════════════════════════════

class TestRetryHelper:
    @pytest.mark.asyncio
    async def test_retry_succeeds_first_try(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        _dbmod.set_db(fake_db)
        try:
            calls = []

            async def factory():
                calls.append(1)
                return "ok"

            result = await m._retry(factory, what="test-op", task_id="t1")
        finally:
            _dbmod.set_db(None)
        assert result == "ok"
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_retry_exhausts_and_raises_last_exception(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        _dbmod.set_db(fake_db)

        async def factory():
            raise RuntimeError("always fails")

        with patch("asyncio.sleep", AsyncMock(return_value=None)):
            try:
                with pytest.raises(RuntimeError, match="always fails"):
                    await m._retry(factory, what="test-op", task_id="t1", attempts=2)
            finally:
                _dbmod.set_db(None)

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        _dbmod.set_db(fake_db)
        attempt = {"n": 0}

        async def factory():
            attempt["n"] += 1
            if attempt["n"] == 1:
                raise RuntimeError("transient")
            return "recovered"

        with patch("asyncio.sleep", AsyncMock(return_value=None)):
            try:
                result = await m._retry(factory, what="test-op", task_id="t1", attempts=3)
            finally:
                _dbmod.set_db(None)
        assert result == "recovered"


# ═════════════════════════════════════════════════════════════════════
# _log / _set_status — worker logging helpers
# ═════════════════════════════════════════════════════════════════════

class TestLogAndSetStatus:
    @pytest.mark.asyncio
    async def test_log_no_db_still_emits(self):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        _dbmod.set_db(None)
        await m._log("t1", "🚀 committing now")

    @pytest.mark.asyncio
    async def test_log_persists_step_and_phase(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_tasks.rows.append({"task_id": "t1", "steps": []})
        _dbmod.set_db(fake_db)
        try:
            await m._log("t1", "🚀 committing now")
        finally:
            _dbmod.set_db(None)
        step = fake_db.cto_tasks.rows[0]["steps"][0]
        assert step["kind"] == "phase_commit"

    @pytest.mark.asyncio
    async def test_set_status_no_db_noop(self):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        _dbmod.set_db(None)
        await m._set_status("t1", status="done")

    @pytest.mark.asyncio
    async def test_set_status_failed_runs_translator(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_tasks.rows.append({"task_id": "t1"})
        _dbmod.set_db(fake_db)
        try:
            with patch("services.error_translator.translate",
                      AsyncMock(return_value={"plain": "oops", "steps": ["retry"],
                                              "suggestion": "try again", "source": "llm"})):
                await m._set_status("t1", status="failed", error="boom")
        finally:
            _dbmod.set_db(None)
        row = fake_db.cto_tasks.rows[0]
        assert row["error_plain"] == "oops"

    @pytest.mark.asyncio
    async def test_set_status_translator_crash_swallowed(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_tasks.rows.append({"task_id": "t1"})
        _dbmod.set_db(fake_db)
        try:
            with patch("services.error_translator.translate",
                      AsyncMock(side_effect=RuntimeError("down"))):
                await m._set_status("t1", status="failed", error="boom")
        finally:
            _dbmod.set_db(None)
        row = fake_db.cto_tasks.rows[0]
        assert row["status"] == "failed"


# ═════════════════════════════════════════════════════════════════════
# GET /cto/projects/list · DELETE /cto/projects/{id} ·
# GET /cto/projects/{id}/indexing-status · POST /cto/projects/verify-pat
# ═════════════════════════════════════════════════════════════════════

class TestListRemoveIndexingVerifyPat:
    def test_list_projects_hides_token_adds_has_pat(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1", "name": "Widgets",
            "github_token": "encrypted-blob", "created_at": time.time(),
        })
        r = client.get("/api/aurem-dev/cto/projects/list", headers=AUTH)
        assert r.status_code == 200
        proj = r.json()["projects"][0]
        assert proj["has_pat"] is True
        assert "github_token" not in proj

    def test_list_projects_unauthenticated(self, client):
        r = client.get("/api/aurem-dev/cto/projects/list")
        assert r.status_code == 401

    def test_remove_project_deletes_owned_row(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        r = client.delete("/api/aurem-dev/cto/projects/p1", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["deleted"] == 1
        assert fake_db.cto_projects.rows == []

    def test_indexing_status_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/projects/nope/indexing-status", headers=AUTH)
        assert r.status_code == 404

    def test_indexing_status_found(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1", "name": "Widgets",
            "indexing_status": "ready",
        })
        r = client.get("/api/aurem-dev/cto/projects/p1/indexing-status", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["ready"] is True

    def test_verify_pat_always_rejects(self, client):
        r = client.post("/api/aurem-dev/cto/projects/verify-pat", headers=AUTH, json={})
        assert r.status_code == 200
        assert r.json()["error"] == "pat_not_supported"


# ═════════════════════════════════════════════════════════════════════
# GET /cto/projects/{id}/check-pat
# ═════════════════════════════════════════════════════════════════════

class TestCheckProjectPat:
    def test_project_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/projects/nope/check-pat", headers=AUTH)
        assert r.status_code == 404

    def test_missing_token_returns_missing_state(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "app_installation_missing", "not connected"))):
            r = client.get("/api/aurem-dev/cto/projects/p1/check-pat", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["state"] == "missing"

    def test_valid_token_200(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get",
                  AsyncMock(return_value=_FakeResp(200, {"login": "acme"}))):
            r = client.get("/api/aurem-dev/cto/projects/p1/check-pat", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["state"] == "valid"
        assert r.json()["login"] == "acme"

    def test_expired_token_403(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp(403))):
            r = client.get("/api/aurem-dev/cto/projects/p1/check-pat", headers=AUTH)
        assert r.json()["state"] == "expired"

    def test_network_error_returns_unknown(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get", AsyncMock(side_effect=RuntimeError("down"))):
            r = client.get("/api/aurem-dev/cto/projects/p1/check-pat", headers=AUTH)
        assert r.json()["state"] == "unknown"

    def test_other_status_returns_unknown(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp(500))):
            r = client.get("/api/aurem-dev/cto/projects/p1/check-pat", headers=AUTH)
        assert r.json()["state"] == "unknown"


# ═════════════════════════════════════════════════════════════════════
# build-brain / brain
# ═════════════════════════════════════════════════════════════════════

class TestBrainEndpoints:
    def test_build_brain_project_not_found(self, client, fake_db):
        r = client.post("/api/aurem-dev/cto/projects/nope/build-brain", headers=AUTH)
        assert r.status_code == 404

    def test_build_brain_auth_error(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "app_installation_missing", "detail"))):
            r = client.post("/api/aurem-dev/cto/projects/p1/build-brain", headers=AUTH)
        assert r.status_code == 403

    def test_build_brain_success(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets", "branch": "main",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("services.project_brain.build_brain_v2",
                  AsyncMock(return_value={"version": 2, "structure": {"src": {}},
                                          "stack": {"lang": "python"}, "task_count": 3,
                                          "next_full_refresh_at": 123, "hot_paths": ["a.py"]})):
            r = client.post("/api/aurem-dev/cto/projects/p1/build-brain", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["task_count"] == 3

    def test_get_brain_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/projects/nope/brain", headers=AUTH)
        assert r.status_code == 404

    def test_get_brain_exists_false_when_no_brain(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.project_brain.get_brain_v2", AsyncMock(return_value=None)):
            r = client.get("/api/aurem-dev/cto/projects/p1/brain", headers=AUTH)
        assert r.json()["exists"] is False

    def test_get_brain_exists_true_with_summary(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.project_brain.get_brain_v2",
                  AsyncMock(return_value={"version": 2})), \
             patch("services.project_brain.format_brain_for_agent",
                  return_value="summary text"):
            r = client.get("/api/aurem-dev/cto/projects/p1/brain", headers=AUTH)
        assert r.json()["exists"] is True
        assert r.json()["summary"] == "summary text"

    def test_build_brain_missing_repo_config(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))):
            r = client.post("/api/aurem-dev/cto/projects/p1/build-brain", headers=AUTH)
        assert r.status_code == 400


# ═════════════════════════════════════════════════════════════════════
# GET /cto/projects/{id}/test-pat
# ═════════════════════════════════════════════════════════════════════

class TestTestProjectPat:
    def test_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/projects/nope/test-pat", headers=AUTH)
        assert r.status_code == 404

    def test_no_repo_configured(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        r = client.get("/api/aurem-dev/cto/projects/p1/test-pat", headers=AUTH)
        assert r.json()["ok"] is False

    def test_auth_error_raises_403(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "app_installation_missing", "detail"))):
            r = client.get("/api/aurem-dev/cto/projects/p1/test-pat", headers=AUTH)
        assert r.status_code == 403

    def test_success_200(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get",
                  AsyncMock(return_value=_FakeResp(200, {"full_name": "acme/widgets",
                                                         "private": True}))):
            r = client.get("/api/aurem-dev/cto/projects/p1/test-pat", headers=AUTH)
        assert r.json()["ok"] is True
        assert r.json()["private"] is True

    def test_rejected_credentials_401(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp(401))):
            r = client.get("/api/aurem-dev/cto/projects/p1/test-pat", headers=AUTH)
        assert r.json()["ok"] is False

    def test_repo_not_found_404_status(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp(404))):
            r = client.get("/api/aurem-dev/cto/projects/p1/test-pat", headers=AUTH)
        assert "not found" in r.json()["error"]

    def test_network_error(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        import httpx
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get",
                  AsyncMock(side_effect=httpx.RequestError("boom"))):
            r = client.get("/api/aurem-dev/cto/projects/p1/test-pat", headers=AUTH)
        assert r.json()["ok"] is False

    def test_no_token_no_auth_error(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, None, None))):
            r = client.get("/api/aurem-dev/cto/projects/p1/test-pat", headers=AUTH)
        assert r.json()["ok"] is False
        assert "not linked" in r.json()["error"]

    def test_bad_json_response_still_ok(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })

        class _BadJsonResp(_FakeResp):
            def json(self):
                raise ValueError("bad json")

        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get",
                  AsyncMock(return_value=_BadJsonResp(200))):
            r = client.get("/api/aurem-dev/cto/projects/p1/test-pat", headers=AUTH)
        assert r.json()["ok"] is True
        assert r.json()["repo"] == "acme/widgets"

    def test_other_status_code(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp(500))):
            r = client.get("/api/aurem-dev/cto/projects/p1/test-pat", headers=AUTH)
        assert r.json()["ok"] is False
        assert "HTTP 500" in r.json()["error"]


# ═════════════════════════════════════════════════════════════════════
# GET /cto/projects/{id}/tree · GET /cto/projects/{id}/file
# ═════════════════════════════════════════════════════════════════════

class TestTreeAndFile:
    def test_tree_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/projects/nope/tree", headers=AUTH)
        assert r.status_code == 404

    def test_tree_auth_error_403(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "app_installation_missing", "detail"))):
            r = client.get("/api/aurem-dev/cto/projects/p1/tree", headers=AUTH)
        assert r.status_code == 403

    def test_tree_not_connected_400(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))):
            r = client.get("/api/aurem-dev/cto/projects/p1/tree", headers=AUTH)
        assert r.status_code == 400

    def test_tree_success_sorted_and_filtered(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets", "branch": "main",
        })
        tree_data = {"tree": [
            {"type": "blob", "path": "README.md", "size": 10},
            {"type": "blob", "path": "src/main.py", "size": 20},
            {"type": "blob", "path": "node_modules/x.js", "size": 5},
            {"type": "tree", "path": "src"},
        ]}
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp(200, tree_data))):
            r = client.get("/api/aurem-dev/cto/projects/p1/tree", headers=AUTH)
        assert r.status_code == 200
        paths = [f["path"] for f in r.json()["files"]]
        assert "node_modules/x.js" not in paths
        assert paths[0] == "README.md"

    def test_tree_github_404_branch(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets", "branch": "main",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp(404))):
            r = client.get("/api/aurem-dev/cto/projects/p1/tree", headers=AUTH)
        assert r.status_code == 404

    def test_tree_401_invalid_pat(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets", "branch": "main",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp(401))):
            r = client.get("/api/aurem-dev/cto/projects/p1/tree", headers=AUTH)
        assert r.status_code == 401

    def test_tree_network_error_502(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets", "branch": "main",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("httpx.AsyncClient.get", AsyncMock(side_effect=RuntimeError("net down"))):
            r = client.get("/api/aurem-dev/cto/projects/p1/tree", headers=AUTH)
        assert r.status_code == 502

    def test_file_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/projects/nope/file", headers=AUTH,
                       params={"path": "x.py"})
        assert r.status_code == 404

    def test_file_auth_error_403(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "app_installation_missing", "detail"))):
            r = client.get("/api/aurem-dev/cto/projects/p1/file", headers=AUTH,
                           params={"path": "x.py"})
        assert r.status_code == 403

    def test_file_not_connected_400(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))):
            r = client.get("/api/aurem-dev/cto/projects/p1/file", headers=AUTH,
                           params={"path": "x.py"})
        assert r.status_code == 400

    def test_file_invalid_path_traversal(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))):
            r = client.get("/api/aurem-dev/cto/projects/p1/file", headers=AUTH,
                           params={"path": "../../etc/passwd"})
        assert r.status_code == 400

    def test_file_success(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects.gh_api_fetch_file",
                  AsyncMock(return_value="print('hi')")):
            r = client.get("/api/aurem-dev/cto/projects/p1/file", headers=AUTH,
                           params={"path": "app.py"})
        assert r.status_code == 200
        assert r.json()["content"] == "print('hi')"

    def test_file_not_found_on_github(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects.gh_api_fetch_file",
                  AsyncMock(return_value=None)):
            r = client.get("/api/aurem-dev/cto/projects/p1/file", headers=AUTH,
                           params={"path": "missing.py"})
        assert r.status_code == 404

    def test_file_fetch_exception_502(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects.gh_api_fetch_file",
                  AsyncMock(side_effect=RuntimeError("net down"))):
            r = client.get("/api/aurem-dev/cto/projects/p1/file", headers=AUTH,
                           params={"path": "app.py"})
        assert r.status_code == 502

    def test_file_truncated_when_oversized(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        big_content = "x" * 250_000
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects.gh_api_fetch_file",
                  AsyncMock(return_value=big_content)):
            r = client.get("/api/aurem-dev/cto/projects/p1/file", headers=AUTH,
                           params={"path": "big.py"})
        assert r.json()["truncated"] is True


# ═════════════════════════════════════════════════════════════════════
# PATCH /cto/projects/{id}
# ═════════════════════════════════════════════════════════════════════

class TestUpdateProject:
    def test_nothing_to_update(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        r = client.patch("/api/aurem-dev/cto/projects/p1", headers=AUTH, json={})
        assert r.status_code == 400

    def test_pat_rejected(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        r = client.patch("/api/aurem-dev/cto/projects/p1", headers=AUTH,
                         json={"github_token": "ghp_xxx"})
        assert r.status_code == 400

    def test_not_found(self, client, fake_db):
        r = client.patch("/api/aurem-dev/cto/projects/nope", headers=AUTH,
                         json={"branch": "dev"})
        assert r.status_code == 404

    def test_success_updates_branch(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1", "branch": "main"})
        with patch("services.repo_context.invalidate_repo_context", AsyncMock(return_value=None)):
            r = client.patch("/api/aurem-dev/cto/projects/p1", headers=AUTH,
                             json={"branch": "dev"})
        assert r.status_code == 200
        assert fake_db.cto_projects.rows[0]["branch"] == "dev"

    def test_installation_id_sets_auth_method(self, client, fake_db):
        # 2026-08-26 — installation_id updates now run the real
        # verify_installation_for_repo() check (Aug reconnect root-cause
        # fix) before trusting the client-supplied id — mock it to
        # simulate GitHub confirming real repo access, same as
        # test_admin_audit_and_installation_active_2026_08.py does.
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.repo_context.invalidate_repo_context", AsyncMock(return_value=None)), \
             patch("services.github_app.verify_installation_for_repo",
                  AsyncMock(return_value=(True, None, None))):
            r = client.patch("/api/aurem-dev/cto/projects/p1", headers=AUTH,
                             json={"installation_id": 42})
        assert r.status_code == 200
        assert fake_db.cto_projects.rows[0]["auth_method"] == "github_app"

    def test_success_swallows_cache_invalidation_errors(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1", "branch": "main"})
        with patch("services.repo_context.invalidate_repo_context",
                  AsyncMock(side_effect=RuntimeError("cache down"))), \
             patch("routers.repo_status._CACHE") as mock_cache:
            mock_cache.pop.side_effect = RuntimeError("pop failed")
            r = client.patch("/api/aurem-dev/cto/projects/p1", headers=AUTH,
                             json={"branch": "dev"})
        assert r.status_code == 200
        assert fake_db.cto_projects.rows[0]["branch"] == "dev"


# ═════════════════════════════════════════════════════════════════════
# POST /cto/projects/add
# ═════════════════════════════════════════════════════════════════════

class TestAddProject:
    def _body(self, **kw):
        base = {"name": "Widgets", "github_url": "https://github.com/acme/widgets"}
        base.update(kw)
        return base

    def test_bad_github_url(self, client, fake_db):
        r = client.post("/api/aurem-dev/cto/projects/add", headers=AUTH,
                        json=self._body(github_url="not-a-url"))
        assert r.status_code == 400

    def test_neither_pat_nor_installation_id(self, client, fake_db):
        with patch("services.signup_guards.emit_funnel_event", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/projects/add", headers=AUTH, json=self._body())
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "auth_required"

    def test_pat_provided_rejected(self, client, fake_db):
        with patch("services.signup_guards.emit_funnel_event", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/projects/add", headers=AUTH,
                            json=self._body(github_token="ghp_xxx"))
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "pat_not_supported"

    def test_installation_not_linked_to_account(self, client, fake_db):
        with patch("services.signup_guards.emit_funnel_event", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/projects/add", headers=AUTH,
                            json=self._body(installation_id=99))
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "installation_not_found_or_inactive"

    def test_installation_no_repo_access(self, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 99, "user_id": "u1", "active": True,
        })
        import httpx
        err = httpx.HTTPStatusError(
            "404", request=None, response=_FakeResp(404))
        with patch("services.signup_guards.emit_funnel_event", AsyncMock(return_value=None)), \
             patch("services.github_app.get_repo_via_installation",
                  AsyncMock(side_effect=err)):
            r = client.post("/api/aurem-dev/cto/projects/add", headers=AUTH,
                            json=self._body(installation_id=99))
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "installation_no_repo_access"

    def test_installation_token_rejected(self, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 99, "user_id": "u1", "active": True,
        })
        import httpx
        err = httpx.HTTPStatusError("401", request=None, response=_FakeResp(401))
        with patch("services.signup_guards.emit_funnel_event", AsyncMock(return_value=None)), \
             patch("services.github_app.get_repo_via_installation",
                  AsyncMock(side_effect=err)):
            r = client.post("/api/aurem-dev/cto/projects/add", headers=AUTH,
                            json=self._body(installation_id=99))
        assert r.json()["detail"]["error"] == "installation_token_rejected"

    def test_installation_probe_other_status_502(self, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 99, "user_id": "u1", "active": True,
        })
        import httpx
        err = httpx.HTTPStatusError("500", request=None, response=_FakeResp(500))
        with patch("services.signup_guards.emit_funnel_event", AsyncMock(return_value=None)), \
             patch("services.github_app.get_repo_via_installation",
                  AsyncMock(side_effect=err)):
            r = client.post("/api/aurem-dev/cto/projects/add", headers=AUTH,
                            json=self._body(installation_id=99))
        assert r.status_code == 502

    def test_installation_probe_network_error(self, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 99, "user_id": "u1", "active": True,
        })
        import httpx
        with patch("services.signup_guards.emit_funnel_event", AsyncMock(return_value=None)), \
             patch("services.github_app.get_repo_via_installation",
                  AsyncMock(side_effect=httpx.RequestError("boom"))):
            r = client.post("/api/aurem-dev/cto/projects/add", headers=AUTH,
                            json=self._body(installation_id=99))
        assert r.status_code == 502

    def test_success_via_installation(self, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 99, "user_id": "u1", "active": True,
        })
        with patch("services.signup_guards.emit_funnel_event", AsyncMock(return_value=None)), \
             patch("services.github_app.get_repo_via_installation",
                  AsyncMock(return_value={"full_name": "acme/widgets"})), \
             patch("services.github_app.get_installation_token",
                  AsyncMock(return_value=("tok", 3600))), \
             patch("services.project_onboarding_scan.run_onboarding_scan",
                  AsyncMock(return_value=None)), \
             patch("routers.github_funnel.track_server_side", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/projects/add", headers=AUTH,
                            json=self._body(installation_id=99))
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["auth_method"] == "github_app"
        assert fake_db.cto_projects.rows[0]["github_owner"] == "acme"

    def test_indexing_scheduler_exception_swallowed(self, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 99, "user_id": "u1", "active": True,
        })
        with patch("services.signup_guards.emit_funnel_event", AsyncMock(return_value=None)), \
             patch("services.github_app.get_repo_via_installation",
                  AsyncMock(return_value={"full_name": "acme/widgets"})), \
             patch("services.github_app.get_installation_token",
                  AsyncMock(side_effect=RuntimeError("token mint failed"))), \
             patch("routers.github_funnel.track_server_side", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/projects/add", headers=AUTH,
                            json=self._body(installation_id=99))
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ═════════════════════════════════════════════════════════════════════
# POST /cto/tasks/submit
# ═════════════════════════════════════════════════════════════════════

class TestSubmitTask:
    def _body(self, **kw):
        # 2026-08-25 ambiguity-gate (services/ambiguity_gate.py) rejects
        # vague tasks before reaching rate-limit/budget/maxx/project
        # checks — "fix the bug" now short-circuits every one of these
        # tests with the SAME `ok:False, needs_clarification` response,
        # which is exactly why they all failed identically. Use a
        # concrete, file-referencing task so we reach the logic each
        # test actually means to exercise.
        base = {"project_id": "p1", "task": "fix the bug in signup.py"}
        base.update(kw)
        return base

    def test_rate_limited(self, client, fake_db):
        with patch("services.rate_limiter.check_rate_limit_async",
                  AsyncMock(return_value=False)), \
             patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                            json=self._body())
        assert r.status_code == 429

    def test_maxx_mode_locked_for_free_tier(self, client, fake_db):
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.subscription_tiers.can_use_feature", return_value=False):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                            json=self._body(maxx_mode=True))
        assert r.status_code == 403

    def test_success_queues_task(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context",
                  AsyncMock(return_value=fake_ctx)), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                            json=self._body())
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert len(fake_db.cto_tasks.rows) == 1

    def test_project_not_found_defense_in_depth(self, client, fake_db):
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context",
                  AsyncMock(return_value=fake_ctx)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                            json=self._body())
        assert r.status_code == 404

    def test_founder_bypasses_rate_limit(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        fake_ctx = type("Ctx", (), {"pat": "tok"})()

        async def _founder(authorization=None):
            return {**USER, "tier": "founder"}

        from routers import cto_projects as router_mod
        router_mod.current_dev = _founder
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.ora_context.build_ora_context",
                  AsyncMock(return_value=fake_ctx)), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                            json=self._body())
        assert r.status_code == 200


# ═════════════════════════════════════════════════════════════════════
# POST /cto/tasks/{id}/rollback
# ═════════════════════════════════════════════════════════════════════

class TestRollbackTask:
    def test_bad_confirm_string(self, client, fake_db):
        r = client.post("/api/aurem-dev/cto/tasks/t1/rollback", headers=AUTH,
                        json={"confirm": "nope"})
        assert r.status_code == 400

    def test_task_not_found(self, client, fake_db):
        r = client.post("/api/aurem-dev/cto/tasks/t1/rollback", headers=AUTH,
                        json={"confirm": "ROLLBACK"})
        assert r.status_code == 404

    def test_wrong_status(self, client, fake_db):
        fake_db.cto_tasks.rows.append({"task_id": "t1", "user_id": "u1", "status": "queued"})
        r = client.post("/api/aurem-dev/cto/tasks/t1/rollback", headers=AUTH,
                        json={"confirm": "ROLLBACK"})
        assert r.status_code == 400

    def test_no_commit_sha(self, client, fake_db):
        fake_db.cto_tasks.rows.append({"task_id": "t1", "user_id": "u1", "status": "done"})
        r = client.post("/api/aurem-dev/cto/tasks/t1/rollback", headers=AUTH,
                        json={"confirm": "ROLLBACK"})
        assert r.status_code == 400

    def test_already_rolled_back(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "done",
            "commit_sha": "abc123", "rollback_sha": "def456",
        })
        r = client.post("/api/aurem-dev/cto/tasks/t1/rollback", headers=AUTH,
                        json={"confirm": "ROLLBACK"})
        assert r.status_code == 409

    def test_rollback_in_progress(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "done",
            "commit_sha": "abc123", "rollback_status": "running",
        })
        r = client.post("/api/aurem-dev/cto/tasks/t1/rollback", headers=AUTH,
                        json={"confirm": "ROLLBACK"})
        assert r.status_code == 409

    def test_previous_rollback_failed(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "done",
            "commit_sha": "abc123", "rollback_status": "failed",
        })
        r = client.post("/api/aurem-dev/cto/tasks/t1/rollback", headers=AUTH,
                        json={"confirm": "ROLLBACK"})
        assert r.status_code == 409

    def test_parent_project_not_found(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "done",
            "commit_sha": "abc123", "project_id": "p1",
        })
        r = client.post("/api/aurem-dev/cto/tasks/t1/rollback", headers=AUTH,
                        json={"confirm": "ROLLBACK"})
        assert r.status_code == 404

    def test_auth_error_403(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "done",
            "commit_sha": "abc123", "project_id": "p1",
        })
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "app_installation_missing", "detail"))):
            r = client.post("/api/aurem-dev/cto/tasks/t1/rollback", headers=AUTH,
                            json={"confirm": "ROLLBACK"})
        assert r.status_code == 403

    def test_success_queues_rollback(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "done",
            "commit_sha": "abc123", "project_id": "p1",
        })
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects._run_rollback", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/t1/rollback", headers=AUTH,
                            json={"confirm": "ROLLBACK"})
        assert r.status_code == 200
        assert r.json()["rollback_status"] == "queued"


# ═════════════════════════════════════════════════════════════════════
# GET /cto/tasks/{id} · GET /cto/tasks/{id}/scan · GET /cto/tasks/project/{id}
# ═════════════════════════════════════════════════════════════════════

class TestGetTaskAndScanAndProjectTasks:
    def test_get_task_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/tasks/t1", headers=AUTH)
        assert r.status_code == 404

    def test_get_task_found(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "done",
            "edited_files": {"x.py": "code"},
        })
        r = client.get("/api/aurem-dev/cto/tasks/t1", headers=AUTH)
        assert r.status_code == 200
        assert "edited_files" in r.json()["task"]

    def test_get_task_personal_track_strips_diff(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "done",
            "edited_files": {"x.py": "code"},
        })

        async def _personal(authorization=None):
            return {**USER, "track": "personal"}

        from routers import cto_projects as router_mod
        router_mod.current_dev = _personal
        r = client.get("/api/aurem-dev/cto/tasks/t1", headers=AUTH)
        assert "edited_files" not in r.json()["task"]

    def test_get_task_scan_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/tasks/t1/scan", headers=AUTH)
        assert r.status_code == 404

    def test_get_task_scan_found_no_scan(self, client, fake_db):
        fake_db.cto_tasks.rows.append({"task_id": "t1", "user_id": "u1", "status": "done"})
        r = client.get("/api/aurem-dev/cto/tasks/t1/scan", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["scan"] is None

    def test_project_tasks_empty(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/tasks/project/p1", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["tasks"] == []

    def test_project_tasks_returns_rows(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "project_id": "p1", "created_at": time.time(),
        })
        r = client.get("/api/aurem-dev/cto/tasks/project/p1", headers=AUTH)
        assert len(r.json()["tasks"]) == 1


# ═════════════════════════════════════════════════════════════════════
# GET /cto/tasks/{id}/stream — SSE (terminal-state synthetic frames only)
# ═════════════════════════════════════════════════════════════════════

class TestTaskStream:
    def test_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/tasks/t1/stream", headers=AUTH)
        assert r.status_code == 404

    def test_already_done_emits_synthetic_handoff_and_done(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "done",
            "commit_sha": "abc1234", "project_id": "p1",
        })
        r = client.get("/api/aurem-dev/cto/tasks/t1/stream", headers=AUTH)
        assert r.status_code == 200
        assert "task_handoff" in r.text
        assert '"type": "done"' in r.text or '"type":"done"' in r.text

    def test_already_failed_emits_fail_frame(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "failed",
            "error": "boom",
        })
        r = client.get("/api/aurem-dev/cto/tasks/t1/stream", headers=AUTH)
        assert r.status_code == 200
        assert "fail" in r.text

    def test_live_queue_frame_forwarded_immediately(self, client, fake_db):
        from routers import cto_projects as m
        fake_db.cto_tasks.rows.append({
            "task_id": "t-live-1", "user_id": "u1", "status": "queued",
        })
        q = asyncio.Queue(maxsize=256)
        q.put_nowait({"type": "done", "step": "shipped", "pct": 100, "ts": time.time()})
        m._task_queues["t-live-1"] = q
        r = client.get("/api/aurem-dev/cto/tasks/t-live-1/stream", headers=AUTH)
        assert r.status_code == 200
        assert "shipped" in r.text
        assert "t-live-1" not in m._task_queues


# ═════════════════════════════════════════════════════════════════════
# POST /cto/tasks/{id}/retry
# ═════════════════════════════════════════════════════════════════════

class TestRetryTask:
    def test_not_found(self, client, fake_db):
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/t1/retry", headers=AUTH)
        assert r.status_code == 404

    def test_wrong_status(self, client, fake_db):
        fake_db.cto_tasks.rows.append({"task_id": "t1", "user_id": "u1", "status": "done"})
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/t1/retry", headers=AUTH)
        assert r.status_code == 400

    def test_parent_project_not_found(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "failed", "project_id": "p1",
        })
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/t1/retry", headers=AUTH)
        assert r.status_code == 404

    def test_auth_error_403(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "failed", "project_id": "p1",
            "error": "boom",
        })
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "app_installation_missing", "detail"))):
            r = client.post("/api/aurem-dev/cto/tasks/t1/retry", headers=AUTH)
        assert r.status_code == 403

    def test_success_carries_failure_context(self, client, fake_db):
        fake_db.cto_tasks.rows.append({
            "task_id": "t1", "user_id": "u1", "status": "failed", "project_id": "p1",
            "error": "empty file body rejected", "task": "fix it",
        })
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/t1/retry", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["carried_failure_context"] is True
        assert r.json()["retry_of"] == "t1"


# ═════════════════════════════════════════════════════════════════════
# POST /cto/projects/{id}/warm-start · _run_warm_agents (direct)
# ═════════════════════════════════════════════════════════════════════

class TestWarmStart:
    def test_project_not_found(self, client, fake_db):
        r = client.post("/api/aurem-dev/cto/projects/nope/warm-start", headers=AUTH)
        assert r.status_code == 404

    def test_auth_error_403(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "app_installation_missing", "detail"))):
            r = client.post("/api/aurem-dev/cto/projects/p1/warm-start", headers=AUTH)
        assert r.status_code == 403

    def test_no_token_skips_warm_start(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, None, None))):
            r = client.post("/api/aurem-dev/cto/projects/p1/warm-start", headers=AUTH)
        assert r.json()["status"] == "no_token"
        assert r.json()["job_id"] is None

    def test_success_creates_job(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects._run_warm_agents", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/projects/p1/warm-start", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["job_id"] is not None
        assert len(fake_db.warm_start_jobs.rows) == 1

    @pytest.mark.asyncio
    async def test_run_warm_agents_all_succeed(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.warm_start_jobs.rows.append({"job_id": "ws1", "agents_done": []})
        _dbmod.set_db(fake_db)
        try:
            with patch("services.project_brain.get_brain_v2",
                      AsyncMock(return_value={"last_scan": 0})), \
                 patch("services.project_brain.build_brain_v2",
                      AsyncMock(return_value={})), \
                 patch("httpx.AsyncClient.get",
                      AsyncMock(return_value=_FakeResp(200, [
                          {"sha": "abc1234", "commit": {"message": "fix bug",
                                                        "author": {"name": "dev"}}},
                      ]))), \
                 patch("services.project_brain._gh_list_files",
                      AsyncMock(return_value=["a.py", "b.py"])), \
                 patch("services.project_brain._gh_read_small",
                      AsyncMock(return_value="flask==2.0")), \
                 patch("services.graph_builder.get_graph", AsyncMock(return_value=None)), \
                 patch("services.graph_builder.build_graph", AsyncMock(return_value=None)):
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
        assert "recent_commits" in job

    @pytest.mark.asyncio
    async def test_run_warm_agents_agent_exception_still_marks_done(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.warm_start_jobs.rows.append({"job_id": "ws1", "agents_done": []})
        _dbmod.set_db(fake_db)
        try:
            with patch("services.project_brain.get_brain_v2",
                      AsyncMock(side_effect=RuntimeError("db down"))), \
                 patch("httpx.AsyncClient.get",
                      AsyncMock(side_effect=RuntimeError("net down"))), \
                 patch("services.project_brain._gh_list_files",
                      AsyncMock(side_effect=RuntimeError("net down"))), \
                 patch("services.project_brain._gh_read_small",
                      AsyncMock(side_effect=RuntimeError("net down"))), \
                 patch("services.graph_builder.get_graph",
                      AsyncMock(side_effect=RuntimeError("net down"))):
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

    @pytest.mark.asyncio
    async def test_run_warm_agents_timeout_still_marks_done(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.warm_start_jobs.rows.append({"job_id": "ws1", "agents_done": []})
        _dbmod.set_db(fake_db)
        try:
            with patch("services.project_brain.get_brain_v2", AsyncMock(return_value=None)), \
                 patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp(200, []))), \
                 patch("services.project_brain._gh_list_files", AsyncMock(return_value=[])), \
                 patch("services.project_brain._gh_read_small", AsyncMock(return_value="")), \
                 patch("services.graph_builder.get_graph", AsyncMock(return_value=None)), \
                 patch("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError())):
                await m._run_warm_agents(
                    job_id="ws1", project_id="p1", user_id="u1",
                    gh_token="tok", gh_owner="acme", gh_repo="widgets",
                    branch="main", db=fake_db,
                )
        finally:
            _dbmod.set_db(None)
        job = fake_db.warm_start_jobs.rows[0]
        assert job["status"] == "ready"
        assert set(job["agents_done"]) == {"brain", "recent", "structure", "stack", "graph"}

    @pytest.mark.asyncio
    async def test_run_warm_agents_outer_crash_marks_job_failed(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.warm_start_jobs.rows.append({"job_id": "ws1", "agents_done": []})
        _dbmod.set_db(fake_db)
        try:
            with patch("services.project_brain.get_brain_v2", AsyncMock(return_value=None)), \
                 patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp(200, []))), \
                 patch("services.project_brain._gh_list_files", AsyncMock(return_value=[])), \
                 patch("services.project_brain._gh_read_small", AsyncMock(return_value="")), \
                 patch("services.graph_builder.get_graph", AsyncMock(return_value=None)), \
                 patch("asyncio.gather", AsyncMock(side_effect=RuntimeError("gather crashed"))):
                await m._run_warm_agents(
                    job_id="ws1", project_id="p1", user_id="u1",
                    gh_token="tok", gh_owner="acme", gh_repo="widgets",
                    branch="main", db=fake_db,
                )
        finally:
            _dbmod.set_db(None)
        job = fake_db.warm_start_jobs.rows[0]
        assert job["status"] == "failed"


    @pytest.mark.asyncio
    async def test_run_warm_agents_mark_done_db_error_swallowed(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.warm_start_jobs.rows.append({"job_id": "ws1", "agents_done": []})
        _dbmod.set_db(fake_db)

        orig_update_one = fake_db.warm_start_jobs.update_one

        async def _raising_update_one(query, update, upsert=False):
            if "$addToSet" in update:
                raise RuntimeError("db down")
            return await orig_update_one(query, update, upsert=upsert)

        fake_db.warm_start_jobs.update_one = _raising_update_one
        try:
            with patch("services.project_brain.get_brain_v2",
                      AsyncMock(return_value={"last_scan": 0})), \
                 patch("services.project_brain.build_brain_v2",
                      AsyncMock(return_value={})), \
                 patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp(200, []))), \
                 patch("services.project_brain._gh_list_files", AsyncMock(return_value=[])), \
                 patch("services.project_brain._gh_read_small", AsyncMock(return_value="")), \
                 patch("services.graph_builder.get_graph", AsyncMock(return_value=None)), \
                 patch("services.graph_builder.build_graph", AsyncMock(return_value=None)):
                await m._run_warm_agents(
                    job_id="ws1", project_id="p1", user_id="u1",
                    gh_token="tok", gh_owner="acme", gh_repo="widgets",
                    branch="main", db=fake_db,
                )
        finally:
            _dbmod.set_db(None)
        job = fake_db.warm_start_jobs.rows[0]
        assert job["status"] == "ready"
        assert job["agents_done"] == []


# ═════════════════════════════════════════════════════════════════════
# Codebase Graph endpoints
# ═════════════════════════════════════════════════════════════════════

class TestGraphEndpoints:
    def test_build_graph_not_found(self, client, fake_db):
        r = client.post("/api/aurem-dev/cto/projects/nope/build-graph", headers=AUTH)
        assert r.status_code == 404

    def test_build_graph_auth_error(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "app_installation_missing", "detail"))):
            r = client.post("/api/aurem-dev/cto/projects/p1/build-graph", headers=AUTH)
        assert r.status_code == 403

    def test_build_graph_not_connected(self, client, fake_db):
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))):
            r = client.post("/api/aurem-dev/cto/projects/p1/build-graph", headers=AUTH)
        assert r.status_code == 400

    def test_build_graph_success(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=("tok", None, None))), \
             patch("services.graph_builder.build_graph", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/projects/p1/build-graph", headers=AUTH)
        assert r.status_code == 200

    def test_get_graph_not_built(self, client, fake_db):
        with patch("services.graph_builder.get_graph", AsyncMock(return_value=None)):
            r = client.get("/api/aurem-dev/cto/projects/p1/graph", headers=AUTH)
        assert r.json()["status"] == "not_built"

    def test_get_graph_ready(self, client, fake_db):
        with patch("services.graph_builder.get_graph",
                  AsyncMock(return_value={"file_count": 5})):
            r = client.get("/api/aurem-dev/cto/projects/p1/graph", headers=AUTH)
        assert r.json()["status"] == "ready"

    def test_get_graph_full(self, client, fake_db):
        with patch("services.graph_builder.get_graph_full",
                  AsyncMock(return_value={"file_count": 5})):
            r = client.get("/api/aurem-dev/cto/projects/p1/graph", headers=AUTH,
                           params={"full": "true"})
        assert r.json()["status"] == "ready"

    def test_build_mermaid_failure(self, client, fake_db):
        with patch("services.mermaid_diagram.build_and_persist_mermaid",
                  AsyncMock(return_value={"ok": False, "reason": "no graph yet"})):
            r = client.post("/api/aurem-dev/cto/projects/p1/graph/mermaid", headers=AUTH)
        assert r.status_code == 400

    def test_build_mermaid_success(self, client, fake_db):
        with patch("services.mermaid_diagram.build_and_persist_mermaid",
                  AsyncMock(return_value={"ok": True, "mermaid_code": "graph TD;"})):
            r = client.post("/api/aurem-dev/cto/projects/p1/graph/mermaid", headers=AUTH)
        assert r.status_code == 200

    def test_graph_tour_not_built(self, client, fake_db):
        with patch("services.graph_builder.get_graph_full", AsyncMock(return_value=None)):
            r = client.get("/api/aurem-dev/cto/projects/p1/graph/tour", headers=AUTH)
        assert r.json()["status"] == "not_built"

    def test_graph_tour_success(self, client, fake_db):
        doc = {
            "nodes": {"src/api.py": {"description": "API layer", "symbols": ["app"]}},
            "layers": {"API": ["src/api.py"]},
            "file_count": 1,
        }
        with patch("services.graph_builder.get_graph_full", AsyncMock(return_value=doc)):
            r = client.get("/api/aurem-dev/cto/projects/p1/graph/tour", headers=AUTH)
        assert r.json()["status"] == "ready"
        assert r.json()["tour"][0]["path"] == "src/api.py"

    def test_graph_tour_caps_at_12_steps(self, client, fake_db):
        nodes = {f"src/f{i}.py": {"description": "", "symbols": []} for i in range(20)}
        doc = {
            "nodes": nodes,
            "layers": {"Config": [f"src/f{i}.py" for i in range(3)],
                      "Data": [f"src/f{i}.py" for i in range(3, 6)],
                      "Service": [f"src/f{i}.py" for i in range(6, 9)],
                      "API": [f"src/f{i}.py" for i in range(9, 12)],
                      "Hook": [f"src/f{i}.py" for i in range(12, 15)],
                      "UI": [f"src/f{i}.py" for i in range(15, 18)],
                      "Util": [f"src/f{i}.py" for i in range(18, 20)]},
            "file_count": 20,
        }
        with patch("services.graph_builder.get_graph_full", AsyncMock(return_value=doc)):
            r = client.get("/api/aurem-dev/cto/projects/p1/graph/tour", headers=AUTH)
        assert len(r.json()["tour"]) == 12

    def test_search_graph_not_built(self, client, fake_db):
        with patch("services.graph_builder.get_graph_full", AsyncMock(return_value=None)):
            r = client.get("/api/aurem-dev/cto/projects/p1/graph/search", headers=AUTH,
                           params={"q": "api"})
        assert r.json()["status"] == "not_built"

    def test_search_graph_empty_query(self, client, fake_db):
        with patch("services.graph_builder.get_graph_full",
                  AsyncMock(return_value={"nodes": {}})):
            r = client.get("/api/aurem-dev/cto/projects/p1/graph/search", headers=AUTH)
        assert r.json()["results"] == []

    def test_search_graph_scores_and_sorts(self, client, fake_db):
        doc = {"nodes": {
            "src/auth.py": {"description": "authentication", "symbols": ["login"], "layer": "API"},
            "src/other.py": {"description": "unrelated", "symbols": [], "layer": "Util"},
        }}
        with patch("services.graph_builder.get_graph_full", AsyncMock(return_value=doc)):
            r = client.get("/api/aurem-dev/cto/projects/p1/graph/search", headers=AUTH,
                           params={"q": "auth"})
        results = r.json()["results"]
        assert results[0]["path"] == "src/auth.py"

    def test_search_graph_endswith_and_symbol_matches(self, client, fake_db):
        doc = {"nodes": {
            "src/user_auth.py": {"description": "handles login flow",
                                  "symbols": ["login", "logout_handler"], "layer": "API"},
        }}
        with patch("services.graph_builder.get_graph_full", AsyncMock(return_value=doc)):
            r = client.get("/api/aurem-dev/cto/projects/p1/graph/search", headers=AUTH,
                           params={"q": "login"})
        results = r.json()["results"]
        assert results[0]["path"] == "src/user_auth.py"
        assert results[0]["score"] > 100

    def test_search_graph_endswith_query_matches(self, client, fake_db):
        doc = {"nodes": {
            "src/config/auth.py": {"description": "", "symbols": [], "layer": "Config"},
        }}
        with patch("services.graph_builder.get_graph_full", AsyncMock(return_value=doc)):
            r = client.get("/api/aurem-dev/cto/projects/p1/graph/search", headers=AUTH,
                           params={"q": "config/auth.py"})
        results = r.json()["results"]
        assert results[0]["path"] == "src/config/auth.py"

    def test_graph_impact_no_files(self, client, fake_db):
        r = client.post("/api/aurem-dev/cto/projects/p1/graph/impact", headers=AUTH, json={})
        assert r.status_code == 400

    def test_graph_impact_not_built(self, client, fake_db):
        with patch("services.graph_builder.get_graph_full", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/projects/p1/graph/impact", headers=AUTH,
                            json={"files": ["src/a.py"]})
        assert r.json()["status"] == "not_built"

    def test_graph_impact_success(self, client, fake_db):
        doc = {"edges": [
            {"from": "src/main.py", "to": "src/a.py"},
            {"from": "src/other.py", "to": "src/b.py"},
        ]}
        with patch("services.graph_builder.get_graph_full", AsyncMock(return_value=doc)):
            r = client.post("/api/aurem-dev/cto/projects/p1/graph/impact", headers=AUTH,
                            json={"files": ["src/a.py"]})
        body = r.json()
        assert body["blast_radius"] == 1
        assert body["impacted"][0]["path"] == "src/main.py"

    def test_graph_impact_caps_at_50(self, client, fake_db):
        edges = [{"from": f"src/f{i}.py", "to": "src/a.py"} for i in range(60)]
        doc = {"edges": edges}
        with patch("services.graph_builder.get_graph_full", AsyncMock(return_value=doc)):
            r = client.post("/api/aurem-dev/cto/projects/p1/graph/impact", headers=AUTH,
                            json={"files": ["src/a.py"]})
        assert r.json()["blast_radius"] == 50

    def test_warm_start_status_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/projects/warm-start/ws1/status", headers=AUTH)
        assert r.status_code == 404

    def test_warm_start_status_success(self, client, fake_db):
        fake_db.warm_start_jobs.rows.append({
            "job_id": "ws1", "user_id": "u1", "status": "running",
            "agents_done": ["brain", "recent"],
            "agents_total": ["brain", "recent", "structure", "stack", "graph"],
        })
        r = client.get("/api/aurem-dev/cto/projects/warm-start/ws1/status", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["progress"] == 0.4
        assert r.json()["ready"] is False


# ═════════════════════════════════════════════════════════════════════
# _enqueue_cto_task — programmatic Mode C trigger (chat handoff)
# ═════════════════════════════════════════════════════════════════════

class TestEnqueueCtoTask:
    @pytest.mark.asyncio
    async def test_no_db_returns_reason(self):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        _dbmod.set_db(None)
        result = await m._enqueue_cto_task("u1", "p1", "fix it")
        assert result == {"ok": False, "reason": "no_db"}

    @pytest.mark.asyncio
    async def test_no_project_returns_reason(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        _dbmod.set_db(fake_db)
        try:
            result = await m._enqueue_cto_task("u1", "home", "fix it")
        finally:
            _dbmod.set_db(None)
        assert result == {"ok": False, "reason": "no_project"}

    @pytest.mark.asyncio
    async def test_no_pat_marks_task_failed(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        _dbmod.set_db(fake_db)
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                      AsyncMock(return_value=(None, "app_installation_missing", "detail"))):
                result = await m._enqueue_cto_task("u1", "p1", "fix it")
        finally:
            _dbmod.set_db(None)
        assert result["ok"] is False
        assert result["reason"] == "no_pat"
        assert fake_db.cto_tasks.rows[0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_success_enqueues_task(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        _dbmod.set_db(fake_db)
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                      AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
                result = await m._enqueue_cto_task("u1", "p1", "fix it")
        finally:
            _dbmod.set_db(None)
        assert result["ok"] is True
        assert result["project_id"] == "p1"

    @pytest.mark.asyncio
    async def test_success_with_background_tasks_arg(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        from fastapi import BackgroundTasks
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        _dbmod.set_db(fake_db)
        bg = BackgroundTasks()
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                      AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
                result = await m._enqueue_cto_task("u1", "p1", "fix it", bg=bg)
        finally:
            _dbmod.set_db(None)
        assert result["ok"] is True
        assert len(bg.tasks) == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_most_recent_project(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1", "created_at": 1})
        _dbmod.set_db(fake_db)
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                      AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
                result = await m._enqueue_cto_task("u1", "does-not-exist", "fix it")
        finally:
            _dbmod.set_db(None)
        assert result["ok"] is True
        assert result["project_id"] == "p1"


# ═════════════════════════════════════════════════════════════════════
# _run_rollback_via_api / _run_rollback_with_git (direct unit tests)
# ═════════════════════════════════════════════════════════════════════

class TestRollbackWorkers:
    @pytest.mark.asyncio
    async def test_rollback_log_no_db_noop(self):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        _dbmod.set_db(None)
        await m._rollback_log("t1", "some step")

    @pytest.mark.asyncio
    async def test_rollback_via_api_success(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_tasks.rows.append({"task_id": "t1"})
        _dbmod.set_db(fake_db)
        proj = {"github_owner": "acme", "github_repo": "widgets",
               "branch": "main", "user_id": "u1"}

        async def _fake_revert(**kw):
            await kw["progress"]("reverting…")
            return {"sha": "revsha123"}

        try:
            with patch("services.git_identity.resolve_git_identity",
                      AsyncMock(return_value=("dev", "dev@example.com"))), \
                 patch("routers.cto_projects.gh_api_revert", _fake_revert):
                await m._run_rollback_via_api("t1", proj, "abc123", "tok")
        finally:
            _dbmod.set_db(None)
        row = fake_db.cto_tasks.rows[0]
        assert row["rollback_status"] == "done"
        assert row["rollback_sha"] == "revsha123"

    @pytest.mark.asyncio
    async def test_rollback_via_api_failure_scrubs_token(self, fake_db):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_tasks.rows.append({"task_id": "t1"})
        _dbmod.set_db(fake_db)
        proj = {"github_owner": "acme", "github_repo": "widgets",
               "branch": "main", "user_id": "u1"}
        try:
            with patch("services.git_identity.resolve_git_identity",
                      AsyncMock(return_value=("dev", "dev@example.com"))), \
                 patch("routers.cto_projects.gh_api_revert",
                      AsyncMock(side_effect=RuntimeError("failed with secret-tok-value"))):
                await m._run_rollback_via_api("t1", proj, "abc123", "secret-tok-value")
        finally:
            _dbmod.set_db(None)
        row = fake_db.cto_tasks.rows[0]
        assert row["rollback_status"] == "failed"
        assert "secret-tok-value" not in row["rollback_error"]

    @pytest.mark.asyncio
    async def test_rollback_with_git_success(self, fake_db, tmp_path):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_tasks.rows.append({"task_id": "t1"})
        _dbmod.set_db(fake_db)
        proj = {"github_owner": "acme", "github_repo": "widgets",
               "branch": "main", "user_id": "u1"}

        def _fake_sh(cmd, cwd, timeout=60):
            import types
            if cmd[:2] == ["git", "rev-parse"]:
                return types.SimpleNamespace(returncode=0, stdout="abc1234\n", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        try:
            with patch("routers.cto_projects.WORKSPACE", tmp_path), \
                 patch("routers.cto_projects._sh", _fake_sh):
                await m._run_rollback_with_git("t1", proj, "abc123", "tok")
        finally:
            _dbmod.set_db(None)
        row = fake_db.cto_tasks.rows[0]
        assert row["rollback_status"] == "done"
        assert row["rollback_sha"] == "abc1234"

    @pytest.mark.asyncio
    async def test_rollback_with_git_clone_failure(self, fake_db, tmp_path):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_tasks.rows.append({"task_id": "t1"})
        _dbmod.set_db(fake_db)
        proj = {"github_owner": "acme", "github_repo": "widgets",
               "branch": "main", "user_id": "u1"}

        def _fake_sh(cmd, cwd, timeout=60):
            import types
            return types.SimpleNamespace(returncode=1, stdout="", stderr="auth failed with tok")

        try:
            with patch("routers.cto_projects.WORKSPACE", tmp_path), \
                 patch("routers.cto_projects._sh", _fake_sh):
                await m._run_rollback_with_git("t1", proj, "abc123", "tok")
        finally:
            _dbmod.set_db(None)
        row = fake_db.cto_tasks.rows[0]
        assert row["rollback_status"] == "failed"
        assert "tok" not in row["rollback_error"] or "***PAT***" in row["rollback_error"]

    @pytest.mark.asyncio
    async def test_rollback_with_git_retries_without_merge_flag(self, fake_db, tmp_path):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_tasks.rows.append({"task_id": "t1"})
        _dbmod.set_db(fake_db)
        proj = {"github_owner": "acme", "github_repo": "widgets",
               "branch": "main", "user_id": "u1"}
        calls = []

        def _fake_sh(cmd, cwd, timeout=60):
            import types
            calls.append(cmd)
            if cmd[:2] == ["git", "revert"] and "-m" in cmd:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="not a merge commit")
            if cmd[:2] == ["git", "rev-parse"]:
                return types.SimpleNamespace(returncode=0, stdout="abc1234\n", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        try:
            with patch("routers.cto_projects.WORKSPACE", tmp_path), \
                 patch("routers.cto_projects._sh", _fake_sh):
                await m._run_rollback_with_git("t1", proj, "abc123", "tok")
        finally:
            _dbmod.set_db(None)
        row = fake_db.cto_tasks.rows[0]
        assert row["rollback_status"] == "done"
        assert ["git", "revert", "--abort"] in calls

    @pytest.mark.asyncio
    async def test_rollback_with_git_revert_fails_both_attempts(self, fake_db, tmp_path):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_tasks.rows.append({"task_id": "t1"})
        _dbmod.set_db(fake_db)
        proj = {"github_owner": "acme", "github_repo": "widgets",
               "branch": "main", "user_id": "u1"}

        def _fake_sh(cmd, cwd, timeout=60):
            import types
            if cmd[:2] == ["git", "revert"] and cmd[2] != "--abort":
                return types.SimpleNamespace(returncode=1, stdout="",
                                             stderr="conflict with tok")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        try:
            with patch("routers.cto_projects.WORKSPACE", tmp_path), \
                 patch("routers.cto_projects._sh", _fake_sh):
                await m._run_rollback_with_git("t1", proj, "abc123", "tok")
        finally:
            _dbmod.set_db(None)
        row = fake_db.cto_tasks.rows[0]
        assert row["rollback_status"] == "failed"
        assert "conflict" in row["rollback_error"]

    @pytest.mark.asyncio
    async def test_rollback_with_git_push_fails(self, fake_db, tmp_path):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_tasks.rows.append({"task_id": "t1"})
        _dbmod.set_db(fake_db)
        proj = {"github_owner": "acme", "github_repo": "widgets",
               "branch": "main", "user_id": "u1"}

        def _fake_sh(cmd, cwd, timeout=60):
            import types
            if cmd[:2] == ["git", "push"]:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="remote rejected")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        try:
            with patch("routers.cto_projects.WORKSPACE", tmp_path), \
                 patch("routers.cto_projects._sh", _fake_sh):
                await m._run_rollback_with_git("t1", proj, "abc123", "tok")
        finally:
            _dbmod.set_db(None)
        row = fake_db.cto_tasks.rows[0]
        assert row["rollback_status"] == "failed"
        assert "push failed" in row["rollback_error"]

    @pytest.mark.asyncio
    async def test_rollback_with_git_no_token_scrub_returns_empty(self, fake_db, tmp_path):
        from routers import cto_projects as m
        from cto_services import db as _dbmod
        fake_db.cto_tasks.rows.append({"task_id": "t1"})
        _dbmod.set_db(fake_db)
        proj = {"github_owner": "acme", "github_repo": "widgets",
               "branch": "main", "user_id": "u1"}

        def _fake_sh(cmd, cwd, timeout=60):
            import types
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")

        try:
            with patch("routers.cto_projects.WORKSPACE", tmp_path), \
                 patch("routers.cto_projects._sh", _fake_sh):
                await m._run_rollback_with_git("t1", proj, "abc123", "")
        finally:
            _dbmod.set_db(None)
        row = fake_db.cto_tasks.rows[0]
        assert row["rollback_status"] == "failed"

    @pytest.mark.asyncio
    async def test_run_rollback_dispatches_to_git_path(self, fake_db):
        from routers import cto_projects as m
        with patch.object(m, "_GIT_AVAILABLE", True), \
             patch("routers.cto_projects._run_rollback_with_git",
                  AsyncMock(return_value=None)) as git_mock, \
             patch("routers.cto_projects._run_rollback_via_api",
                  AsyncMock(return_value=None)) as api_mock:
            await m._run_rollback("t1", {}, "abc123", "tok")
        git_mock.assert_awaited_once()
        api_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_rollback_dispatches_to_api_path(self, fake_db):
        from routers import cto_projects as m
        with patch.object(m, "_GIT_AVAILABLE", False), \
             patch("routers.cto_projects._run_rollback_with_git",
                  AsyncMock(return_value=None)) as git_mock, \
             patch("routers.cto_projects._run_rollback_via_api",
                  AsyncMock(return_value=None)) as api_mock:
            await m._run_rollback("t1", {}, "abc123", "tok")
        api_mock.assert_awaited_once()
        git_mock.assert_not_awaited()


# ═════════════════════════════════════════════════════════════════════
# _run_task dispatcher · _run_project_indexing · _emit · get_repo_token
# ═════════════════════════════════════════════════════════════════════

class TestMiscHelpers:
    def test_sh_runs_real_subprocess(self, tmp_path):
        from routers import cto_projects as m
        result = m._sh(["echo", "hello"], tmp_path, timeout=5)
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_load_design_system_missing_file_degrades_gracefully(self):
        from routers import cto_projects as m
        with patch("pathlib.Path.exists", side_effect=RuntimeError("disk error")):
            result = m._load_design_system()
        assert result == ""

    @pytest.mark.asyncio
    async def test_run_task_dispatches_to_git_path(self):
        from routers import cto_projects as m
        with patch.object(m, "_GIT_AVAILABLE", True), \
             patch("routers.cto_projects._run_task_with_git",
                  AsyncMock(return_value=None)) as git_mock, \
             patch("routers.cto_projects._run_task_via_api",
                  AsyncMock(return_value=None)) as api_mock:
            await m._run_task("t1", {}, "fix it", [], "", "tok")
        git_mock.assert_awaited_once()
        api_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_task_dispatches_to_api_path(self):
        from routers import cto_projects as m
        with patch.object(m, "_GIT_AVAILABLE", False), \
             patch("routers.cto_projects._run_task_with_git",
                  AsyncMock(return_value=None)) as git_mock, \
             patch("routers.cto_projects._run_task_via_api",
                  AsyncMock(return_value=None)) as api_mock:
            await m._run_task("t1", {}, "fix it", [], "", "tok")
        api_mock.assert_awaited_once()
        git_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_repo_token_delegates_to_pat_vault(self):
        from routers import cto_projects as m
        with patch("services.pat_vault.get_repo_token",
                  AsyncMock(return_value="tok123")):
            result = await m.get_repo_token({"project_id": "p1"})
        assert result == "tok123"

    @pytest.mark.asyncio
    async def test_run_project_indexing_success(self, fake_db):
        from routers import cto_projects as m
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.project_brain.build_brain_v2", AsyncMock(return_value={})):
            await m._run_project_indexing(
                db=fake_db, project_id="p1", user_id="u1",
                github_token="tok", github_owner="acme", github_repo="widgets",
                branch="main",
            )
        row = fake_db.cto_projects.rows[0]
        assert row["indexing_status"] == "ready"

    @pytest.mark.asyncio
    async def test_run_project_indexing_failure(self, fake_db):
        from routers import cto_projects as m
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        with patch("services.project_brain.build_brain_v2",
                  AsyncMock(side_effect=RuntimeError("scan crashed"))):
            await m._run_project_indexing(
                db=fake_db, project_id="p1", user_id="u1",
                github_token="tok", github_owner="acme", github_repo="widgets",
                branch="main",
            )
        row = fake_db.cto_projects.rows[0]
        assert row["indexing_status"] == "error"
        assert "scan crashed" in row["indexing_error"]

    @pytest.mark.asyncio
    async def test_emit_no_task_id_noop(self):
        from routers import cto_projects as m
        await m._emit("", "step")

    @pytest.mark.asyncio
    async def test_emit_pushes_frame(self):
        from routers import cto_projects as m
        m._task_queues.pop("t-emit-1", None)
        await m._emit("t-emit-1", "working on it", pct=50, extra_field="x")
        q = m._task_queues["t-emit-1"]
        frame = q.get_nowait()
        assert frame["step"] == "working on it"
        assert frame["extra_field"] == "x"

    @pytest.mark.asyncio
    async def test_emit_drops_oldest_when_full(self):
        from routers import cto_projects as m
        m._task_queues.pop("t-emit-2", None)
        for i in range(256):
            await m._emit("t-emit-2", f"step {i}")
        await m._emit("t-emit-2", "final step")
        q = m._task_queues["t-emit-2"]
        assert q.qsize() == 256
        first = q.get_nowait()
        assert first["step"] == "step 1"
