"""Sensitive-path guard (G3 real implementation) — end-to-end reproduction.

Verifies the block inserted 2026-08-27 in `routers/cto_projects.py`
worker functions `_run_task_via_api` and `_run_task_with_git` that
prevents an AI-generated task from silently modifying security-sensitive
files (payments.py, auth.py, stripe_client.py, mcp.py, vault*.py,
admin*.py, .github/workflows/*).

The founder's acceptance bar is a REAL forced-reproduction that
exercises the actual worker path with a mocked LLM returning a
sensitive-file edit, and confirms status='failed' with the expected
'security-sensitive' error BEFORE any commit/push happens.
"""
from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

from routers import cto_projects as router_mod
from cto_services import db as _dbmod
from services.sensitive_path_guard import is_sensitive_path, find_sensitive_paths


# ── Section 0 — Spot-check unit matching (founder ran 15 himself; a few here) ──

class TestSensitivePathMatchingSpotCheck:
    def test_exact_sensitive_basenames_matched(self):
        assert is_sensitive_path("backend/routers/payments.py") is True
        assert is_sensitive_path("auth.py") is True
        assert is_sensitive_path("foo/bar/stripe_client.py") is True
        assert is_sensitive_path("src/mcp.py") is True
        assert is_sensitive_path("vault_client.py") is True
        assert is_sensitive_path("admin_dashboard.py") is True

    def test_github_workflows_prefix_matched(self):
        assert is_sensitive_path(".github/workflows/ci.yml") is True
        assert is_sensitive_path("./.github/workflows/deploy.yml") is True

    def test_admin_segment_matched(self):
        assert is_sensitive_path("routers/admin/users.py") is True

    def test_no_false_positive_on_lookalikes(self):
        # Founder's stated non-negotiables
        assert is_sensitive_path("backend/utils/auth_helpers.py") is False
        assert is_sensitive_path("payments_util.py") is False
        assert is_sensitive_path("README.md") is False
        assert is_sensitive_path("backend/models/user.py") is False

    def test_find_returns_only_sensitive(self):
        got = find_sensitive_paths([
            "README.md", "auth.py", ".github/workflows/x.yml", "payments_util.py",
        ])
        assert got == ["auth.py", ".github/workflows/x.yml"]


# ── Fake DB reused pattern (from test_iter_cto_projects_worker_coverage_2026_08.py) ──

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
    "project_id": "p_sp", "user_id": "u_sp",
    "github_owner": "acme", "github_repo": "widgets", "branch": "main",
    "tech_stack": "python",
}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture(autouse=True)
def _set_fake_db(fake_db):
    _dbmod.set_db(fake_db)
    yield
    _dbmod.set_db(None)


def _llm_reply_for(path: str, body: str = "# hardened\ndef fn():\n    return 42\n") -> str:
    """Return a valid FILE: block reply the parse_file_blocks parser will accept."""
    return f"SUMMARY: edit {path}\n\nFILE: {path}\n```\n{body}```\n"


# ── Section 1 — _run_task_via_api: sensitive edit BLOCKED ──

class TestRunTaskViaApiSensitiveBlocked:
    @pytest.mark.asyncio
    async def test_edit_targeting_auth_py_is_blocked_with_security_sensitive_error(self, fake_db):
        """FORCED REPRODUCTION: task asks 'Edit auth.py …', LLM returns FILE: auth.py
        block → guard must fire → status='failed', error contains 'security-sensitive',
        and no downstream commit path is reached."""
        await fake_db.cto_tasks.insert_one(
            {"task_id": "sp_task_auth", "status": "queued",
             "allow_sensitive_file_change": False},
        )
        await fake_db.dev_users.insert_one({"user_id": "u_sp", "tier": "pro"})

        # Make sure no downstream commit code ever runs. If it does, the test
        # should scream loudly — that would mean the guard didn't fire in time.
        commit_spy = AsyncMock(
            side_effect=AssertionError("commit path reached — guard failed to block"),
        )

        with patch.object(router_mod, "gh_api_fetch_file",
                         AsyncMock(return_value="def old(): pass\n")), \
             patch.object(router_mod, "call_llm",
                         AsyncMock(return_value=_llm_reply_for("auth.py"))), \
             patch("services.subscription_tiers.can_use_feature", return_value=False), \
             patch("asyncio.sleep", AsyncMock(return_value=None)), \
             patch.object(router_mod, "_commit_via_git_data_api", commit_spy,
                         create=True):
            await router_mod._run_task_via_api(
                "sp_task_auth", PROJ,
                "Edit auth.py and add a comment", ["auth.py"], "",
                "ghp_faketoken_sp1",
            )

        row = await fake_db.cto_tasks.find_one({"task_id": "sp_task_auth"})
        assert row is not None, "task row missing"
        assert row["status"] == "failed", f"expected failed, got {row['status']}"
        err = row.get("error") or ""
        assert "security-sensitive" in err, f"error did not mention security-sensitive: {err!r}"
        assert "auth.py" in err

    @pytest.mark.asyncio
    async def test_edit_targeting_github_workflows_is_blocked(self, fake_db):
        await fake_db.cto_tasks.insert_one(
            {"task_id": "sp_task_wf", "status": "queued",
             "allow_sensitive_file_change": False},
        )
        await fake_db.dev_users.insert_one({"user_id": "u_sp", "tier": "pro"})
        with patch.object(router_mod, "gh_api_fetch_file",
                         AsyncMock(return_value="name: ci\n")), \
             patch.object(router_mod, "call_llm",
                         AsyncMock(return_value=_llm_reply_for(
                             ".github/workflows/ci.yml", body="name: hacked\n"))), \
             patch("services.subscription_tiers.can_use_feature", return_value=False), \
             patch("asyncio.sleep", AsyncMock(return_value=None)):
            await router_mod._run_task_via_api(
                "sp_task_wf", PROJ,
                "Edit .github/workflows/ci.yml", [".github/workflows/ci.yml"], "",
                "ghp_faketoken_sp2",
            )
        row = await fake_db.cto_tasks.find_one({"task_id": "sp_task_wf"})
        assert row["status"] == "failed"
        assert "security-sensitive" in (row.get("error") or "")

    @pytest.mark.asyncio
    async def test_allow_flag_overrides_block(self, fake_db):
        """If cto_tasks.allow_sensitive_file_change=True, guard must NOT
        fire (the block is fail-closed by default but overridable per-task).
        We can't assert final status='done' without wiring the whole
        commit path — instead we assert the error is NOT the security-sensitive
        wording (whatever else fails downstream is fine)."""
        await fake_db.cto_tasks.insert_one(
            {"task_id": "sp_task_allow", "status": "queued",
             "allow_sensitive_file_change": True},
        )
        await fake_db.dev_users.insert_one({"user_id": "u_sp", "tier": "pro"})
        with patch.object(router_mod, "gh_api_fetch_file",
                         AsyncMock(return_value="def old(): pass\n")), \
             patch.object(router_mod, "call_llm",
                         AsyncMock(return_value=_llm_reply_for("auth.py"))), \
             patch("services.subscription_tiers.can_use_feature", return_value=False), \
             patch("asyncio.sleep", AsyncMock(return_value=None)):
            await router_mod._run_task_via_api(
                "sp_task_allow", PROJ,
                "Edit auth.py", ["auth.py"], "",
                "ghp_faketoken_sp3",
            )
        row = await fake_db.cto_tasks.find_one({"task_id": "sp_task_allow"})
        # Whatever happened downstream, it must NOT be the sensitive-path block.
        err = row.get("error") or ""
        assert "security-sensitive" not in err, (
            f"allow_sensitive_file_change=True was ignored — guard fired anyway: {err!r}"
        )


# ── Section 2 — _run_task_via_api: NORMAL file NOT blocked ──

class TestRunTaskViaApiNormalFileUnaffected:
    @pytest.mark.asyncio
    async def test_edit_targeting_readme_is_not_blocked_by_guard(self, fake_db):
        """Normal file (README.md) must NOT trigger the security-sensitive
        block — the task may still fail downstream on commit setup, but
        the error must NEVER be the sensitive-path wording."""
        await fake_db.cto_tasks.insert_one(
            {"task_id": "sp_task_readme", "status": "queued",
             "allow_sensitive_file_change": False},
        )
        await fake_db.dev_users.insert_one({"user_id": "u_sp", "tier": "pro"})
        with patch.object(router_mod, "gh_api_fetch_file",
                         AsyncMock(return_value="# My Repo\n")), \
             patch.object(router_mod, "call_llm",
                         AsyncMock(return_value=_llm_reply_for(
                             "README.md", body="# My Repo\n\nNew line\n"))), \
             patch("services.subscription_tiers.can_use_feature", return_value=False), \
             patch("asyncio.sleep", AsyncMock(return_value=None)):
            await router_mod._run_task_via_api(
                "sp_task_readme", PROJ,
                "Edit README.md and add a line", ["README.md"], "",
                "ghp_faketoken_sp4",
            )
        row = await fake_db.cto_tasks.find_one({"task_id": "sp_task_readme"})
        err = row.get("error") or ""
        assert "security-sensitive" not in err, (
            f"guard false-positive on README.md: {err!r}"
        )


# ── Section 3 — _run_task_with_git: primary path (git binary present) ──

class TestRunTaskWithGitSensitiveBlocked:
    @pytest.mark.asyncio
    async def test_git_path_blocks_payments_py_edit(self, fake_db, tmp_path,
                                                    monkeypatch):
        """Primary worker path (git subprocess). Mock _sh so clone
        'succeeds' (files pre-planted in the workspace), LLM returns a
        FILE: block for payments.py → guard must block, no push."""
        # Point WORKSPACE at a temp dir so `WORKSPACE / task_id / repo`
        # is writeable and doesn't collide with other runs.
        monkeypatch.setattr(router_mod, "WORKSPACE", tmp_path)

        task_id = "sp_task_git_pay"
        repo_path = tmp_path / task_id / "repo"

        await fake_db.cto_tasks.insert_one(
            {"task_id": task_id, "status": "queued",
             "allow_sensitive_file_change": False},
        )
        await fake_db.dev_users.insert_one({"user_id": "u_sp", "tier": "pro"})

        # _sh is called for `git clone` first, then various later git ops.
        # We simulate a successful clone by creating the repo dir + a
        # payments.py file, and returning rc=0 for all _sh calls up to
        # the point the guard fires (which is before any commit-related
        # _sh call).
        def _fake_sh(cmd, cwd=None, timeout=None, **kwargs):
            if cmd[:2] == ["git", "clone"]:
                repo_path.mkdir(parents=True, exist_ok=True)
                (repo_path / "payments.py").write_text("def charge(): pass\n")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr="",
            )

        with patch.object(router_mod, "_sh", side_effect=_fake_sh), \
             patch.object(router_mod, "call_llm",
                         AsyncMock(return_value=_llm_reply_for("payments.py"))), \
             patch("services.subscription_tiers.can_use_feature", return_value=False), \
             patch("asyncio.sleep", AsyncMock(return_value=None)):
            await router_mod._run_task_with_git(
                task_id, PROJ,
                "Edit routers/payments.py and change the header comment",
                ["payments.py"], "",
                "ghp_faketoken_sp5",
            )

        row = await fake_db.cto_tasks.find_one({"task_id": task_id})
        assert row is not None
        assert row["status"] == "failed", f"expected failed, got {row.get('status')}"
        err = row.get("error") or ""
        assert "security-sensitive" in err, f"expected sensitive block, got: {err!r}"
        assert "payments.py" in err
