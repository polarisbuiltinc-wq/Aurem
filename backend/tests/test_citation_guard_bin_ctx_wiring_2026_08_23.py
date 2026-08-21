"""
2026-08-23 — regression test for the "detailed audit immediately
followed by a false 'files not found' disclaimer" bug.

Root cause: `routers/chat.py::chat_stream` built a separate `_ctx`
dict for CitationGuard's own re-verification fetch that was MISSING
`bin_ctx` — every repo tool requires it via `local_tools._repo_ctx_from`.
Without it, ANY citation the guard flagged (even a single mismatched
one among 10 genuinely-read files) came back as `_NO_BIN_CTX_ERROR`
("No project selected"), which `enforce()` then reported to the user
as "FILE NOT FOUND" — even for files that were, in fact, correctly
read moments earlier in the same turn using the correct main-turn
`bin_ctx`. Fix: reuse the same `bin_ctx` already built for the turn.

Separately: CitationGuard's rewrite call is a single-shot completion
with no tool-execution loop. If the model still emits a `tool_call`
fence there (habit from the main system prompt), it used to leak as
literal text into the chat bubble. Fixed by stripping tool_call
fences from the rewrite output defensively.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.citation_guard import CitationGuard
from services.local_tools import _repo_ctx_from, read_repo_file


class _FakeBinCtx:
    bin_id = "test_user"
    repo_owner = "test-org"
    repo_name = "test-repo"
    branch = "main"
    pat = "fake-token"
    is_founder = False
    pid = "p_test"


def test_repo_ctx_from_none_without_bin_ctx():
    """The exact gate that produced the false 'not found' — missing
    bin_ctx means every repo tool refuses before ever reaching GitHub."""
    assert _repo_ctx_from({"user_id": "test_user", "project_id": "p_test"}) is None


def test_repo_ctx_from_resolves_with_bin_ctx():
    rc = _repo_ctx_from({
        "user_id": "test_user", "project_id": "p_test", "bin_ctx": _FakeBinCtx(),
    })
    assert rc is not None
    assert rc["owner"] == "test-org" and rc["repo"] == "test-repo"


@pytest.mark.asyncio
async def test_read_repo_file_short_circuits_without_bin_ctx():
    """Reproduces the bug exactly: even a file that WAS genuinely read
    this turn gets `no_bin_ctx`, never a real GitHub call, when ctx is
    missing bin_ctx (the pre-fix `_ctx` shape in chat_stream)."""
    r = await read_repo_file(
        {"user_id": "test_user", "project_id": "p_test"},
        {"path": "backend/config.py"},
    )
    assert r["ok"] is False
    assert r.get("error_class") == "no_bin_ctx"


@pytest.mark.asyncio
async def test_read_repo_file_passes_bin_ctx_gate_when_present():
    """Post-fix shape: passes the bin_ctx gate and proceeds to attempt
    the real fetch (network layer is out of scope for this test)."""
    r = await read_repo_file(
        {"user_id": "test_user", "project_id": "p_test", "bin_ctx": _FakeBinCtx()},
        {"path": "backend/config.py"},
    )
    assert r.get("error_class") != "no_bin_ctx"


@pytest.mark.asyncio
async def test_enforce_does_not_blanket_deny_when_bin_ctx_present():
    """9 files genuinely read this turn + 1 inferred-but-unread citation
    (realistic multi-file audit scenario). With bin_ctx present, the
    guard's re-fetch attempt for the unread file at least reaches the
    real fetch path instead of being pre-empted by a self-inflicted
    'no project selected' error for a project that WAS selected."""
    read_files = [f"backend/mod_{i}.py" for i in range(9)]
    tool_calls = [{"tool": "read_repo_file", "args": {"path": p}} for p in read_files]
    response_text = " ".join(f"{p} looks fine." for p in read_files) + \
        " backend/inferred_not_read.py also seems related."

    report = CitationGuard().verify(response_text, tool_calls)
    assert report["unverified_paths"] == ["backend/inferred_not_read.py"]
    assert report["pass"] is False

    async def llm_caller(**kw):
        return "rewritten"

    # OLD shape (bug): ctx missing bin_ctx.
    out_old = await CitationGuard().enforce(
        response_text=response_text, tool_calls=tool_calls,
        ctx={"user_id": "test_user", "project_id": "p_test"},
        llm_caller=llm_caller, original_messages=[],
    )
    assert out_old["fetched"]["backend/inferred_not_read.py"].startswith("FILE NOT FOUND")

    # NEW shape (fixed): ctx includes bin_ctx — same self-inflicted
    # failure can no longer happen; a real attempt is made instead of
    # an automatic refusal.
    from unittest.mock import AsyncMock
    mock_read = AsyncMock(return_value={"ok": True, "content": "print('hi')"})
    out_new = await CitationGuard().enforce(
        response_text=response_text, tool_calls=tool_calls,
        ctx={"user_id": "test_user", "project_id": "p_test", "bin_ctx": _FakeBinCtx()},
        llm_caller=llm_caller, original_messages=[], read_repo_file=mock_read,
    )
    assert "FILE NOT FOUND" not in out_new["fetched"]["backend/inferred_not_read.py"]
    mock_read.assert_awaited_once()


def test_enforce_strips_tool_call_fence_from_rewrite():
    """The tool_call-leaked-as-raw-text bug: CitationGuard's rewrite
    has no tool loop behind it, so a stray tool_call fence from the
    model must be stripped, not shown verbatim in the chat bubble."""
    import asyncio
    from unittest.mock import AsyncMock

    leaky_text = (
        "Here is the rewrite.\n\n```tool_call\n"
        '{"tool": "read_repo_file", "args": {"path": "x.py"}}'
        "\n```\n\nDone."
    )

    async def llm_caller(**kw):
        return leaky_text

    async def run():
        return await CitationGuard().enforce(
            response_text="backend/unread.py has an issue.",
            tool_calls=[],
            ctx={"user_id": "test_user", "project_id": "p_test", "bin_ctx": _FakeBinCtx()},
            llm_caller=llm_caller, original_messages=[],
            read_repo_file=AsyncMock(return_value={"ok": False}),
        )

    out = asyncio.get_event_loop().run_until_complete(run())
    assert "```tool_call" not in out["text"]
    assert "read_repo_file" not in out["text"]
