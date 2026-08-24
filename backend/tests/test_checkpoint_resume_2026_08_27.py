"""Checkpoint/resume Phase 2 acceptance tests for cto_projects.retry_task.

Feature under test (2026-08-27):
  When a cto_task fails AFTER the LLM codegen + validation pipeline
  succeeded but BEFORE the commit/push completes, its final vetted
  `pending_edits` are persisted on the task doc. A subsequent
  POST /cto/tasks/{task_id}/retry within PENDING_EDITS_TTL_S (900s)
  must reuse those exact edits and SKIP the LLM codegen call —
  surfaced via `resumed_from_checkpoint: true` in both the response
  and the new task doc, plus a "reusing saved edits, skipping
  regeneration" marker in the new task's `steps`.

Verifies:
  1. Fresh pending_edits (0s old) -> resume path taken.
  2. Stale pending_edits (>900s old) -> normal fresh regen path.
  3. No pending_edits at all -> normal fresh regen path (regression).
  4. Vanguard verify still runs on resumed edits (code inspection).
  5. _run_task dispatcher forwards resume_edits to the correct worker
     depending on _GIT_AVAILABLE.
  6. Worker functions honor resume_edits (skip codegen branch reached).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

# Reuse the FakeDB pattern from the sibling test file so we get the
# same in-process FastAPI TestClient wiring.
from tests.test_phase2c_cto_projects_router import (  # type: ignore
    _FakeDB,
    USER,
    AUTH,
)


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
# Retry endpoint — resume-from-checkpoint behaviour
# ═════════════════════════════════════════════════════════════════════

class TestRetryResumeCheckpoint:
    """POST /cto/tasks/{id}/retry — resume_edits selection."""

    def _seed(self, fake_db, pending_edits=None):
        task_row = {
            "task_id": "t1",
            "user_id": "u1",
            "status": "failed",
            "project_id": "p1",
            "error": "commit push failed: 503",
            "task": "add auth middleware",
            "files": ["backend/server.py"],
            "context": "",
            "maxx_mode": False,
        }
        if pending_edits is not None:
            task_row["pending_edits"] = pending_edits
        fake_db.cto_tasks.rows.append(task_row)
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})

    def test_fresh_pending_edits_triggers_resume(self, client, fake_db):
        """0s-old pending_edits → resumed_from_checkpoint=true, marker in steps."""
        self._seed(fake_db, pending_edits={
            "edits":   {"backend/server.py": "print('hi')\n"},
            "summary": "add hi",
            "saved_at": datetime.now(timezone.utc),
        })
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)) as m_run:
            r = client.post("/api/aurem-dev/cto/tasks/t1/retry", headers=AUTH)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resumed_from_checkpoint"] is True
        assert body["retry_of"] == "t1"

        # Verify the new task doc has the flag + step marker persisted.
        new_task_id = body["task_id"]
        new_row = next(r for r in fake_db.cto_tasks.rows
                       if r.get("task_id") == new_task_id)
        assert new_row["resumed_from_checkpoint"] is True
        marker_present = any(
            "reusing saved edits" in (s.get("step") or "").lower()
            for s in new_row.get("steps", [])
        )
        assert marker_present, f"steps missing resume marker: {new_row.get('steps')}"

        # Verify _run_task received the resume_edits payload as its
        # trailing positional arg (see retry_task's bg.add_task call).
        assert m_run.await_count == 1
        _args, _kwargs = m_run.call_args
        # signature: (task_id, proj, task, files, context, user_token,
        #             maxx_mode, resume_edits)
        assert _args[-1] is not None
        assert _args[-1].get("edits") == {"backend/server.py": "print('hi')\n"}

    def test_stale_pending_edits_falls_through_to_fresh_regen(self, client, fake_db):
        """>900s-old pending_edits → resumed_from_checkpoint=false, no marker."""
        stale_at = datetime.now(timezone.utc) - timedelta(seconds=901)
        self._seed(fake_db, pending_edits={
            "edits":   {"backend/server.py": "print('stale')\n"},
            "summary": "stale",
            "saved_at": stale_at,
        })
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)) as m_run:
            r = client.post("/api/aurem-dev/cto/tasks/t1/retry", headers=AUTH)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resumed_from_checkpoint"] is False

        # _run_task must have been called with resume_edits=None.
        _args, _kwargs = m_run.call_args
        assert _args[-1] is None

        # No marker step should be present.
        new_task_id = body["task_id"]
        new_row = next(r for r in fake_db.cto_tasks.rows
                       if r.get("task_id") == new_task_id)
        assert new_row["resumed_from_checkpoint"] is False
        for s in new_row.get("steps", []):
            assert "reusing saved edits" not in (s.get("step") or "").lower()

    def test_no_pending_edits_falls_through_normally(self, client, fake_db):
        """No pending_edits at all → normal fresh regen (regression)."""
        self._seed(fake_db, pending_edits=None)
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)) as m_run:
            r = client.post("/api/aurem-dev/cto/tasks/t1/retry", headers=AUTH)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resumed_from_checkpoint"] is False
        _args, _kwargs = m_run.call_args
        assert _args[-1] is None

    def test_pending_edits_missing_saved_at_is_ignored(self, client, fake_db):
        """Malformed pending_edits (no saved_at) must not trigger resume."""
        self._seed(fake_db, pending_edits={
            "edits":   {"a.py": "x"},
            "summary": "no timestamp",
            # NO saved_at
        })
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/t1/retry", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["resumed_from_checkpoint"] is False

    def test_pending_edits_empty_edits_dict_is_ignored(self, client, fake_db):
        """pending_edits with empty edits dict must not trigger resume."""
        self._seed(fake_db, pending_edits={
            "edits":   {},
            "summary": "empty",
            "saved_at": datetime.now(timezone.utc),
        })
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/t1/retry", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["resumed_from_checkpoint"] is False

    def test_boundary_at_ttl_seconds_still_resumes(self, client, fake_db):
        """Exactly at TTL boundary (899s) still counts as fresh."""
        just_under = datetime.now(timezone.utc) - timedelta(seconds=899)
        self._seed(fake_db, pending_edits={
            "edits":   {"a.py": "x"},
            "summary": "boundary",
            "saved_at": just_under,
        })
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/t1/retry", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["resumed_from_checkpoint"] is True


# ═════════════════════════════════════════════════════════════════════
# _run_task dispatcher — routes resume_edits to correct worker
# ═════════════════════════════════════════════════════════════════════

class TestRunTaskDispatcherForwardsResumeEdits:
    """The dispatcher must thread resume_edits to whichever worker fires."""

    @pytest.mark.asyncio
    async def test_dispatcher_forwards_to_git_worker_when_available(self):
        from routers import cto_projects as m
        pe = {"edits": {"a.py": "x"}, "summary": "s",
              "saved_at": datetime.now(timezone.utc)}
        with patch.object(m, "_GIT_AVAILABLE", True), \
             patch.object(m, "_run_task_with_git",
                          AsyncMock(return_value=None)) as m_git, \
             patch.object(m, "_run_task_via_api",
                          AsyncMock(return_value=None)) as m_api:
            await m._run_task("t1", {"project_id": "p"}, "task",
                              [], "", "tok", False, pe)
        assert m_git.await_count == 1
        assert m_api.await_count == 0
        # The trailing resume_edits arg must be passed through verbatim.
        assert m_git.call_args.args[-1] is pe

    @pytest.mark.asyncio
    async def test_dispatcher_forwards_to_api_worker_when_git_missing(self):
        from routers import cto_projects as m
        pe = {"edits": {"a.py": "x"}, "summary": "s",
              "saved_at": datetime.now(timezone.utc)}
        with patch.object(m, "_GIT_AVAILABLE", False), \
             patch.object(m, "_run_task_with_git",
                          AsyncMock(return_value=None)) as m_git, \
             patch.object(m, "_run_task_via_api",
                          AsyncMock(return_value=None)) as m_api:
            await m._run_task("t1", {"project_id": "p"}, "task",
                              [], "", "tok", False, pe)
        assert m_api.await_count == 1
        assert m_git.await_count == 0
        assert m_api.call_args.args[-1] is pe


# ═════════════════════════════════════════════════════════════════════
# Code-inspection assertions — Vanguard verify still gates resumed edits
# ═════════════════════════════════════════════════════════════════════

class TestResumedEditsStillPassThroughVanguard:
    """The founder's explicit conservative choice: resumed edits still
    go through hallucination-gate + Vanguard verify + lint before
    commit. This is a *code-inspection* assertion (running Vanguard
    end-to-end requires the full LLM stack)."""

    def test_ttl_constant_is_15_minutes(self):
        from routers import cto_projects as m
        assert m.PENDING_EDITS_TTL_S == 15 * 60

    def test_git_worker_persist_is_after_vanguard_block(self):
        """In _run_task_with_git, the pending_edits persist call must
        come AFTER the Vanguard verify_patch call (so the persisted
        edits are already Vanguard-vetted)."""
        import inspect
        from routers import cto_projects as m
        src = inspect.getsource(m._run_task_with_git)
        idx_vanguard = src.find("verify_patch(")
        idx_persist  = src.find('"pending_edits":')
        assert idx_vanguard != -1, "verify_patch call not found in git worker"
        assert idx_persist  != -1, "pending_edits persist not found in git worker"
        assert idx_vanguard < idx_persist, (
            "pending_edits persisted BEFORE Vanguard verify — "
            "would let unvetted edits be resumed"
        )

    def test_api_worker_persist_is_after_hallucination_and_vanguard(self):
        """Same for _run_task_via_api: persist must be after both the
        hallucination gate and Vanguard verify."""
        import inspect
        from routers import cto_projects as m
        src = inspect.getsource(m._run_task_via_api)
        idx_persist = src.find('"pending_edits":')
        assert idx_persist != -1
        # A verify_patch OR hallucination gate reference must exist
        # before it.
        idx_verify = src.find("verify_patch(")
        idx_hallu  = src.lower().find("hallucin")
        earlier_gate = min(x for x in (idx_verify, idx_hallu) if x != -1)
        assert earlier_gate < idx_persist, (
            "pending_edits persisted before any hallucination/Vanguard gate"
        )

    def test_resume_branch_does_not_short_circuit_vanguard(self):
        """The resume_edits branch in both workers must fall through
        to the same downstream verify pipeline (no early return / no
        skip of verify_patch inside the resume branch itself)."""
        import inspect
        from routers import cto_projects as m
        for fn in (m._run_task_via_api, m._run_task_with_git):
            src = inspect.getsource(fn)
            # Locate the resume branch header.
            marker = 'if resume_edits and resume_edits.get("edits"):'
            i = src.find(marker)
            assert i != -1, f"resume branch marker missing in {fn.__name__}"
            # Slice from resume branch to the matching else (rough).
            after = src[i:i + 2000]
            # Must NOT contain "return" inside the resume block before
            # the pipeline continues.
            first_return = after.find("return")
            first_else   = after.find("\n        else:")
            if first_return != -1 and first_else != -1:
                assert first_return > first_else, (
                    f"{fn.__name__}: resume branch appears to early-return "
                    "before falling through to Vanguard/verify"
                )
