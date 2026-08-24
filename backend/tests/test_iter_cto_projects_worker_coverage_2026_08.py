"""Coverage-floor wave (2026-08-26) — backend/routers/cto_projects.py.

`_run_task_via_api` (CC=166, ~1180 lines) and `_run_task_with_git`
(CC=51, ~450 lines) were previously left untested end-to-end (only
source-lock/shape asserts existed — see test_iter_vanguard_autofix_
and_admin_chat.py, test_iter52_production_bug_fixes.py) because they
are the real git-worker pipelines. Both functions wrap almost their
ENTIRE body in a single outer try/except, so triggering a failure at
a known point (missing PAT, a failed `git clone`, or a codegen crash)
exercises a large, real, honest slice of the function — setup, the
early read/context-injection steps, and the shared failure-handling
path (error classification, failure-signature dedup, `_set_status`,
Sentry capture, PAT scrubbing) — without needing to fake an entire
successful commit/push flow.
"""
from __future__ import annotations

import asyncio
import subprocess
import time
import types

import pytest
from unittest.mock import AsyncMock, patch

from routers import cto_projects as router_mod
from cto_services import db as _dbmod


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

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
        for r in self.rows:
            if self._match(r, query):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            new_row = dict(query or {})
            new_row.update((update.get("$set") or {}))
            self.rows.append(new_row)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    async def insert_one(self, doc):
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


PROJ = {
    "project_id": "p1", "user_id": "u1",
    "github_owner": "acme", "github_repo": "widgets", "branch": "main",
}


@pytest.fixture
def fake_db():
    db = _FakeDB()
    return db


@pytest.fixture(autouse=True)
def _set_fake_db(fake_db):
    _dbmod.set_db(fake_db)
    yield
    _dbmod.set_db(None)


class TestRunTaskViaApiNoPat:
    """No PAT on the project — the very first guard clause."""

    @pytest.mark.asyncio
    async def test_no_user_token_fails_fast_no_codegen_attempted(self, fake_db):
        await fake_db.cto_tasks.insert_one({"task_id": "task1", "status": "queued"})
        with patch.object(router_mod, "call_llm", AsyncMock(
            side_effect=AssertionError("codegen must not run without a PAT"),
        )):
            await router_mod._run_task_via_api(
                "task1", PROJ, "fix the bug", [], "", None,
            )
        task_row = await fake_db.cto_tasks.find_one({"task_id": "task1"})
        assert task_row is not None
        assert task_row["status"] == "failed"
        assert "PAT" in task_row["error"]


class TestRunTaskViaApiCodegenFailure:
    """A real (mocked) LLM outage during codegen — exercises file-read,
    brain/issues context injection, and the shared failure handler."""

    @pytest.mark.asyncio
    async def test_call_llm_crash_is_caught_and_recorded_as_failed(self, fake_db):
        await fake_db.cto_tasks.insert_one({"task_id": "task2", "status": "queued"})
        await fake_db.dev_users.insert_one({"user_id": "u1", "tier": "pro"})
        with patch.object(router_mod, "gh_api_fetch_file",
                         AsyncMock(return_value="print('hello')")), \
             patch.object(router_mod, "call_llm",
                         AsyncMock(side_effect=RuntimeError("llm-down"))), \
             patch("services.subscription_tiers.can_use_feature",
                  return_value=False), \
             patch("asyncio.sleep", AsyncMock(return_value=None)):
            await router_mod._run_task_via_api(
                "task2", PROJ, "fix main.py please", ["main.py"], "",
                "ghp_faketoken123",
            )
        task_row = await fake_db.cto_tasks.find_one({"task_id": "task2"})
        assert task_row is not None
        assert task_row["status"] == "failed"
        # PAT must never leak into the persisted error string.
        assert "ghp_faketoken123" not in task_row["error"]
        assert task_row.get("failure_signature")


class TestRunTaskWithGitCloneFailure:
    """`git clone` itself fails — exercises the git-path setup + the
    parallel (but distinct) failure handler for the subprocess path."""

    @pytest.mark.asyncio
    async def test_git_clone_failure_is_caught_and_recorded_as_failed(self, fake_db):
        await fake_db.cto_tasks.insert_one({"task_id": "task3", "status": "queued"})
        fail_proc = subprocess.CompletedProcess(
            args=["git", "clone"], returncode=128,
            stdout="", stderr="fatal: repository not found",
        )
        with patch.object(router_mod, "_sh", return_value=fail_proc):
            await router_mod._run_task_with_git(
                "task3", PROJ, "fix main.py please", ["main.py"], "",
                "ghp_anothertoken456",
            )
        task_row = await fake_db.cto_tasks.find_one({"task_id": "task3"})
        assert task_row is not None
        assert task_row["status"] == "failed"
        assert "ghp_anothertoken456" not in task_row["error"]
        assert "git clone failed" in task_row["error"]
