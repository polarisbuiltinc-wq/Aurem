"""P0 hotfix (2026-08-28) — production regression.

Live-reproduced bug: asking ORA to ship a fix (and separately, to roll
back a commit) crashed with `commit_files() missing 2 required
positional arguments: 'author_email' and 'author_name'`, then wrapped
the crash in a misleading "update your profile" message.

Root cause #1: `routers/cto_projects.py::_run_task_via_api` (the
no-git-binary ship engine) called `gh_api_commit()` without ever
resolving/passing `author_name`/`author_email` — the ONLY commit_files
call site in the repo missing this. Every other caller (rollback,
loop_engine, visibility kit, local_tools) resolves identity first.

Root cause #2: `services/cto_projects_helpers.py::_set_status()` sent
the raw exception text to an LLM rewriter (`error_translator.py`) on
ANY task failure, with no guard for `INTERNAL_CALL_ERROR` (AUREM's own
bug, per `core/errors.py`). Since the raw TypeError text literally
contains "author_email"/"author_name", the LLM plausibly invented
"update your profile" guidance — blaming the user for a caller bug.
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
                for k, v in (update.get("$push") or {}).items():
                    r.setdefault(k, []).append(v)
                for k, v in (update.get("$inc") or {}).items():
                    r[k] = r.get(k, 0) + v
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
    return _FakeDB()


@pytest.fixture(autouse=True)
def _set_fake_db(fake_db):
    _dbmod.set_db(fake_db)
    yield
    _dbmod.set_db(None)


async def _real_shaped_commit_files(owner, repo, branch, token, files,
                                     commit_message, author_name, author_email,
                                     progress=None):
    """Mimics github_api_writer.commit_files()'s REAL signature exactly
    (author_name/author_email REQUIRED, no defaults). If the caller in
    cto_projects.py regresses back to omitting them, Python itself
    raises the exact production TypeError — no need to hand-roll one."""
    assert author_name, "author_name must be a real resolved identity"
    assert author_email, "author_email must be a real resolved identity"
    return {"ok": True, "sha": "abc1234", "full_sha": "abc1234" * 5,
            "html_url": f"https://github.com/{owner}/{repo}/commit/abc1234"}


class TestCommitFilesAuthorIdentityFix:
    """Root cause #1 — _run_task_via_api must resolve a real developer
    identity before calling commit_files(), same as every other caller."""

    @pytest.mark.asyncio
    async def test_resume_edits_ship_resolves_identity_and_commits_clean(self, fake_db):
        await fake_db.cto_tasks.insert_one({
            "task_id": "task_p0", "status": "queued", "started_at": 0,
        })
        await fake_db.dev_users.insert_one({"user_id": "u1", "tier": "pro"})
        # H3 hardening (2026-08-30) — _run_task_via_api now re-fetches
        # the project's live GitHub binding right before committing and
        # asserts it matches the pin captured at worker start. Seed the
        # fake DB with PROJ itself so that assert passes (happy path).
        await fake_db.cto_projects.insert_one(dict(PROJ))

        resume_edits = {
            "edits": {"README.md": "# widgets\n\nA test comment.\n"},
            "summary": "test change",
        }

        async def _fake_fetch(owner, repo, path, ref, token):
            # Serves both the pre-edit READ phase and the POST-PUSH
            # verify re-fetch — always returns the pushed content so
            # verification passes deterministically.
            return "# widgets\n\nA test comment.\n"

        with patch.object(router_mod, "gh_api_fetch_file",
                          AsyncMock(side_effect=_fake_fetch)), \
             patch.object(router_mod, "gh_api_commit", _real_shaped_commit_files), \
             patch("services.git_identity.resolve_git_identity",
                  AsyncMock(return_value=("Jane Dev", "jane@example.com"))) as _mock_identity, \
             patch("services.vanguard_verify_agent.verify_patch",
                  AsyncMock(return_value={"pass": True, "summary": "clean",
                                          "findings": []})):
            await router_mod._run_task_via_api(
                "task_p0", PROJ, "add a comment to README.md",
                ["README.md"], "", "ghp_faketoken789",
                resume_edits=resume_edits,
            )

        _mock_identity.assert_awaited()
        task_row = await fake_db.cto_tasks.find_one({"task_id": "task_p0"})
        assert task_row is not None
        # Before the fix, the missing-args TypeError would be caught
        # by the function's own broad except and land here as
        # status="failed" with the exact production error text.
        assert task_row["status"] == "done", task_row.get("error")
        assert task_row.get("commit_sha") == "abc1234"

    @pytest.mark.asyncio
    async def test_missing_identity_never_reaches_bare_typeerror(self, fake_db):
        """Even if identity resolution itself fails, resolve_git_identity
        is documented to never raise (falls back to a synthetic
        identity) — so the caller can never again hit a bare
        missing-argument TypeError from this call site."""
        await fake_db.cto_tasks.insert_one({
            "task_id": "task_p0b", "status": "queued", "started_at": 0,
        })
        await fake_db.dev_users.insert_one({"user_id": "u1", "tier": "pro"})
        resume_edits = {"edits": {"README.md": "content\n"}, "summary": "x"}

        async def _fake_fetch(owner, repo, path, ref, token):
            return "content\n"

        with patch.object(router_mod, "gh_api_fetch_file",
                          AsyncMock(side_effect=_fake_fetch)), \
             patch.object(router_mod, "gh_api_commit", _real_shaped_commit_files), \
             patch("services.vanguard_verify_agent.verify_patch",
                  AsyncMock(return_value={"pass": True, "summary": "clean",
                                          "findings": []})):
            await router_mod._run_task_via_api(
                "task_p0b", PROJ, "add a comment to README.md",
                ["README.md"], "", "ghp_faketoken789",
                resume_edits=resume_edits,
            )
        task_row = await fake_db.cto_tasks.find_one({"task_id": "task_p0b"})
        assert task_row is not None
        assert "missing" not in (task_row.get("error") or "").lower()
        assert "positional argument" not in (task_row.get("error") or "")


class TestInternalCallErrorNeverGoesToLlmTranslator:
    """Root cause #2 — an INTERNAL_CALL_ERROR must use the correct,
    human-reviewed catalog message, never the LLM rewrite (which
    hallucinated "update your profile" from raw exception text)."""

    @pytest.mark.asyncio
    async def test_internal_call_error_bypasses_llm_rewrite(self, fake_db):
        from services.cto_projects_helpers import _set_status

        await fake_db.cto_tasks.insert_one({"task_id": "task_p0c", "status": "running"})

        with patch("services.error_translator.translate",
                  AsyncMock(side_effect=AssertionError(
                      "LLM translator must NOT be called for INTERNAL_CALL_ERROR"))):
            await _set_status(
                "task_p0c", status="failed",
                error="commit_files() missing 2 required positional "
                      "arguments: 'author_email' and 'author_name'",
                error_code="INTERNAL_CALL_ERROR",
            )
        row = await fake_db.cto_tasks.find_one({"task_id": "task_p0c"})
        assert row["error_source"] == "internal_call_error_catalog"
        rendered = (row["error_plain"] + " ".join(row["error_steps"])).lower()
        for banned in ("update your profile", "check your profile",
                       "fix your profile", "update your account",
                       "check your account"):
            assert banned not in rendered
        assert "aurem" in rendered or "our" in rendered or "this one's on us" in row["error_plain"].lower()

    @pytest.mark.asyncio
    async def test_non_internal_error_still_uses_llm_translator(self, fake_db):
        """Regression guard the other way — a real user-data-shaped
        failure (e.g. repo_not_found) must still go through the
        existing static/LLM translator, unaffected by this fix."""
        from services.cto_projects_helpers import _set_status

        await fake_db.cto_tasks.insert_one({"task_id": "task_p0d", "status": "running"})
        await _set_status(
            "task_p0d", status="failed",
            error="repo_not_found: acme/widgets",
            error_code="SCHEMA_MISMATCH",
        )
        row = await fake_db.cto_tasks.find_one({"task_id": "task_p0d"})
        assert row["error_source"] == "static_table"
        assert "renamed" in row["error_plain"].lower() or "deleted" in row["error_plain"].lower()


class TestRollbackPersonaRecognizesRevertIntent:
    """P0-B — the system prompt must treat revert/rollback/undo as a
    real EXECUTE-mode mutation so the ```aurem-handoff fence (and
    therefore the Approve button) actually renders for these
    requests. Before this fix neither verb was in the recognized
    mutation-verb list, so the LLM correctly (per its own rules)
    never emitted a fence, and the Approve button never appeared."""

    def test_revert_rollback_undo_are_recognized_mutation_verbs(self):
        from services.orchestrator import AUREM_CTO_PERSONA
        verb_line_idx = AUREM_CTO_PERSONA.find("Fence MUST ")
        assert verb_line_idx != -1
        verb_region = AUREM_CTO_PERSONA[verb_line_idx:verb_line_idx + 400]
        for verb in ("revert", "rollback", "undo"):
            assert verb in verb_region

    def test_dedicated_rollback_guidance_section_present(self):
        from services.orchestrator import AUREM_CTO_PERSONA
        assert "ROLLBACK / REVERT / UNDO REQUESTS" in AUREM_CTO_PERSONA
