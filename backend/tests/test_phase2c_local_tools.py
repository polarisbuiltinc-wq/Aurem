"""Phase 2c coverage wave — backend/services/local_tools.py (2026-08-24).

Final file of the 5-file continuous coverage loop (admin_analytics.py,
loop_engine.py, chat.py, cto_projects.py all done before this one).

Real baseline (CONFIRMED, measured before writing anything new): 42
pre-existing test files already import `services.local_tools` directly
in-process. Running them together with `pytest --cov=services.local_tools`:

    services/local_tools.py   911 stmts, 424 missed, 53.46% covered
    527 passed, 33 failed (pre-existing — CONFIRMED not caused by this
    wave), 13 skipped, 16 deselected

All 33 pre-existing failures share the same root cause already
documented for every other Phase 2c wave in this loop: the GitHub-App
/PAT-removal migration issue (legacy PAT-only test fixtures no longer
resolve a repo credential). Confirmed unrelated — zero app code was
touched in this wave, only this new test file was added.

This wave targets the pure/near-pure context-resolution helpers
(`_is_safe_repo_path`, `_run_syntax_check`, `_resolve_project`,
`_repo_ctx_from`, `_verify_ctx`) plus the two smallest full tool
functions that are easy to exercise end-to-end without a real GitHub
App installation (`get_commit_diff`, `get_repo_info`, `save_finding`'s
guard clauses) — already comfortably above the 60% floor from the
53.46% baseline without needing to touch the much larger, riskier
`write_repo_file` / `list_repo_files` / `execute_bash` bodies.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class _FakeBinCtx:
    def __init__(self, bin_id="u1", repo_owner="acme", repo_name="widgets",
                branch="main", pat="tok", is_founder=False, pid="p1",
                ora_boundary_active=True):
        self.bin_id = bin_id
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.branch = branch
        self.pat = pat
        self.is_founder = is_founder
        self.pid = pid
        self.ora_boundary_active = ora_boundary_active


class _FakeResp:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json = json_body or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=self)


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def find_one(self, query=None, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in (query or {}).items()):
                return dict(r)
        return None


class _FakeDB:
    def __init__(self, projects=None):
        self.cto_projects = _FakeCollection(projects or [])


# ═════════════════════════════════════════════════════════════════════
# _is_safe_repo_path
# ═════════════════════════════════════════════════════════════════════

class TestIsSafeRepoPath:
    def test_empty_path_unsafe(self):
        from services import local_tools as m
        assert m._is_safe_repo_path("") is False

    def test_non_string_unsafe(self):
        from services import local_tools as m
        assert m._is_safe_repo_path(None) is False

    def test_normal_path_safe(self):
        from services import local_tools as m
        assert m._is_safe_repo_path("src/app.py") is True

    def test_shell_metachar_unsafe(self):
        from services import local_tools as m
        assert m._is_safe_repo_path("src/app.py; rm -rf /") is False

    def test_backtick_unsafe(self):
        from services import local_tools as m
        assert m._is_safe_repo_path("`whoami`.py") is False


# ═════════════════════════════════════════════════════════════════════
# _run_syntax_check
# ═════════════════════════════════════════════════════════════════════

class TestRunSyntaxCheck:
    def test_empty_content_skipped(self):
        from services import local_tools as m
        result = m._run_syntax_check(content="", file_path="x.py", ext=".py")
        assert result == {"has_errors": False, "errors": "",
                          "skipped": True, "reason": "empty_content"}

    def test_valid_python_no_errors(self):
        from services import local_tools as m
        result = m._run_syntax_check(content="x = 1\nprint(x)\n",
                                     file_path="x.py", ext=".py")
        assert result["has_errors"] is False

    def test_invalid_python_has_errors(self):
        from services import local_tools as m
        result = m._run_syntax_check(content="def f(:\n    pass\n",
                                     file_path="x.py", ext=".py")
        assert result["has_errors"] is True
        assert result["errors"]

    def test_valid_js_no_errors(self):
        from services import local_tools as m
        result = m._run_syntax_check(content="const x = 1;\nconsole.log(x);\n",
                                     file_path="x.js", ext=".js")
        assert result["has_errors"] is False

    def test_invalid_js_has_errors(self):
        from services import local_tools as m
        result = m._run_syntax_check(content="function f( {\n",
                                     file_path="x.js", ext=".js")
        assert result["has_errors"] is True

    def test_ts_parse_error_flagged(self):
        from services import local_tools as m
        import subprocess
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="x.ts(1,1): error TS1005: ';' expected.",
            stderr="")
        with patch("subprocess.run", return_value=fake_result):
            result = m._run_syntax_check(content="const x =", file_path="x.ts", ext=".ts")
        assert result["has_errors"] is True

    def test_ts_type_error_only_not_blocked(self):
        from services import local_tools as m
        import subprocess
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=1,
            stdout="x.ts(1,1): error TS2322: Type mismatch.", stderr="")
        with patch("subprocess.run", return_value=fake_result):
            result = m._run_syntax_check(content="const x: number = 'a';",
                                         file_path="x.ts", ext=".ts")
        assert result["has_errors"] is False
        assert result["skipped"] is True
        assert result["reason"] == "ts_only_type_errors"

    def test_unknown_ext_no_check(self):
        from services import local_tools as m
        result = m._run_syntax_check(content="hello", file_path="x.md", ext=".md")
        assert result == {"has_errors": False, "errors": "", "skipped": False}

    def test_tool_missing_falls_open(self):
        from services import local_tools as m
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = m._run_syntax_check(content="x=1", file_path="x.py", ext=".py")
        assert result["has_errors"] is False
        assert result["skipped"] is True
        assert result["reason"] == "py_check:FileNotFoundError"

    def test_tool_timeout_falls_open(self):
        from services import local_tools as m
        import subprocess
        with patch("subprocess.run",
                  side_effect=subprocess.TimeoutExpired(cmd="node", timeout=10)):
            result = m._run_syntax_check(content="x=1", file_path="x.js", ext=".js")
        assert result["skipped"] is True
        assert result["reason"] == "js_check:TimeoutExpired"

    def test_tmp_write_failure_falls_open(self):
        from services import local_tools as m
        with patch("tempfile.NamedTemporaryFile", side_effect=OSError("disk full")):
            result = m._run_syntax_check(content="x=1", file_path="x.py", ext=".py")
        assert result["skipped"] is True
        assert "tmp_write" in result["reason"]


# ═════════════════════════════════════════════════════════════════════
# _resolve_project
# ═════════════════════════════════════════════════════════════════════

class TestResolveProject:
    @pytest.mark.asyncio
    async def test_no_user_id_returns_none(self):
        from services import local_tools as m
        assert await m._resolve_project("", "p1") is None

    @pytest.mark.asyncio
    async def test_no_db_returns_none(self):
        from services import local_tools as m
        from cto_services import db as _dbmod
        _dbmod.set_db(None)
        assert await m._resolve_project("u1", "p1") is None

    @pytest.mark.asyncio
    async def test_empty_project_id_returns_none(self):
        from services import local_tools as m
        from cto_services import db as _dbmod
        _dbmod.set_db(_FakeDB())
        try:
            assert await m._resolve_project("u1", "") is None
        finally:
            _dbmod.set_db(None)

    @pytest.mark.asyncio
    async def test_home_project_id_returns_none(self):
        from services import local_tools as m
        from cto_services import db as _dbmod
        _dbmod.set_db(_FakeDB())
        try:
            assert await m._resolve_project("u1", "home") is None
        finally:
            _dbmod.set_db(None)

    @pytest.mark.asyncio
    async def test_project_not_found_returns_none(self):
        from services import local_tools as m
        from cto_services import db as _dbmod
        _dbmod.set_db(_FakeDB())
        try:
            assert await m._resolve_project("u1", "p1") is None
        finally:
            _dbmod.set_db(None)

    @pytest.mark.asyncio
    async def test_found_attaches_decrypted_token(self):
        from services import local_tools as m
        from cto_services import db as _dbmod
        _dbmod.set_db(_FakeDB(projects=[{"project_id": "p1", "user_id": "u1"}]))
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                      AsyncMock(return_value=("tok123", None, None))):
                proj = await m._resolve_project("u1", "p1")
        finally:
            _dbmod.set_db(None)
        assert proj["github_token"] == "tok123"

    @pytest.mark.asyncio
    async def test_found_auth_error_logs_and_returns_none_token(self):
        from services import local_tools as m
        from cto_services import db as _dbmod
        _dbmod.set_db(_FakeDB(projects=[{"project_id": "p1", "user_id": "u1"}]))
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                      AsyncMock(return_value=(None, "app_installation_missing", "detail"))):
                proj = await m._resolve_project("u1", "p1")
        finally:
            _dbmod.set_db(None)
        assert proj["github_token"] is None


# ═════════════════════════════════════════════════════════════════════
# _repo_ctx_from
# ═════════════════════════════════════════════════════════════════════

class TestRepoCtxFrom:
    def test_no_bin_ctx_returns_none(self):
        from services import local_tools as m
        assert m._repo_ctx_from({}) is None

    def test_cross_user_mismatch_returns_none(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(bin_id="u1"), "user_id": "u2"}
        assert m._repo_ctx_from(ctx) is None

    def test_missing_owner_or_repo_returns_none(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(repo_owner="", repo_name=""), "user_id": "u1"}
        assert m._repo_ctx_from(ctx) is None

    def test_valid_ctx_returns_normalised_dict(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        rc = m._repo_ctx_from(ctx)
        assert rc["ok"] is True
        assert rc["owner"] == "acme"
        assert rc["repo"] == "widgets"
        assert rc["branch"] == "main"

    def test_no_branch_defaults_to_main(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(branch=None), "user_id": "u1"}
        rc = m._repo_ctx_from(ctx)
        assert rc["branch"] == "main"


# ═════════════════════════════════════════════════════════════════════
# _verify_ctx
# ═════════════════════════════════════════════════════════════════════

class TestVerifyCtx:
    def test_no_bin_ctx_returns_none(self):
        from services import local_tools as m
        assert m._verify_ctx({}) is None

    def test_cross_user_mismatch_returns_none(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(bin_id="u1"), "user_id": "u2"}
        assert m._verify_ctx(ctx) is None

    def test_boundary_off_non_founder_refused(self):
        from services import local_tools as m
        bc = _FakeBinCtx(ora_boundary_active=False, is_founder=False)
        ctx = {"bin_ctx": bc, "user_id": "u1"}
        assert m._verify_ctx(ctx) is None

    def test_boundary_off_founder_allowed(self):
        from services import local_tools as m
        bc = _FakeBinCtx(ora_boundary_active=False, is_founder=True)
        ctx = {"bin_ctx": bc, "user_id": "u1"}
        assert m._verify_ctx(ctx) is bc

    def test_normal_boundary_on_allowed(self):
        from services import local_tools as m
        bc = _FakeBinCtx(ora_boundary_active=True, is_founder=False)
        ctx = {"bin_ctx": bc, "user_id": "u1"}
        assert m._verify_ctx(ctx) is bc


# ═════════════════════════════════════════════════════════════════════
# get_commit_diff
# ═════════════════════════════════════════════════════════════════════

class TestGetCommitDiff:
    @pytest.mark.asyncio
    async def test_missing_sha(self):
        from services import local_tools as m
        result = await m.get_commit_diff({}, {})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_no_bin_ctx(self):
        from services import local_tools as m
        result = await m.get_commit_diff({}, {"sha": "abc1234"})
        assert result == m._NO_BIN_CTX_ERROR

    @pytest.mark.asyncio
    async def test_missing_owner_or_repo(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(repo_owner="acme", repo_name="widgets"),
              "user_id": "u1"}
        with patch("services.local_tools._repo_ctx_from",
                  return_value={"ok": True, "owner": "", "repo": "", "token": "tok"}):
            result = await m.get_commit_diff(ctx, {"sha": "abc1234"})
        assert result["ok"] is False
        assert "missing" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_commit_not_found_404(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_FakeResp(404))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("services.http.ext_client", return_value=mock_client):
            result = await m.get_commit_diff(ctx, {"sha": "abc1234"})
        assert result["ok"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_network_error(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.http.ext_client", side_effect=RuntimeError("net down")):
            result = await m.get_commit_diff(ctx, {"sha": "abc1234"})
        assert result["ok"] is False
        assert "GitHub API error" in result["error"]

    @pytest.mark.asyncio
    async def test_success(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        commit_json = {
            "commit": {"message": "fix the bug",
                       "author": {"name": "dev", "date": "2026-08-24"}},
            "files": [
                {"filename": "x.py", "status": "modified", "additions": 3,
                 "deletions": 1, "patch": "@@ -1 +1 @@"},
            ],
        }
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_FakeResp(200, commit_json))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("services.http.ext_client", return_value=mock_client):
            result = await m.get_commit_diff(ctx, {"sha": "abc1234567"})
        assert result["ok"] is True
        assert result["sha"] == "abc1234"
        assert result["message"] == "fix the bug"
        assert result["total_files"] == 1
        assert result["files_changed"][0]["path"] == "x.py"


# ═════════════════════════════════════════════════════════════════════
# get_repo_info
# ═════════════════════════════════════════════════════════════════════

class TestGetRepoInfo:
    @pytest.mark.asyncio
    async def test_no_bin_ctx(self):
        from services import local_tools as m
        result = await m.get_repo_info({}, {})
        assert result == m._NO_BIN_CTX_ERROR

    @pytest.mark.asyncio
    async def test_no_db_returns_basic_info(self):
        from services import local_tools as m
        from cto_services import db as _dbmod
        _dbmod.set_db(None)
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        try:
            result = await m.get_repo_info(ctx, {})
        finally:
            _dbmod.set_db(None)
        assert result["ok"] is True
        assert result["github_owner"] == "acme"
        assert "name" not in result

    @pytest.mark.asyncio
    async def test_db_lookup_adds_extra_metadata(self):
        from services import local_tools as m
        from cto_services import db as _dbmod
        _dbmod.set_db(_FakeDB(projects=[{
            "project_id": "p1", "user_id": "u1", "name": "Widgets",
            "tech_stack": "python", "last_task": "fix bug", "tasks_done": 5,
        }]))
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        try:
            result = await m.get_repo_info(ctx, {})
        finally:
            _dbmod.set_db(None)
        assert result["name"] == "Widgets"
        assert result["tasks_done"] == 5

    @pytest.mark.asyncio
    async def test_db_lookup_exception_swallowed(self):
        from services import local_tools as m
        from cto_services import db as _dbmod

        class _Boom:
            async def find_one(self, *a, **k):
                raise RuntimeError("mongo down")

        db = _FakeDB()
        db.cto_projects = _Boom()
        _dbmod.set_db(db)
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        try:
            result = await m.get_repo_info(ctx, {})
        finally:
            _dbmod.set_db(None)
        assert result["ok"] is True
        assert "name" not in result

    @pytest.mark.asyncio
    async def test_has_pat_reflects_token_presence(self):
        from services import local_tools as m
        from cto_services import db as _dbmod
        _dbmod.set_db(None)
        ctx = {"bin_ctx": _FakeBinCtx(pat=None), "user_id": "u1"}
        try:
            result = await m.get_repo_info(ctx, {})
        finally:
            _dbmod.set_db(None)
        assert result["has_pat"] is False


# ═════════════════════════════════════════════════════════════════════
# save_finding — guard clauses only
# ═════════════════════════════════════════════════════════════════════

class TestSaveFindingGuards:
    @pytest.mark.asyncio
    async def test_no_bin_ctx_rejected(self):
        from services import local_tools as m
        result = await m.save_finding({}, {"title": "x", "severity": "high",
                                            "description": "d"})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_missing_required_fields_rejected(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        result = await m.save_finding(ctx, {})
        assert result["ok"] is False
