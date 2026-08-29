"""
test_h3_loop_repo_pin_2026_08_30.py — Overnight Master Loop 2 / X1
cross-project round, W2/H3 (built on founder's explicit follow-up GO).

At loop-run start, {user_id, project_id, repo, branch, installation}
is pinned (captured once into `self.context["_pinned_installation_id"]`
plus the immutable `owner`/`repo`/`branch` already sourced from
`bin_ctx`/the DB at staging time). Before every real write, confirm_ship
re-fetches the LIVE binding and asserts it still matches — mismatch
aborts with an explicit error, zero writes.

Named tests: t_loop_repos_pinned, t_loop_pin_blocks_stray_write,
t_loop_pin_matches_context.
"""
from __future__ import annotations

from collections import deque

import pytest

from services import loop_engine as le


def _mk_engine(cto_projects_find_one, ship_pending, pinned_installation_id=None):
    class _FakeLoopSessions:
        async def find_one_and_update(self, q, u):
            return {"loop_id": "loop_x", "context": {}}
        async def find_one(self, *a, **k):
            return {"loop_id": "loop_x", "context": {}, "state": "shipping"}

    class _FakeCtoProjects:
        async def find_one(self, *a, **k):
            return cto_projects_find_one

    class _FakeDB:
        loop_sessions = _FakeLoopSessions()
        cto_projects  = _FakeCtoProjects()
        class _FakeTrustEvents:
            async def insert_one(self, *a, **k):
                return None
        trust_surface_events = _FakeTrustEvents()

    eng = le.LoopEngine.__new__(le.LoopEngine)
    eng.db          = _FakeDB()
    eng.loop_id     = "loop_x"
    eng.user_id     = "u1"
    eng.project_id  = "p1"
    eng.user_message = "Add a new endpoint"
    eng.state       = le.LoopState.PAUSED_FOR_USER
    eng.phase       = "ship"
    ctx = {"ship_pending": ship_pending}
    if pinned_installation_id is not None:
        ctx["_pinned_installation_id"] = pinned_installation_id
    eng.context = ctx
    eng._narration_ring = deque(maxlen=16)
    return eng


@pytest.fixture(autouse=True)
def _force_mock_off(monkeypatch):
    from services.ora_chat_v2 import llm_client
    monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", False)


@pytest.mark.asyncio
async def test_t_loop_repos_pinned(monkeypatch):
    """Force a context mismatch mid-loop -> loop aborts, ZERO writes to
    the wrong repo, explicit error shown."""
    commits_called = []

    async def fake_commit_files(**kw):
        commits_called.append(kw)
        return {"sha": "should-never-happen"}

    monkeypatch.setattr("services.github_api_writer.commit_files", fake_commit_files)
    monkeypatch.setattr(le, "_persist_session", lambda *a, **k: _noop())

    eng = _mk_engine(
        cto_projects_find_one={
            "github_owner": "SOMEONE-ELSE", "github_repo": "different-repo",
            "github_branch": "main", "installation_id": "999",
        },
        ship_pending={
            "owner": "TJSNDHU", "repo": "Aurem", "branch": "main",
            "token": "ghp_x", "files": {"app.py": "x = 1\n"},
            "commit_message": "feat: add endpoint",
        },
        pinned_installation_id="111",
    )
    emits = []
    async def fake_emit(state, phase, **kw):
        emits.append({"state": state.value if hasattr(state, "value") else state, **kw})
    eng._emit = fake_emit

    await eng.confirm_ship(True)

    assert not commits_called, "a pin mismatch must make ZERO real GitHub writes"
    assert eng.state != le.LoopState.COMPLETED
    assert eng.state == le.LoopState.FAILED
    fail_events = [e for e in emits if e.get("state") == "failed"]
    assert fail_events, "an explicit, user-visible failure must be emitted"
    assert any("changed while this ship was waiting" in e.get("message", "") for e in fail_events)
    assert fail_events[0]["data"]["requires_user_action"] is True


@pytest.mark.asyncio
async def test_t_loop_pin_blocks_stray_write(monkeypatch):
    """Same guard, different mismatched field (branch drifted, not
    owner/repo) — still a hard abort, still zero writes."""
    commits_called = []

    async def fake_commit_files(**kw):
        commits_called.append(kw)
        return {"sha": "should-never-happen"}

    monkeypatch.setattr("services.github_api_writer.commit_files", fake_commit_files)
    monkeypatch.setattr(le, "_persist_session", lambda *a, **k: _noop())

    eng = _mk_engine(
        cto_projects_find_one={
            "github_owner": "TJSNDHU", "github_repo": "Aurem",
            "github_branch": "a-different-branch", "installation_id": None,
        },
        ship_pending={
            "owner": "TJSNDHU", "repo": "Aurem", "branch": "main",
            "token": "ghp_x", "files": {"app.py": "x = 1\n"},
            "commit_message": "feat: add endpoint",
        },
    )
    eng._emit = lambda *a, **k: _noop()

    await eng.confirm_ship(True)

    assert not commits_called, "a branch-only drift must still block the write"


@pytest.mark.asyncio
async def test_t_loop_pin_matches_context(monkeypatch):
    """Clean case — live binding matches the pin exactly -> the write
    proceeds normally (no over-guard)."""
    commits_called = []

    async def fake_commit_files(**kw):
        commits_called.append(kw)
        return {"sha": "abc1234", "full_sha": "abc1234deadbeef",
                "html_url": "https://github.com/o/r/commit/abc1234"}

    monkeypatch.setattr("services.github_api_writer.commit_files", fake_commit_files)
    monkeypatch.setattr(le, "_persist_session", lambda *a, **k: _noop())

    eng = _mk_engine(
        cto_projects_find_one={
            "github_owner": "TJSNDHU", "github_repo": "Aurem",
            "github_branch": "main", "installation_id": "111",
        },
        ship_pending={
            "owner": "TJSNDHU", "repo": "Aurem", "branch": "main",
            "token": "ghp_x", "files": {"app.py": "x = 1\n"},
            "commit_message": "feat: add endpoint",
        },
        pinned_installation_id="111",
    )
    eng._emit = lambda *a, **k: _noop()

    await eng.confirm_ship(True)

    assert commits_called, "a matching pin must NOT be blocked (no over-guard)"
    assert eng.state == le.LoopState.COMPLETED


async def _noop(*a, **k):
    return None
