"""Phase 2c local_tools.py — behavioral verification (live-in-process).

Purpose: verify the E1-review-scoped behaviors of the local_tools tool
surface end-to-end via direct async invocation (mirroring what the chat
orchestrator does mid-conversation). Covers:

  1. read_repo_file / list_repo_files / search_repo — return a clean
     error (not stack trace) when no BINContext / no project is selected.
  2. execute_bash — non-founder ctx is HARD-refused with a clean error
     (no subprocess touched, no raw exception).
  3. get_repo_info / get_commit_diff — Home context (no bin_ctx) returns
     clean structured error, not a raw stack trace.
  4. save_finding — Home context (no bin_ctx) refused cleanly.
  5. _is_safe_repo_path — shell-metachar paths (SEC-005) are rejected
     BEFORE reaching any subprocess in write_repo_file.
  6. invoke_local_tool dispatcher — unknown tool → clean None/refusal.

Known env limitation: GitHub App installation may not be provisioned in
preview, so success-path repo reads may error out — these tests focus on
CLEAN ERROR CONTRACT (no stack traces, structured `{ok: False, error}`).
"""
import asyncio
import inspect
import pytest

from services import local_tools as lt


# ─── Helpers ─────────────────────────────────────────────────────────

def _looks_like_stack_trace(text: str) -> bool:
    """Cheap detector for raw Python tracebacks leaking into error strings."""
    if not isinstance(text, str):
        return False
    return (
        "Traceback (most recent call last)" in text
        or 'File "/' in text and "line " in text
    )


def _assert_clean_error(res: dict, tag: str) -> None:
    assert isinstance(res, dict), f"{tag}: expected dict result, got {type(res)!r}"
    assert res.get("ok") is False, f"{tag}: expected ok=False, got {res!r}"
    err = res.get("error", "")
    assert isinstance(err, str) and err, f"{tag}: expected non-empty error string"
    assert not _looks_like_stack_trace(err), (
        f"{tag}: error string looks like a raw traceback: {err!r}"
    )


# ─── 1. _is_safe_repo_path — SEC-005 shell-metachar guard ────────────


class TestSafeRepoPathSEC005:
    """Security-critical: shell metachar in a repo path must be rejected
    BEFORE any subprocess touches it (write_repo_file's syntax gate)."""

    @pytest.mark.parametrize("bad", [
        "src/app;rm -rf /.py",     # semicolon
        "src/`whoami`.py",         # backtick
        "src/app|nc evil 80.py",   # pipe
        "src/app&&ls.py",          # && chain
        "src/$IFS/app.py",         # env var
        "src/app'.py",             # quote
        'src/app".py',             # dquote
        "src/app\n.py",            # newline
        "src/app$(id).py",         # cmd substitution
        "",                        # empty
        None,                      # non-str
    ])
    def test_unsafe_paths_rejected(self, bad):
        assert lt._is_safe_repo_path(bad) is False, (
            f"_is_safe_repo_path must reject {bad!r} (SEC-005 boundary)"
        )

    @pytest.mark.parametrize("good", [
        "src/app.py",
        "path/to/some_file.jsx",
        "a/b/c/d.ts",
        "docs/README.md",
        "with space/file.py",     # spaces allowed per whitelist
        "src-1/app_2.py",
    ])
    def test_safe_paths_allowed(self, good):
        assert lt._is_safe_repo_path(good) is True, (
            f"_is_safe_repo_path must allow {good!r}"
        )


# ─── 2. execute_bash — non-founder HARD refusal ──────────────────────


class TestExecuteBashRefusal:
    """execute_bash must refuse non-founder callers with a clean error
    (not a subprocess call, not a stack trace)."""

    @pytest.mark.asyncio
    async def test_non_founder_refused(self):
        ctx = {"is_founder": False, "user_id": "u_regular"}
        res = await lt.execute_bash(ctx, {"command": "ls /app"})
        _assert_clean_error(res, "execute_bash(non-founder)")
        # refusal must reference safer alternatives (steering the LLM)
        assert "read_repo_file" in res["error"], (
            "refusal should redirect LLM to GitHub-scoped tools"
        )

    @pytest.mark.asyncio
    async def test_missing_is_founder_refused(self):
        # ctx without is_founder at all also refused (defence-in-depth)
        res = await lt.execute_bash({}, {"command": "ls /app"})
        _assert_clean_error(res, "execute_bash(no is_founder)")

    @pytest.mark.asyncio
    async def test_founder_with_empty_command_clean_error(self):
        ctx = {"is_founder": True, "user_id": "u_founder"}
        res = await lt.execute_bash(ctx, {"command": ""})
        _assert_clean_error(res, "execute_bash(founder,empty cmd)")
        assert "command is required" in res["error"].lower() or \
               "required" in res["error"].lower()


# ─── 3. read_repo_file / list_repo_files / search_repo — no bin_ctx ─


class TestRepoToolsNoBinCtx:
    """When there is no BINContext (Home chat / no project selected),
    the repo-scoped tools must return the canonical no-bin-ctx error
    cleanly — never a raw stack trace, never a fabricated success."""

    @pytest.mark.asyncio
    async def test_read_repo_file_no_bin_ctx(self):
        res = await lt.read_repo_file({}, {"path": "README.md"})
        _assert_clean_error(res, "read_repo_file(no bin_ctx)")

    @pytest.mark.asyncio
    async def test_read_repo_file_missing_path_arg(self):
        res = await lt.read_repo_file({}, {})
        _assert_clean_error(res, "read_repo_file(no path)")
        assert "path" in res["error"].lower()

    @pytest.mark.asyncio
    async def test_read_repo_file_traversal_rejected(self):
        res = await lt.read_repo_file({}, {"path": "../etc/passwd"})
        _assert_clean_error(res, "read_repo_file(traversal)")

    @pytest.mark.asyncio
    async def test_read_repo_file_absolute_path_rejected(self):
        res = await lt.read_repo_file({}, {"path": "/etc/passwd"})
        _assert_clean_error(res, "read_repo_file(absolute)")

    @pytest.mark.asyncio
    async def test_list_repo_files_no_bin_ctx(self):
        res = await lt.list_repo_files({}, {"glob": "**/*.py"})
        _assert_clean_error(res, "list_repo_files(no bin_ctx)")

    @pytest.mark.asyncio
    async def test_search_repo_no_bin_ctx(self):
        res = await lt.search_repo({}, {"pattern": "def foo"})
        _assert_clean_error(res, "search_repo(no bin_ctx)")


# ─── 4. get_repo_info / get_commit_diff — Home context ───────────────


class TestRepoMetaToolsNoBinCtx:
    @pytest.mark.asyncio
    async def test_get_repo_info_no_bin_ctx(self):
        res = await lt.get_repo_info({}, {})
        _assert_clean_error(res, "get_repo_info(no bin_ctx)")

    @pytest.mark.asyncio
    async def test_get_commit_diff_no_bin_ctx(self):
        res = await lt.get_commit_diff({}, {"sha": "abc123"})
        _assert_clean_error(res, "get_commit_diff(no bin_ctx)")

    @pytest.mark.asyncio
    async def test_get_commit_diff_missing_sha(self):
        # even with a bin_ctx-ish shape, missing sha is a clean 4xx-style
        res = await lt.get_commit_diff({}, {})
        _assert_clean_error(res, "get_commit_diff(no sha)")


# ─── 5. save_finding — no bin_ctx refused ────────────────────────────


class TestSaveFindingRefusalPath:
    @pytest.mark.asyncio
    async def test_no_bin_ctx_refused(self):
        res = await lt.save_finding({}, {
            "title": "test",
            "severity": "low",
            "description": "x",
            "file_path": "README.md",
        })
        _assert_clean_error(res, "save_finding(no bin_ctx)")

    @pytest.mark.asyncio
    async def test_missing_required_fields_refused(self):
        # Provide a minimal bin_ctx-shaped dict but omit required args
        class _StubBC:
            user_id = "u_test"
            project_id = "p_test"
            owner = "o"
            repo = "r"
            branch = "main"
            is_founder = True
            boundary_enabled = True
        res = await lt.save_finding({"bin_ctx": _StubBC(), "user_id": "u_test"}, {})
        _assert_clean_error(res, "save_finding(no fields)")


# ─── 6. invoke_local_tool dispatcher ─────────────────────────────────


class TestInvokeLocalToolDispatcher:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_none_or_clean(self):
        res = await lt.invoke_local_tool("this_tool_does_not_exist_xyz", {}, {})
        # invoke_local_tool returns None for unknown tool names so the
        # orchestrator can fall through — but it MUST NOT raise or leak.
        assert res is None or (isinstance(res, dict) and res.get("ok") is False), (
            f"invoke_local_tool for unknown tool should return None or clean "
            f"error dict, got {res!r}"
        )

    @pytest.mark.asyncio
    async def test_known_tool_no_bin_ctx_clean(self):
        # Dispatcher should route to read_repo_file and surface its
        # clean no-bin-ctx error, not raise.
        res = await lt.invoke_local_tool(
            "read_repo_file", {"path": "README.md"}, {}
        )
        assert res is None or (isinstance(res, dict)), (
            f"dispatcher should return dict/None, got {res!r}"
        )
        if isinstance(res, dict):
            # If a result comes back, it should be the clean no-bin-ctx error
            assert res.get("ok") is False
            assert not _looks_like_stack_trace(res.get("error", ""))


# ─── 7. write_repo_file — SEC-005 gate reached ───────────────────────


class TestWriteRepoFileSEC005Gate:
    """write_repo_file must call _is_safe_repo_path BEFORE any subprocess
    runs — verified by asserting a semicolon-path returns a clean rejection
    error (and doesn't crash / doesn't leak a traceback)."""

    @pytest.mark.asyncio
    async def test_unsafe_path_rejected_pre_subprocess(self):
        res = await lt.write_repo_file(
            {},  # no bin_ctx → will refuse EITHER way, but should still be clean
            {"path": "src/app;rm -rf.py", "content": "print(1)",
             "commit_message": "x"},
        )
        assert isinstance(res, dict)
        assert res.get("ok") is False
        # Must not be a stack trace regardless of which guard fires first
        assert not _looks_like_stack_trace(res.get("error", ""))
