"""
test_h3_b1_direct_task_pin_2026_08_30.py — Overnight Master Loop 2,
founder follow-up GO: H3 extended to the direct task-submit ship path
(routers/cto_projects.py::_run_task_via_api), plus B1-extend (the same
stale "not connected" cache clear now also fires on this path).

Named tests: t_direct_ship_pin_mismatch_aborts,
t_direct_ship_pin_matches_context, t_direct_ship_clears_not_connected.
"""
from __future__ import annotations

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
    "installation_id": "111",
}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture(autouse=True)
def _set_fake_db(fake_db):
    _dbmod.set_db(fake_db)
    yield
    _dbmod.set_db(None)


@pytest.fixture(autouse=True)
def _force_mock_off(monkeypatch):
    from services.ora_chat_v2 import llm_client
    monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", False)


async def _real_shaped_commit_files(owner, repo, branch, token, files,
                                     commit_message, author_name, author_email,
                                     progress=None):
    return {"ok": True, "sha": "abc1234", "full_sha": "abc1234" * 5,
            "html_url": f"https://github.com/{owner}/{repo}/commit/abc1234"}


async def _fake_fetch(owner, repo, path, ref, token):
    return "# widgets\n\nA test comment.\n"


@pytest.mark.asyncio
async def test_t_direct_ship_pin_mismatch_aborts(fake_db):
    """The project's live GitHub binding drifted (owner changed)
    between worker start and the real commit -> ABORT, zero writes,
    explicit user-visible error, no false 'done'."""
    await fake_db.cto_tasks.insert_one({"task_id": "task_pin", "status": "queued", "started_at": 0})
    await fake_db.dev_users.insert_one({"user_id": "u1", "tier": "pro"})
    # Live binding differs from PROJ (the pin captured at worker start).
    await fake_db.cto_projects.insert_one({
        **PROJ, "github_owner": "someone-else", "github_repo": "different-repo",
    })

    commits_called = []
    async def _spy_commit(*a, **k):
        commits_called.append((a, k))
        return await _real_shaped_commit_files(*a, **k)

    resume_edits = {"edits": {"README.md": "# widgets\n\nA test comment.\n"},
                    "summary": "test change"}

    with patch.object(router_mod, "gh_api_fetch_file", AsyncMock(side_effect=_fake_fetch)), \
         patch.object(router_mod, "gh_api_commit", _spy_commit), \
         patch("services.git_identity.resolve_git_identity",
               AsyncMock(return_value=("Jane Dev", "jane@example.com"))), \
         patch("services.vanguard_verify_agent.verify_patch",
               AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})):
        await router_mod._run_task_via_api(
            "task_pin", PROJ, "add a comment to README.md",
            ["README.md"], "", "ghp_faketoken789", resume_edits=resume_edits,
        )

    assert not commits_called, "a pin mismatch must make ZERO real GitHub writes"
    task_row = await fake_db.cto_tasks.find_one({"task_id": "task_pin"})
    assert task_row["status"] == "failed"
    assert "changed while this task was running" in task_row.get("error", "")


@pytest.mark.asyncio
async def test_t_direct_ship_pin_matches_context(fake_db):
    """Clean case — live binding matches the pin exactly -> commit
    proceeds normally (no over-guard)."""
    await fake_db.cto_tasks.insert_one({"task_id": "task_ok", "status": "queued", "started_at": 0})
    await fake_db.dev_users.insert_one({"user_id": "u1", "tier": "pro"})
    await fake_db.cto_projects.insert_one(dict(PROJ))

    resume_edits = {"edits": {"README.md": "# widgets\n\nA test comment.\n"},
                    "summary": "test change"}

    with patch.object(router_mod, "gh_api_fetch_file", AsyncMock(side_effect=_fake_fetch)), \
         patch.object(router_mod, "gh_api_commit", _real_shaped_commit_files), \
         patch("services.git_identity.resolve_git_identity",
               AsyncMock(return_value=("Jane Dev", "jane@example.com"))), \
         patch("services.vanguard_verify_agent.verify_patch",
               AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})):
        await router_mod._run_task_via_api(
            "task_ok", PROJ, "add a comment to README.md",
            ["README.md"], "", "ghp_faketoken789", resume_edits=resume_edits,
        )

    task_row = await fake_db.cto_tasks.find_one({"task_id": "task_ok"})
    assert task_row["status"] == "done", f"unexpected status/error: {task_row}"


@pytest.mark.asyncio
async def test_t_direct_ship_clears_not_connected(fake_db, monkeypatch):
    """B1-extend — a successful direct-ship commit must drop the
    connection-status cache row for that project (same fix as
    loop_engine.py's post-ship cache clear)."""
    from routers import repo_status
    repo_status._CACHE["p1"] = {"project_id": "p1", "status": "disconnected"}

    await fake_db.cto_tasks.insert_one({"task_id": "task_cache", "status": "queued", "started_at": 0})
    await fake_db.dev_users.insert_one({"user_id": "u1", "tier": "pro"})
    await fake_db.cto_projects.insert_one(dict(PROJ))

    resume_edits = {"edits": {"README.md": "# widgets\n\nA test comment.\n"},
                    "summary": "test change"}

    with patch.object(router_mod, "gh_api_fetch_file", AsyncMock(side_effect=_fake_fetch)), \
         patch.object(router_mod, "gh_api_commit", _real_shaped_commit_files), \
         patch("services.git_identity.resolve_git_identity",
               AsyncMock(return_value=("Jane Dev", "jane@example.com"))), \
         patch("services.vanguard_verify_agent.verify_patch",
               AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})):
        await router_mod._run_task_via_api(
            "task_cache", PROJ, "add a comment to README.md",
            ["README.md"], "", "ghp_faketoken789", resume_edits=resume_edits,
        )

    assert "p1" not in repo_status._CACHE
