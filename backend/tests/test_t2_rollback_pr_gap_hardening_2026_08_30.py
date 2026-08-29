"""
tests/test_t2_rollback_pr_gap_hardening_2026_08_30.py — T2/R10 fix
(2026-08-30, founder GO after H3 → B1-extend → W0-residue).

Closes the gaps documented in `memory/R10-ROLLBACK-PR-GAP.md`:
  1. `merge_commit_sha` (the REAL landed commit) is now persisted via
     the merge webhook AND re-fetched live at rollback time, instead
     of reverting the stale pre-merge throwaway-branch sha.
  2. A revert push is now bounded-poll-verified (<=10 attempts/~60s)
     to actually land at branch HEAD before being reported "done".
  3. A verify failure reports `rollback_failed` with an explicit
     reason + a `ship_rollback_failed` trust event — never a false
     "done".
  4. The always-on direct-commit (non-PR) revert path is unchanged.

Named tests (all 5 founder-required IDs present):
  t_sha_updates_to_merge_commit_sha
  t_rollback_verifies_then_done
  t_rollback_blip_reports_failed
  t_squash_rollback_reverts_real_diff
  t_revert_reverse_path_alive
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from services import loop_safety, loop_rollback


# ── t_sha_updates_to_merge_commit_sha ──────────────────────────────
@pytest.mark.asyncio
async def test_t_sha_updates_to_merge_commit_sha():
    db = MagicMock()
    db.loop_outcomes.update_one = AsyncMock()
    db.loop_sessions.update_one = AsyncMock()
    db.ship_pr_events.insert_one = AsyncMock()

    payload = {
        "pull_request": {
            "number": 42,
            "merged": True,
            "merge_commit_sha": "realmergedsha0123456789",
            "html_url": "https://github.com/o/r/pull/42",
            "head": {"ref": "auremcto/ship-x-1"},
            "labels": [{"name": "aura:ship"}],
        },
    }
    result = await loop_safety.dispatch_pull_request_webhook(db, payload=payload, action="closed")

    assert result["routed"] == "ship"
    assert result["status"] == "merged"
    # The self-heal write must target the session by pr_branch and set
    # the REAL merge_commit_sha onto context.commit.sha/full_sha.
    heal_calls = [
        c for c in db.loop_sessions.update_one.call_args_list
        if c.args[0] == {"context.commit.pr_branch": "auremcto/ship-x-1"}
    ]
    assert len(heal_calls) == 1
    set_doc = heal_calls[0].args[1]["$set"]
    assert set_doc["context.commit.full_sha"] == "realmergedsha0123456789"
    assert set_doc["context.commit.merge_commit_sha"] == "realmergedsha0123456789"


@pytest.mark.asyncio
async def test_t_sha_no_heal_when_not_merged():
    """A closed-unmerged PR must NOT touch context.commit at all."""
    db = MagicMock()
    db.loop_outcomes.update_one = AsyncMock()
    db.loop_sessions.update_one = AsyncMock()
    db.ship_pr_events.insert_one = AsyncMock()
    payload = {
        "pull_request": {
            "number": 42, "merged": False, "merge_commit_sha": None,
            "html_url": "https://github.com/o/r/pull/42",
            "head": {"ref": "auremcto/ship-x-1"},
            "labels": [{"name": "aura:ship"}],
        },
    }
    await loop_safety.dispatch_pull_request_webhook(db, payload=payload, action="closed")
    assert db.loop_sessions.update_one.await_count == 0


# ── t_rollback_verifies_then_done ──────────────────────────────────
@pytest.mark.asyncio
async def test_t_rollback_verifies_then_done():
    from services import github_api_writer as gw

    calls = {"n": 0}

    async def _fake_revert(**kw):
        return {"sha": "abc1234", "full_sha": "abc1234deadbeef",
                "html_url": "https://github.com/o/r/commit/abc1234deadbeef"}

    async def _fake_verify(owner, repo, branch, expected_sha, token, **kw):
        calls["n"] += 1
        return {"verified": True, "attempts": 2, "last_sha": expected_sha}

    db = MagicMock()
    set_calls = []

    async def _upd(filt, ops):
        if "$set" in ops:
            set_calls.append(ops["$set"])
    db.loop_sessions.update_one = AsyncMock(side_effect=_upd)
    db.loop_run_log.insert_one = AsyncMock()
    db.rollback_attempts.insert_one = AsyncMock()
    db.rollback_attempts.update_one = AsyncMock()

    with patch.object(gw, "revert_commit", _fake_revert), \
         patch.object(gw, "verify_branch_head", _fake_verify):
        await loop_rollback.run_rollback(
            db=db, loop_id="loop_t2_ok",
            project={"github_owner": "o", "github_repo": "r", "branch": "main", "user_id": "u1"},
            commit_sha="deadbeef1234567", user_token="ghp_x",
            author_name="Test", author_email="dev@example.com",
        )

    assert calls["n"] == 1
    status_sets = [s for s in set_calls if "rollback_status" in s]
    done = status_sets[-1]
    assert done["rollback_status"] == "done"
    assert done["rollback_sha"] == "abc1234"
    assert done["rollback_verified"] is True


# ── t_rollback_blip_reports_failed ─────────────────────────────────
@pytest.mark.asyncio
async def test_t_rollback_blip_reports_failed():
    from services import github_api_writer as gw

    async def _fake_revert(**kw):
        return {"sha": "abc1234", "full_sha": "abc1234deadbeef",
                "html_url": "https://github.com/o/r/commit/abc1234deadbeef"}

    async def _fake_verify_timeout(owner, repo, branch, expected_sha, token, **kw):
        return {"verified": False, "attempts": 10, "last_sha": "stalesha0000"}

    db = MagicMock()
    set_calls = []

    async def _upd(filt, ops):
        if "$set" in ops:
            set_calls.append(ops["$set"])
    db.loop_sessions.update_one = AsyncMock(side_effect=_upd)
    db.loop_run_log.insert_one = AsyncMock()
    db.rollback_attempts.insert_one = AsyncMock()
    db.rollback_attempts.update_one = AsyncMock()
    db.trust_surface_events.insert_one = AsyncMock()

    with patch.object(gw, "revert_commit", _fake_revert), \
         patch.object(gw, "verify_branch_head", _fake_verify_timeout):
        await loop_rollback.run_rollback(
            db=db, loop_id="loop_t2_blip",
            project={"github_owner": "o", "github_repo": "r", "branch": "main", "user_id": "u1"},
            commit_sha="deadbeef1234567", user_token="ghp_x",
            author_name="Test", author_email="dev@example.com",
        )

    status_sets = [s for s in set_calls if "rollback_status" in s]
    failed = status_sets[-1]
    # Never falsely reports done.
    assert failed["rollback_status"] == "failed"
    assert failed["rollback_verified"] is False
    assert failed["rollback_candidate_sha"] == "abc1234deadbeef"
    assert "could not be confirmed" in failed["rollback_error"]
    # The durable ship_rollback_failed trust event was logged.
    assert db.trust_surface_events.insert_one.await_count == 1
    logged = db.trust_surface_events.insert_one.await_args.args[0]
    assert logged["kind"] == "ship_rollback_failed"
    assert logged["reason"] == "verify_timeout"


# ── t_squash_rollback_reverts_real_diff ────────────────────────────
def _pr_session_doc():
    return {
        "loop_id": "loop_squash_1", "user_id": "u1", "project_id": "proj_1",
        "state": "completed",
        "context": {"commit": {
            "sha": "stale12", "full_sha": "stale1234567890prehead",
            "html_url": "https://github.com/o/r/commit/stale1234567890prehead",
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


@pytest.mark.asyncio
async def test_t_squash_rollback_reverts_real_diff():
    """A squash-merged PR's real merge_commit_sha (fetched live) must
    be what gets queued for revert — NOT the stale pre-merge full_sha
    captured at ship time from the throwaway branch."""
    from routers.loop import rollback_loop, LoopRollbackBody

    db = _mock_db_for_router(_pr_session_doc())
    bg = BackgroundTasks()
    with patch("routers.loop.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.loop.get_db", return_value=db), \
         patch("services.pat_vault.get_repo_token_or_error", new=AsyncMock(return_value=("tok", None, None))), \
         patch("services.loop_safety.get_pr_status", new=AsyncMock(return_value={
             "ok": True, "merged": True, "state": "closed",
             "merge_commit_sha": "realsquashcommitSHA999",
         })), \
         patch("services.loop_rollback.run_rollback_bg") as mock_bg_task:
        res = await rollback_loop(
            loop_id="loop_squash_1",
            body=LoopRollbackBody(confirm="ROLLBACK"),
            bg=bg,
            authorization="Bearer x",
        )

    assert res["rollback_status"] == "queued"
    assert res["commit_sha"] == "realsquashcommitSHA999"
    assert res["commit_sha"] != "stale1234567890prehead"
    # queued rollback_commit_sha persisted must also be the real sha.
    queued_sets = [
        c.args[1]["$set"] for c in db.loop_sessions.update_one.call_args_list
        if "rollback_commit_sha" in c.args[1].get("$set", {})
    ]
    assert queued_sets[-1]["rollback_commit_sha"] == "realsquashcommitSHA999"


@pytest.mark.asyncio
async def test_t_rollback_pr_status_unconfirmed_reports_failed_not_silent():
    """A network blip on the live PR lookup must be reported honestly
    as a failed/retryable rollback — never silently treated as
    'confirmed unmerged' (which would risk closing an already-merged
    PR's branch) nor as a false success."""
    from routers.loop import rollback_loop, LoopRollbackBody

    db = _mock_db_for_router(_pr_session_doc())
    bg = BackgroundTasks()
    with patch("routers.loop.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.loop.get_db", return_value=db), \
         patch("services.pat_vault.get_repo_token_or_error", new=AsyncMock(return_value=("tok", None, None))), \
         patch("services.loop_safety.get_pr_status", new=AsyncMock(return_value={
             "ok": False, "merged": False, "state": "unknown", "merge_commit_sha": None,
         })), \
         patch("services.loop_safety.close_and_retract", new=AsyncMock()) as mock_close:
        res = await rollback_loop(
            loop_id="loop_squash_1",
            body=LoopRollbackBody(confirm="ROLLBACK"),
            bg=bg,
            authorization="Bearer x",
        )

    mock_close.assert_not_awaited()
    assert res["ok"] is False
    assert res["rollback_status"] == "failed"
    assert "Could not verify" in res["detail"]


# ── t_revert_reverse_path_alive ────────────────────────────────────
@pytest.mark.asyncio
async def test_t_revert_reverse_path_alive():
    """The always-on direct-commit (non-PR) ship path must be totally
    unaffected by this round's PR-merge hardening: no PR lookup at
    all, straight to the queued revert path."""
    from routers.loop import rollback_loop, LoopRollbackBody

    doc = {
        "loop_id": "loop_direct_1", "user_id": "u1", "project_id": "proj_1",
        "state": "completed",
        "context": {"commit": {"sha": "d1234", "full_sha": "d1234567890full"}},
    }
    db = _mock_db_for_router(doc)
    bg = BackgroundTasks()
    with patch("routers.loop.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.loop.get_db", return_value=db), \
         patch("services.pat_vault.get_repo_token_or_error", new=AsyncMock(return_value=("tok", None, None))), \
         patch("services.loop_safety.get_pr_status", new=AsyncMock()) as mock_status:
        res = await rollback_loop(
            loop_id="loop_direct_1",
            body=LoopRollbackBody(confirm="ROLLBACK"),
            bg=bg,
            authorization="Bearer x",
        )

    mock_status.assert_not_awaited()
    assert len(bg.tasks) == 1
    assert res["rollback_status"] == "queued"
    assert res["commit_sha"] == "d1234567890full"
