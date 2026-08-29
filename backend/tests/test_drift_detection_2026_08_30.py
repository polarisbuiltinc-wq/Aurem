"""
tests/test_drift_detection_2026_08_30.py — R1a gap#4 (2026-08-30):
ship-branch drift detection, the last R9 rollback-safety gap from T2.

A rollback (revert-commit OR unmerged-PR close+delete) previously
trusted a SHA captured at ship time forever. If the branch moved since
(someone else pushed, or the ship's own branch got a follow-up commit),
that trust was blind. This round: record the branch head immediately
after ship (`expected_branch_head_sha`), re-check it live at rollback
time, and BLOCK (not silently proceed) until the user explicitly
acknowledges the drift.

Named tests (all 4 founder-required IDs present):
  t_drift_detected_blocks_rollback
  t_drift_acknowledge_proceeds
  t_drift_unmerged_branch
  t_no_drift_normal_rollback
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks


def _direct_commit_session_doc(expected_head="d1234567890full"):
    return {
        "loop_id": "loop_drift_direct", "user_id": "u1", "project_id": "proj_1",
        "state": "completed",
        "context": {"commit": {
            "sha": "d1234", "full_sha": expected_head,
            "expected_branch_head_sha": expected_head,
        }},
    }


def _pr_session_doc(expected_head="stale1234567890prehead"):
    return {
        "loop_id": "loop_drift_pr", "user_id": "u1", "project_id": "proj_1",
        "state": "completed",
        "context": {"commit": {
            "sha": "stale12", "full_sha": expected_head,
            "expected_branch_head_sha": expected_head,
            "pr_url": "https://github.com/o/r/pull/9", "pr_number": 9,
            "pr_branch": "auremcto/ship-x-1",
        }},
    }


def _mock_db_for_router(session_doc):
    db = MagicMock()
    db.loop_sessions.find_one = AsyncMock(return_value=session_doc)
    db.loop_sessions.update_one = AsyncMock()
    db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "proj_1", "user_id": "u1",
        "github_owner": "o", "github_repo": "r", "branch": "main",
    })
    return db


# ── t_drift_detected_blocks_rollback ───────────────────────────────
@pytest.mark.asyncio
async def test_t_drift_detected_blocks_rollback():
    """Direct-commit path: branch head moved since ship (someone else
    pushed) -> rollback is BLOCKED with rollback_status='drift_detected',
    no revert queued, until acknowledged."""
    from routers.loop import rollback_loop, LoopRollbackBody
    from services import github_api_writer as gw

    expected = "d1234567890full"
    doc = _direct_commit_session_doc(expected_head=expected)
    db = _mock_db_for_router(doc)
    bg = BackgroundTasks()

    async def _fake_drift(owner, repo, branch, expected_sha, token):
        return {"drifted": True, "current_sha": "someoneelsepushedthisSHA", "expected_sha": expected_sha}

    with patch("routers.loop.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.loop.get_db", return_value=db), \
         patch("services.pat_vault.get_repo_token_or_error", new=AsyncMock(return_value=("tok", None, None))), \
         patch.object(gw, "check_branch_drift", new=_fake_drift), \
         patch("services.loop_rollback.run_rollback_bg") as mock_bg_task:
        res = await rollback_loop(
            loop_id="loop_drift_direct",
            body=LoopRollbackBody(confirm="ROLLBACK"),
            bg=bg, authorization="Bearer x",
        )

    assert res["ok"] is False
    assert res["rollback_status"] == "drift_detected"
    assert "Branch has changed" in res["detail"]
    assert res["drift"]["current"] == "someoneelsepushedthisSHA"
    assert len(bg.tasks) == 0  # no revert queued
    drift_sets = [c.args[1]["$set"] for c in db.loop_sessions.update_one.call_args_list
                  if c.args[1].get("$set", {}).get("rollback_status") == "drift_detected"]
    assert len(drift_sets) == 1
    assert drift_sets[0]["rollback_drift"]["expected"] == expected


# ── t_drift_acknowledge_proceeds ───────────────────────────────────
@pytest.mark.asyncio
async def test_t_drift_acknowledge_proceeds():
    """Same drifted branch, but the user resends with
    acknowledge_drift=true -> rollback proceeds, targeting the
    EXPECTED commit (the one the fix made), not the drifted head."""
    from routers.loop import rollback_loop, LoopRollbackBody
    from services import github_api_writer as gw

    expected = "d1234567890full"
    doc = _direct_commit_session_doc(expected_head=expected)
    db = _mock_db_for_router(doc)
    bg = BackgroundTasks()

    with patch("routers.loop.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.loop.get_db", return_value=db), \
         patch("services.pat_vault.get_repo_token_or_error", new=AsyncMock(return_value=("tok", None, None))), \
         patch.object(gw, "check_branch_drift", new=AsyncMock(
             side_effect=AssertionError("must not even check drift once acknowledged"))), \
         patch("services.loop_rollback.run_rollback_bg") as mock_bg_task:
        res = await rollback_loop(
            loop_id="loop_drift_direct",
            body=LoopRollbackBody(confirm="ROLLBACK", acknowledge_drift=True),
            bg=bg, authorization="Bearer x",
        )

    assert res["ok"] is True
    assert res["rollback_status"] == "queued"
    assert res["commit_sha"] == expected  # targets the EXPECTED commit, not a drifted head
    assert len(bg.tasks) == 1


# ── t_drift_unmerged_branch ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_t_drift_unmerged_branch():
    """Unmerged-PR case: someone pushed a NEW commit to the auremcto/
    ship branch itself. Auto-close+delete must be BLOCKED (would
    otherwise destroy that push) until acknowledged."""
    from routers.loop import rollback_loop, LoopRollbackBody
    from services import github_api_writer as gw

    expected = "stale1234567890prehead"
    doc = _pr_session_doc(expected_head=expected)
    db = _mock_db_for_router(doc)
    bg = BackgroundTasks()

    async def _fake_drift(owner, repo, branch, expected_sha, token):
        assert branch == "auremcto/ship-x-1"
        return {"drifted": True, "current_sha": "userPushedANewCommitHere", "expected_sha": expected_sha}

    with patch("routers.loop.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.loop.get_db", return_value=db), \
         patch("services.pat_vault.get_repo_token_or_error", new=AsyncMock(return_value=("tok", None, None))), \
         patch("services.loop_safety.get_pr_status", new=AsyncMock(return_value={
             "ok": True, "merged": False, "state": "open", "merge_commit_sha": None,
         })), \
         patch.object(gw, "check_branch_drift", new=_fake_drift), \
         patch("services.loop_safety.close_and_retract", new=AsyncMock()) as mock_close:
        res = await rollback_loop(
            loop_id="loop_drift_pr",
            body=LoopRollbackBody(confirm="ROLLBACK"),
            bg=bg, authorization="Bearer x",
        )

    mock_close.assert_not_awaited()
    assert res["ok"] is False
    assert res["rollback_status"] == "drift_detected"
    assert res["drift"]["branch"] == "auremcto/ship-x-1"


# ── t_no_drift_normal_rollback ──────────────────────────────────────
@pytest.mark.asyncio
async def test_t_no_drift_normal_rollback():
    """expected == current -> normal rollback proceeds, no warning,
    no behavior change from before this feature existed."""
    from routers.loop import rollback_loop, LoopRollbackBody
    from services import github_api_writer as gw

    expected = "d1234567890full"
    doc = _direct_commit_session_doc(expected_head=expected)
    db = _mock_db_for_router(doc)
    bg = BackgroundTasks()

    async def _fake_no_drift(owner, repo, branch, expected_sha, token):
        return {"drifted": False, "current_sha": expected_sha, "expected_sha": expected_sha}

    with patch("routers.loop.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.loop.get_db", return_value=db), \
         patch("services.pat_vault.get_repo_token_or_error", new=AsyncMock(return_value=("tok", None, None))), \
         patch.object(gw, "check_branch_drift", new=_fake_no_drift), \
         patch("services.loop_rollback.run_rollback_bg") as mock_bg_task:
        res = await rollback_loop(
            loop_id="loop_drift_direct",
            body=LoopRollbackBody(confirm="ROLLBACK"),
            bg=bg, authorization="Bearer x",
        )

    assert res["ok"] is True
    assert res["rollback_status"] == "queued"
    assert res["commit_sha"] == expected
    assert len(bg.tasks) == 1


@pytest.mark.asyncio
async def test_drift_skipped_when_no_expected_sha_recorded():
    """Sessions shipped BEFORE this feature existed have no
    `expected_branch_head_sha` — drift simply can't be checked (nothing
    to compare), so the check is skipped entirely, not blocked. Matches
    the pre-existing `test_t_revert_reverse_path_alive` behavior."""
    from routers.loop import rollback_loop, LoopRollbackBody

    doc = {
        "loop_id": "loop_old_1", "user_id": "u1", "project_id": "proj_1",
        "state": "completed",
        "context": {"commit": {"sha": "d1234", "full_sha": "d1234567890full"}},
    }
    db = _mock_db_for_router(doc)
    bg = BackgroundTasks()

    with patch("routers.loop.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.loop.get_db", return_value=db), \
         patch("services.pat_vault.get_repo_token_or_error", new=AsyncMock(return_value=("tok", None, None))), \
         patch("services.loop_safety.get_pr_status", new=AsyncMock()) as mock_status, \
         patch("services.loop_rollback.run_rollback_bg") as mock_bg_task:
        res = await rollback_loop(
            loop_id="loop_old_1",
            body=LoopRollbackBody(confirm="ROLLBACK"),
            bg=bg, authorization="Bearer x",
        )

    mock_status.assert_not_awaited()
    assert res["rollback_status"] == "queued"
    assert len(bg.tasks) == 1


def test_ship_rollback_drift_detected_is_registered_event_kind():
    from services.trust_surface_events import EVENT_KINDS
    assert "ship_rollback_drift_detected" in EVENT_KINDS
