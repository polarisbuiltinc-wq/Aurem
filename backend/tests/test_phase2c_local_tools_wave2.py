"""Phase 2c coverage wave 2 — backend/services/local_tools.py (2026-08-25).

Wave 1 (test_phase2c_local_tools.py) covered the pure context-resolution
helpers and the two smallest full tool functions. Baseline measured
before this wave (real `pytest --cov`, 39 pre-existing files + wave-1):

    services/local_tools.py   911 stmts, 345 missed, 62%
    531 passed, 25 pre-existing failures (GitHub-App/PAT-migration
    fixture issue, same root cause documented for every other Phase
    2c/B wave — confirmed unrelated, zero app code touched before this
    wave's baseline measurement), 13 skipped, 15 deselected.

This wave targets the largest remaining gaps, all reasonably testable
with mocks (no heavy-I/O exception needed for this file):
  * `read_repo_files` — had ZERO coverage before this wave.
  * `write_repo_file`'s remaining validation/vanguard/syntax-gate/
    commit-crash/cache-invalidation branches.
  * `list_repo_files`'s remaining owner/repo-missing, 401/403,
    subtree-fallback, pattern-filter and truncation-note branches.
  * `_search_repo_via_api` (called directly — it's a plain private
    function, no need to go through the higher-level `search_repo`
    dispatch) — network-error, subtree-filter and ext-filter branches.
  * `_ensure_repo_snapshot` — cache-hit, ref-check-failure and
    tarball-download-failure branches (a REAL small gzip tarball is
    built in-memory for the extraction-success test — genuine local
    disk/tarfile I/O, only the network layer is mocked).
  * `save_finding`'s int-conversion, no-db and persist-crash branches.
  * `execute_bash`'s non-founder-with-bin_ctx, shlex-parse-error and
    subprocess-timeout branches.
"""
from __future__ import annotations

import gzip
import io
import tarfile

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


def _mock_ext_client(get_return=None, get_side_effect=None):
    mock_client = MagicMock()
    if get_side_effect is not None:
        mock_client.get = AsyncMock(side_effect=get_side_effect)
    else:
        mock_client.get = AsyncMock(return_value=get_return)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ═════════════════════════════════════════════════════════════════════
# read_repo_files — previously ZERO coverage
# ═════════════════════════════════════════════════════════════════════

class TestReadRepoFiles:
    @pytest.mark.asyncio
    async def test_missing_paths_arg(self):
        from services import local_tools as m
        result = await m.read_repo_files({}, {})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_no_bin_ctx(self):
        from services import local_tools as m
        result = await m.read_repo_files({}, {"paths": ["a.py"]})
        assert result == m._NO_BIN_CTX_ERROR

    @pytest.mark.asyncio
    async def test_missing_owner_or_repo(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.local_tools._repo_ctx_from",
                  return_value={"ok": True, "owner": "", "repo": "", "branch": "main", "token": "tok"}):
            result = await m.read_repo_files(ctx, {"paths": ["a.py"]})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_success_and_dropped_paths_warning(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        many_paths = [f"file{i}.py" for i in range(9)]  # > MAX_FILES_BULK=6
        with patch("services.local_tools._gh_fetch_file",
                  AsyncMock(return_value="print('hi')")):
            result = await m.read_repo_files(ctx, {"paths": many_paths})
        assert result["ok"] is True
        assert result["fetched"] == 6
        assert len(result["dropped"]) == 3
        assert "warning" in result and "HARD-CAPS" in result["warning"]

    @pytest.mark.asyncio
    async def test_hallucination_warning_when_majority_404(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}

        async def _fetch(owner, repo, path, branch, token):
            return None if path != "real.py" else "content"
        with patch("services.local_tools._gh_fetch_file", AsyncMock(side_effect=_fetch)):
            result = await m.read_repo_files(
                ctx, {"paths": ["a.py", "b.py", "real.py"]})
        assert result["ok"] is True
        assert "HALLUCINATION RISK" in result["warning"]

    @pytest.mark.asyncio
    async def test_github_auth_error_in_one_file(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.local_tools._gh_fetch_file",
                  AsyncMock(side_effect=m.GithubAuthError(401))):
            result = await m.read_repo_files(ctx, {"paths": ["a.py"]})
        assert result["fetched"] == 0
        assert result["files"][0]["ok"] is False

    @pytest.mark.asyncio
    async def test_generic_exception_in_one_file(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.local_tools._gh_fetch_file",
                  AsyncMock(side_effect=RuntimeError("network blip"))):
            result = await m.read_repo_files(ctx, {"paths": ["a.py"]})
        assert result["files"][0]["error"] == "network blip"

    @pytest.mark.asyncio
    async def test_invalid_path_in_list_is_flagged(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.local_tools._gh_fetch_file",
                  AsyncMock(return_value="ok")):
            result = await m.read_repo_files(ctx, {"paths": ["/etc/passwd"]})
        assert result["files"][0]["error"] == "Invalid path"


# ═════════════════════════════════════════════════════════════════════
# write_repo_file — remaining branches
# ═════════════════════════════════════════════════════════════════════

class TestWriteRepoFileRemaining:
    @pytest.mark.asyncio
    async def test_missing_path(self):
        from services import local_tools as m
        result = await m.write_repo_file({}, {"content": "x"})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_unsafe_path_chars_rejected(self):
        from services import local_tools as m
        result = await m.write_repo_file({}, {"path": "a;rm -rf.py", "content": "x"})
        assert result["ok"] is False
        assert "letters, digits" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_owner_or_repo(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.local_tools._repo_ctx_from",
                  return_value={"ok": True, "owner": "", "repo": "", "branch": "main", "token": "tok", "pid": "p1"}):
            result = await m.write_repo_file(ctx, {"path": "a.py", "content": "x"})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_missing_token_returns_actionable_error(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(pat=None), "user_id": "u1"}
        result = await m.write_repo_file(ctx, {"path": "a.py", "content": "x"})
        assert result["ok"] is False
        assert "GitHub App access is unavailable" in result["error"]

    @pytest.mark.asyncio
    async def test_vanguard_blocks_critical_finding(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        findings = [{"name": "hardcoded_secret", "severity": "CRITICAL",
                    "filepath": "a.py", "line": 3}]
        with patch("services.vanguard_scanner.scan_file_blocks", return_value=findings), \
             patch("services.vanguard_scanner.has_critical_or_high", return_value=True):
            result = await m.write_repo_file(ctx, {"path": "a.py", "content": "SECRET=1"})
        assert result["ok"] is False
        assert "Vanguard blocked" in result["error"]

    @pytest.mark.asyncio
    async def test_syntax_gate_blocks_invalid_python(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.vanguard_scanner.scan_file_blocks", return_value=[]), \
             patch("services.vanguard_scanner.has_critical_or_high", return_value=False):
            result = await m.write_repo_file(ctx, {"path": "a.py", "content": "def f(:\n"})
        assert result["ok"] is False
        assert result["error"] == "syntax_gate_blocked"

    @pytest.mark.asyncio
    async def test_db_lookup_crash_for_identity_is_swallowed(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("cto_services.db.get_db", side_effect=RuntimeError("db down")), \
             patch("services.git_identity.resolve_git_identity",
                  AsyncMock(return_value=("Dev", "dev@example.com"))), \
             patch("services.git_identity.build_commit_message",
                  return_value="chore: edit a.py"), \
             patch("services.github_api_writer.commit_files",
                  AsyncMock(return_value={"sha": "abc123", "html_url": "https://x"})):
            result = await m.write_repo_file(ctx, {"path": "a.py", "content": "print(1)\n"})
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_commit_files_crash_is_reported(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.git_identity.resolve_git_identity",
                  AsyncMock(return_value=("Dev", "dev@example.com"))), \
             patch("services.git_identity.build_commit_message",
                  return_value="chore: edit a.py"), \
             patch("services.github_api_writer.commit_files",
                  AsyncMock(side_effect=RuntimeError("GitHub 500"))):
            result = await m.write_repo_file(ctx, {"path": "a.py", "content": "print(1)\n"})
        assert result["ok"] is False
        assert "Commit failed at the GitHub API layer" in result["error"]

    @pytest.mark.asyncio
    async def test_vanguard_scan_crash_is_swallowed_not_blocking(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.vanguard_scanner.scan_file_blocks",
                  side_effect=RuntimeError("scanner infra down")), \
             patch("services.git_identity.resolve_git_identity",
                  AsyncMock(return_value=("Dev", "dev@example.com"))), \
             patch("services.git_identity.build_commit_message",
                  return_value="chore: edit a.py"), \
             patch("services.github_api_writer.commit_files",
                  AsyncMock(return_value={"sha": "abc123", "html_url": "https://x"})):
            result = await m.write_repo_file(ctx, {"path": "a.py", "content": "print(1)\n"})
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_syntax_gate_skipped_falls_open(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.vanguard_scanner.scan_file_blocks", return_value=[]), \
             patch("services.vanguard_scanner.has_critical_or_high", return_value=False), \
             patch("services.local_tools._run_syntax_check",
                  return_value={"has_errors": False, "skipped": True, "reason": "tool missing"}), \
             patch("services.git_identity.resolve_git_identity",
                  AsyncMock(return_value=("Dev", "dev@example.com"))), \
             patch("services.git_identity.build_commit_message",
                  return_value="chore: edit a.py"), \
             patch("services.github_api_writer.commit_files",
                  AsyncMock(return_value={"sha": "abc123", "html_url": "https://x"})):
            result = await m.write_repo_file(ctx, {"path": "a.py", "content": "print(1)\n"})
        assert result["ok"] is True


# ═════════════════════════════════════════════════════════════════════
# search_repo — dispatcher, previously ZERO coverage
# ═════════════════════════════════════════════════════════════════════

class TestSearchRepoDispatcher:
    @pytest.mark.asyncio
    async def test_missing_pattern(self):
        from services import local_tools as m
        result = await m.search_repo({}, {})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_no_bin_ctx(self):
        from services import local_tools as m
        result = await m.search_repo({}, {"pattern": "foo"})
        assert result == m._NO_BIN_CTX_ERROR

    @pytest.mark.asyncio
    async def test_invalid_regex_falls_back_to_escaped_literal(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.local_tools._ensure_repo_snapshot",
                  AsyncMock(return_value=(None, "head_sha_unavailable"))), \
             patch("services.local_tools._search_repo_via_api",
                  AsyncMock(return_value={"ok": True, "matches": []})):
            result = await m.search_repo(ctx, {"pattern": "["})
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_snapshot_search_crash_falls_back_to_api(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.local_tools._ensure_repo_snapshot",
                  AsyncMock(return_value=("/tmp/some-snapshot", None))), \
             patch("services.local_tools._search_snapshot_sync",
                  side_effect=RuntimeError("disk read error")), \
             patch("services.local_tools._search_repo_via_api",
                  AsyncMock(return_value={"ok": True, "matches": [], "source": "github_api_fallback"})) as api_mock:
            result = await m.search_repo(ctx, {"pattern": "foo"})
        assert result["ok"] is True
        assert api_mock.await_count == 1
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.git_identity.resolve_git_identity",
                  AsyncMock(return_value=("Dev", "dev@example.com"))), \
             patch("services.git_identity.build_commit_message",
                  return_value="chore: edit a.py"), \
             patch("services.github_api_writer.commit_files",
                  AsyncMock(return_value={"sha": "abc123", "html_url": "https://x"})), \
             patch("services.local_tools._cache_invalidate",
                  side_effect=RuntimeError("cache boom")), \
             patch("services.github_cache.invalidate_repo",
                  side_effect=RuntimeError("cache boom 2")):
            result = await m.write_repo_file(ctx, {"path": "a.py", "content": "print(1)\n"})
        assert result["ok"] is True
        assert result["sha"] == "abc123"


# ═════════════════════════════════════════════════════════════════════
# list_repo_files — remaining branches
# ═════════════════════════════════════════════════════════════════════

class TestListRepoFilesRemaining:
    @pytest.mark.asyncio
    async def test_missing_owner_or_repo(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.local_tools._repo_ctx_from",
                  return_value={"ok": True, "owner": "", "repo": "", "branch": "main", "token": "tok"}):
            result = await m.list_repo_files(ctx, {})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_401_returns_revoked_error(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        mock_client = _mock_ext_client(get_return=_FakeResp(401))
        with patch("services.http.ext_client", return_value=mock_client):
            result = await m.list_repo_files(ctx, {})
        assert result["ok"] is False
        assert result.get("error_class") is not None or "revoked" in str(result).lower() \
            or "installation" in str(result).lower()

    @pytest.mark.asyncio
    async def test_network_error_returns_clean_message(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.http.ext_client", side_effect=RuntimeError("net down")):
            result = await m.list_repo_files(ctx, {})
        assert result["ok"] is False
        assert "GitHub tree fetch failed" in result["error"]

    @pytest.mark.asyncio
    async def test_subtree_filter_and_pattern_and_truncation_note(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        tree_json = {
            "truncated": True,
            "tree": [
                {"path": "backend/a.py", "type": "blob"},
                {"path": "backend/b.py", "type": "blob"},
                {"path": "backend/c.js", "type": "blob"},
                {"path": "frontend/d.py", "type": "blob"},
            ],
        }
        mock_client = _mock_ext_client(get_return=_FakeResp(200, tree_json))
        with patch("services.http.ext_client", return_value=mock_client):
            result = await m.list_repo_files(ctx, {"path": "backend", "pattern": "*.py", "max": 1})
        assert result["ok"] is True
        assert result["tree"] == ["backend/a.py"]
        assert result["total"] == 2
        assert result["truncated"] is True
        assert result["gh_truncated"] is True
        assert "narrow" in result["note"]
        assert "truncated this recursive tree" in result["note"]

    @pytest.mark.asyncio
    async def test_truncated_tree_rescue_fallback_to_contents_walk(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        tree_json = {"truncated": True, "tree": [{"path": "other/x.py", "type": "blob"}]}
        mock_client = _mock_ext_client(get_return=_FakeResp(200, tree_json))
        with patch("services.http.ext_client", return_value=mock_client), \
             patch("services.local_tools._fetch_subtree_contents",
                  AsyncMock(return_value=["backend/pillars/a.py"])):
            result = await m.list_repo_files(ctx, {"path": "backend/pillars"})
        assert result["source"] == "contents_walk_fallback"
        assert result["tree"] == ["backend/pillars/a.py"]


# ═════════════════════════════════════════════════════════════════════
# _search_repo_via_api — called directly (plain private function)
# ═════════════════════════════════════════════════════════════════════

class TestSearchRepoViaApi:
    @pytest.mark.asyncio
    async def test_network_error(self):
        from services import local_tools as m
        with patch("services.http.ext_client", side_effect=RuntimeError("net down")):
            result = await m._search_repo_via_api(
                owner="acme", repo="widgets", branch="main", token="tok",
                pattern="foo", compiled=None, sub_path="", ext="", max_files=20,
            )
        assert result["ok"] is False
        assert "GitHub tree fetch failed" in result["error"]

    @pytest.mark.asyncio
    async def test_subtree_and_ext_filters_narrow_the_search(self):
        from services import local_tools as m
        tree_json = {
            "truncated": False,
            "tree": [
                {"path": "backend/a.py", "type": "blob"},
                {"path": "backend/b.js", "type": "blob"},
                {"path": "frontend/c.py", "type": "blob"},
            ],
        }
        mock_client = _mock_ext_client(get_return=_FakeResp(200, tree_json))
        import re as _re
        compiled = _re.compile("nomatch")
        with patch("services.http.ext_client", return_value=mock_client), \
             patch("services.local_tools._gh_fetch_file",
                  AsyncMock(return_value="some file content\n")):
            result = await m._search_repo_via_api(
                owner="acme", repo="widgets", branch="main", token="tok",
                pattern="nomatch", compiled=compiled, sub_path="backend",
                ext=".py", max_files=20,
            )
        assert result["ok"] is True
        assert result["matches"] == []
        # only backend/a.py qualifies (sub_path="backend" + ext=".py")
        assert result["files_fetched"] == 1


# ═════════════════════════════════════════════════════════════════════
# _ensure_repo_snapshot — cache-hit, ref-failure, download-failure,
# and one real-tarball extraction-success path
# ═════════════════════════════════════════════════════════════════════

class TestEnsureRepoSnapshot:
    @pytest.mark.asyncio
    async def test_head_sha_unavailable_no_stale_cache(self, tmp_path, monkeypatch):
        from services import local_tools as m
        monkeypatch.setattr(m, "_SNAPSHOT_ROOT", str(tmp_path))
        with patch("services.local_tools._repo_head_sha", AsyncMock(return_value=None)):
            dest, err = await m._ensure_repo_snapshot("acme", "widgets", "main", "tok")
        assert dest is None
        assert err == "head_sha_unavailable"

    @pytest.mark.asyncio
    async def test_cache_hit_returns_existing_dir_without_download(self, tmp_path, monkeypatch):
        from services import local_tools as m
        monkeypatch.setattr(m, "_SNAPSHOT_ROOT", str(tmp_path))
        import os
        key = "acme__widgets__main"
        dest = os.path.join(str(tmp_path), key)
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, ".aurem_head_sha"), "w") as fh:
            fh.write("headsha123")
        with patch("services.local_tools._repo_head_sha",
                  AsyncMock(return_value="headsha123")):
            result_dest, err = await m._ensure_repo_snapshot("acme", "widgets", "main", "tok")
        assert err is None
        assert result_dest == dest

    @pytest.mark.asyncio
    async def test_tarball_non_200_status_reported(self, tmp_path, monkeypatch):
        from services import local_tools as m
        monkeypatch.setattr(m, "_SNAPSHOT_ROOT", str(tmp_path))

        class _Stream:
            def __init__(self, status_code):
                self.status_code = status_code

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def aiter_bytes(self):
                return
                yield  # pragma: no cover

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=_Stream(404))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("services.local_tools._repo_head_sha",
                  AsyncMock(return_value="newsha")), \
             patch("services.http.ext_client", return_value=mock_client):
            dest, err = await m._ensure_repo_snapshot("acme", "widgets", "main", "tok")
        assert dest is None
        assert err == "tarball_status_404"

    @pytest.mark.asyncio
    async def test_real_tarball_extracts_successfully(self, tmp_path, monkeypatch):
        from services import local_tools as m
        monkeypatch.setattr(m, "_SNAPSHOT_ROOT", str(tmp_path))

        # Build a REAL gzip tarball in-memory with one root dir + one file,
        # mirroring GitHub's tarball layout (repo-sha/file.py).
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="acme-widgets-abc123/hello.py")
            data = b"print('hi')\n"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        tar_bytes = tar_buf.getvalue()

        class _Stream:
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def aiter_bytes(self):
                yield tar_bytes

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=_Stream())
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("services.local_tools._repo_head_sha",
                  AsyncMock(return_value="newsha")), \
             patch("services.http.ext_client", return_value=mock_client):
            dest, err = await m._ensure_repo_snapshot("acme", "widgets", "main", "tok")
        assert err is None
        assert dest is not None
        import os
        assert os.path.exists(os.path.join(dest, "hello.py"))
        assert os.path.exists(os.path.join(dest, ".aurem_head_sha"))


# ═════════════════════════════════════════════════════════════════════
# save_finding — remaining branches
# ═════════════════════════════════════════════════════════════════════

class TestSaveFindingRemaining:
    @pytest.mark.asyncio
    async def test_non_numeric_line_defaults_to_zero(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.local_tools.get_db", return_value=MagicMock()), \
             patch("services.loop_full_scan.persist_findings_to_backlog",
                  AsyncMock(return_value=1)):
            result = await m.save_finding(ctx, {
                "title": "SQL injection", "severity": "high", "line": "not-a-number",
            })
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_no_db_returns_error(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.local_tools.get_db", return_value=None):
            result = await m.save_finding(ctx, {"title": "XSS", "severity": "medium"})
        assert result["ok"] is False
        assert "database unavailable" in result["error"]

    @pytest.mark.asyncio
    async def test_persist_crash_is_reported(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}
        with patch("services.local_tools.get_db", return_value=MagicMock()), \
             patch("services.loop_full_scan.persist_findings_to_backlog",
                  AsyncMock(side_effect=RuntimeError("mongo down"))):
            result = await m.save_finding(ctx, {"title": "XSS", "severity": "medium"})
        assert result["ok"] is False
        assert "failed to save finding" in result["error"]


# ═════════════════════════════════════════════════════════════════════
# execute_bash — remaining branches
# ═════════════════════════════════════════════════════════════════════

class TestExecuteBashRemaining:
    @pytest.mark.asyncio
    async def test_founder_ctx_but_non_founder_bin_ctx_rejected(self):
        from services import local_tools as m
        ctx = {"bin_ctx": _FakeBinCtx(is_founder=False), "user_id": "u1",
              "is_founder": True}
        result = await m.execute_bash(ctx, {"command": "echo hello"})
        assert result["ok"] is False
        assert "restricted to founder" in result["error"]

    @pytest.mark.asyncio
    async def test_shlex_parse_error_on_unbalanced_quotes(self):
        from services import local_tools as m
        ctx = {"bin_ctx": None, "user_id": "u1", "is_founder": True}
        result = await m.execute_bash(ctx, {"command": "echo 'unbalanced"})
        assert result["ok"] is False
        assert "shell parse error" in result["error"]

    @pytest.mark.asyncio
    async def test_subprocess_timeout_is_reported(self):
        from services import local_tools as m
        import asyncio as _asyncio
        ctx = {"bin_ctx": None, "user_id": "u1", "is_founder": True}
        with patch("asyncio.wait_for", AsyncMock(side_effect=_asyncio.TimeoutError)):
            result = await m.execute_bash(ctx, {"command": "echo hello"})
        assert result["ok"] is False
        assert "timed out" in result["error"]
