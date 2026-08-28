"""
tests/test_rollback_pr_gap_fix.py — Rollback-gap fix (2026-08-28).

`routers/loop.py::rollback_loop` previously always ran a git-revert on
the base branch, even for a `ship_via_pr` commit that landed on a
throwaway branch and never merged — nothing to revert there. Now it
checks live PR merge-state first and closes+deletes the branch
instead when unmerged; falls through to the unchanged revert-commit
path when merged (or when there was never a PR at all).

Named tests:
  t_get_pr_status_merged_true         — live 200 + merged:true → {"merged": True}
  t_get_pr_status_merged_false        — live 200 + merged:false → {"merged": False}
  t_get_pr_status_error_fails_closed  — non-200/exception → {"merged": False} (fail toward the safer path)
  t_rollback_unmerged_pr_closes_and_retracts — pr_url set, not merged → close_and_retract called, run_rollback_bg NOT called
  t_rollback_merged_pr_falls_through  — pr_url set, merged → run_rollback_bg called (existing path), close_and_retract NOT called
  t_rollback_direct_ship_unchanged    — no pr_url at all → existing path unchanged, no PR lookup performed
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from services import loop_safety


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_get_pr_status_merged_true():
    with patch("services.loop_safety.github_request_with_retry",
               new=AsyncMock(return_value=_FakeResp(200, {"merged": True, "state": "closed"}))):
        res = await loop_safety.get_pr_status(owner="o", repo="r", pr_number=1, token="t")
    assert res == {"merged": True, "state": "closed"}


@pytest.mark.asyncio
async def test_get_pr_status_merged_false():
    with patch("services.loop_safety.github_request_with_retry",
               new=AsyncMock(return_value=_FakeResp(200, {"merged": False, "state": "open"}))):
        res = await loop_safety.get_pr_status(owner="o", repo="r", pr_number=1, token="t")
    assert res == {"merged": False, "state": "open"}


@pytest.mark.asyncio
async def test_get_pr_status_error_fails_closed():
    with patch("services.loop_safety.github_request_with_retry",
               new=AsyncMock(side_effect=RuntimeError("network blip"))):
        res = await loop_safety.get_pr_status(owner="o", repo="r", pr_number=1, token="t")
    assert res["merged"] is False


def _session_doc(*, pr_url=None, pr_number=None, pr_branch=None):
    return {
        "loop_id": "loop_pr_gap_1",
        "user_id": "u1",
        "project_id": "proj_1",
        "state": "completed",
        "context": {"commit": {
            "sha": "abc1234", "full_sha": "abc1234full",
            "html_url": "https://github.com/o/r/commit/abc1234full",
            "pr_url": pr_url, "pr_number": pr_number, "pr_branch": pr_branch,
        }},
    }


def _mock_db(session_doc):
    db = MagicMock()
    db.loop_sessions.find_one = AsyncMock(return_value=session_doc)
    db.loop_sessions.update_one = AsyncMock()
    db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "proj_1", "user_id": "u1",
        "github_owner": "o", "github_repo": "r", "branch": "main",
    })
    return db


@pytest.mark.asyncio
async def test_rollback_unmerged_pr_closes_and_retracts():
    from routers.loop import rollback_loop, LoopRollbackBody
    db = _mock_db(_session_doc(pr_url="https://github.com/o/r/pull/9", pr_number=9,
                                pr_branch="auremcto/ship-x-1"))
    with patch("routers.loop.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.loop.get_db", return_value=db), \
         patch("services.pat_vault.get_repo_token_or_error", new=AsyncMock(return_value=("tok", None, None))), \
         patch("services.loop_safety.get_pr_status", new=AsyncMock(return_value={"merged": False, "state": "open"})), \
         patch("services.loop_safety.close_and_retract", new=AsyncMock(return_value={
             "pr_closed": True, "branch_deleted": True, "errors": []})) as mock_close, \
         patch("services.loop_rollback.run_rollback_bg") as mock_run_rollback:
        res = await rollback_loop(
            loop_id="loop_pr_gap_1",
            body=LoopRollbackBody(confirm="ROLLBACK"),
            bg=BackgroundTasks(),
            authorization="Bearer x",
        )
    mock_close.assert_awaited_once()
    mock_run_rollback.assert_not_called()
    assert res["ok"] is True
    assert res["rollback_status"] == "done"
    assert "never merged" in res["detail"]


@pytest.mark.asyncio
async def test_rollback_merged_pr_falls_through():
    from routers.loop import rollback_loop, LoopRollbackBody
    db = _mock_db(_session_doc(pr_url="https://github.com/o/r/pull/9", pr_number=9,
                                pr_branch="auremcto/ship-x-1"))
    bg = BackgroundTasks()
    with patch("routers.loop.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.loop.get_db", return_value=db), \
         patch("services.pat_vault.get_repo_token_or_error", new=AsyncMock(return_value=("tok", None, None))), \
         patch("services.loop_safety.get_pr_status", new=AsyncMock(return_value={"merged": True, "state": "closed"})), \
         patch("services.loop_safety.close_and_retract", new=AsyncMock()) as mock_close:
        res = await rollback_loop(
            loop_id="loop_pr_gap_1",
            body=LoopRollbackBody(confirm="ROLLBACK"),
            bg=bg,
            authorization="Bearer x",
        )
    mock_close.assert_not_awaited()
    # bg.add_task registered run_rollback_bg — it only actually runs when
    # FastAPI executes the response's background tasks, not on direct call.
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func.__name__ == "run_rollback_bg" or "run_rollback" in repr(bg.tasks[0].func)
    assert res["rollback_status"] == "queued"


@pytest.mark.asyncio
async def test_rollback_direct_ship_unchanged():
    from routers.loop import rollback_loop, LoopRollbackBody
    db = _mock_db(_session_doc())  # no pr_url at all
    bg = BackgroundTasks()
    with patch("routers.loop.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.loop.get_db", return_value=db), \
         patch("services.pat_vault.get_repo_token_or_error", new=AsyncMock(return_value=("tok", None, None))), \
         patch("services.loop_safety.get_pr_status", new=AsyncMock()) as mock_status:
        res = await rollback_loop(
            loop_id="loop_pr_gap_1",
            body=LoopRollbackBody(confirm="ROLLBACK"),
            bg=bg,
            authorization="Bearer x",
        )
    mock_status.assert_not_awaited()
    assert len(bg.tasks) == 1
    assert res["rollback_status"] == "queued"
