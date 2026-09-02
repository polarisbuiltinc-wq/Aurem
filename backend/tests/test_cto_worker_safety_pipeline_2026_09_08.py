"""Phase 2 (backend/routers/cto_projects.py safety fix) — 2026-09-08.

THE SAFETY HOLE (captured BEFORE this round's fix, via a direct
source-scan of the two workers):

    _run_task_via_api   : hallucination-gate=YES  syntax-check=YES
                           lint-check=YES  vanguard-verify=YES
                           sensitive-path-guard=YES
    _run_task_with_git  : hallucination-gate=NO   syntax-check=NO
                           lint-check=NO   vanguard-verify=YES
                           sensitive-path-guard=YES

`_run_task_with_git` is the worker that actually runs whenever the
`git` binary is present on the host (`_GIT_AVAILABLE=True`) — i.e. the
REAL production runtime path for any host with git installed. It
committed straight to a customer's real repo with ZERO hallucination/
syntax/lint protection, while the API-only fallback path had all
three. This is the safety hole this round closes.

THE FIX: `_run_hallucination_gate`, `_run_syntax_gate`, and
`_run_lint_gate` are now shared, module-level functions in
`routers/cto_projects.py`, called by BOTH workers in the same order
(right after the sensitive-path guard, before Vanguard verify). The
COMMIT MECHANISM stays intentionally different — `_run_task_via_api`
still commits via the GitHub Data API (`gh_api_commit`), `_run_task_
with_git` still commits via the `git` binary (`git commit`/`git
push`). Only the safety STAGES were unified.

Classes below:
  TestStep1BeforeSnapshotNowClosed — the "before" proof (git worker
    was missing the 3 gates) + regression guard that it's now closed.
  TestGitWorkerSafetyPipeline — the 2 load-bearing proof tests.
  TestApiWorkerStillFull — the regression guard for the untouched
    (already-safe) worker.
"""
from __future__ import annotations

import re
import subprocess
import types
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from routers import cto_projects as router_mod
from services import cto_pipeline_steps as pipeline_mod
from cto_services import db as _dbmod

_CTO_PKG_DIR = Path("/app/backend/routers/cto_projects")


def _cto_worker_src() -> str:
    """`_run_task_via_api` and `_run_task_with_git` now live in
    separate submodules (2026-09-08 responsibility-based split) —
    concatenate both so `_extract_fn` still finds whichever one a
    test is looking for, unchanged from the pre-split behavior."""
    return (
        (_CTO_PKG_DIR / "worker_api.py").read_text()
        + "\n"
        + (_CTO_PKG_DIR / "worker_git.py").read_text()
    )


def _extract_fn(src: str, header: str) -> str:
    """Return the source of an async def function starting with `header`."""
    m = re.search(rf"^async def {re.escape(header)}\(", src, re.MULTILINE)
    assert m, f"function `{header}` not found"
    start = m.start()
    nxt = re.search(r"^(async def |def )", src[start + 1:], re.MULTILINE)
    end = (start + 1 + nxt.start()) if nxt else len(src)
    return src[start:end]


# ── Shared FakeDB (same pattern as test_iter_cto_projects_worker_coverage) ──

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
    "project_id": "p_saf", "user_id": "u_saf",
    "github_owner": "acme", "github_repo": "widgets", "branch": "main",
    "installation_id": "111", "tech_stack": "python",
}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture(autouse=True)
def _set_fake_db(fake_db):
    _dbmod.set_db(fake_db)
    yield
    _dbmod.set_db(None)


# ═════════════════════════════════════════════════════════════════════
# STEP 1 — before-snapshot, now closed
# ═════════════════════════════════════════════════════════════════════

class TestStep1BeforeSnapshotNowClosed:
    """See module docstring for the raw before/after evidence captured
    prior to this round's edit."""

    def test_api_worker_had_and_still_has_all_safety_stages(self):
        src = _cto_worker_src()
        fn = _extract_fn(src, "_run_task_via_api")
        assert "_run_hallucination_gate(" in fn
        assert "_run_syntax_gate(" in fn
        assert "_run_lint_gate(" in fn
        assert "verify_patch(" in fn
        assert "find_sensitive_paths(" in fn
        # commit mechanism unchanged — still the GitHub Data API.
        assert "gh_api_commit(" in fn

    def test_git_worker_was_missing_now_has_hallucination_syntax_lint(self):
        """BEFORE this round: zero references to `_run_hallucination_gate`,
        `_run_syntax_gate`, or `_run_lint_gate` existed anywhere in this
        function (those functions themselves didn't exist yet — the
        git worker had no equivalent inline code either). AFTER: all
        three now present, calling the SAME shared functions the API
        worker calls (not a re-implementation) — this is the safety
        hole closing."""
        src = _cto_worker_src()
        fn = _extract_fn(src, "_run_task_with_git")
        assert "_run_hallucination_gate(" in fn, (
            "safety hole NOT closed: git worker still missing the "
            "hallucination gate"
        )
        assert "_run_syntax_gate(" in fn, (
            "safety hole NOT closed: git worker still missing the "
            "syntax gate"
        )
        assert "_run_lint_gate(" in fn, (
            "safety hole NOT closed: git worker still missing the "
            "lint gate"
        )
        # Pre-existing gates must still be there too (regression guard).
        assert "verify_patch(" in fn
        assert "find_sensitive_paths(" in fn
        # Commit MECHANISM must stay the git binary — only the safety
        # STAGES were unified, not how each worker actually commits.
        assert '"git", "commit"' in fn
        assert '"git", "push"' in fn
        assert "gh_api_commit(" not in fn

    def test_shared_gates_are_called_in_the_same_relative_order_both_workers(self):
        """The 3 new gates must sit between the sensitive-path guard
        and Vanguard verify on BOTH workers — a subtle reorder here
        (e.g. syntax-check after commit) would be the single highest-
        risk regression this round could introduce."""
        src = _cto_worker_src()
        for header in ("_run_task_via_api", "_run_task_with_git"):
            fn = _extract_fn(src, header)
            idx_sensitive = fn.find("find_sensitive_paths(")
            idx_hallu = fn.find("_run_hallucination_gate(")
            idx_syntax = fn.find("_run_syntax_gate(")
            idx_lint = fn.find("_run_lint_gate(")
            idx_vanguard = fn.find("verify_patch(")
            for label, idx in (("sensitive-path-guard", idx_sensitive),
                                ("hallucination-gate", idx_hallu),
                                ("syntax-gate", idx_syntax),
                                ("lint-gate", idx_lint),
                                ("vanguard-verify", idx_vanguard)):
                assert idx != -1, f"{header}: {label} call not found"
            assert idx_sensitive < idx_hallu < idx_syntax < idx_lint < idx_vanguard, (
                f"{header}: safety stages are OUT OF ORDER — "
                f"sensitive={idx_sensitive} hallu={idx_hallu} "
                f"syntax={idx_syntax} lint={idx_lint} vanguard={idx_vanguard}"
            )


# ═════════════════════════════════════════════════════════════════════
# Load-bearing tests — git worker
# ═════════════════════════════════════════════════════════════════════

class TestGitWorkerSafetyPipeline:

    def _fake_sh_factory(self, repo_path: Path):
        def _fake_sh(cmd, cwd=None, timeout=None, **kwargs):
            if cmd[:2] == ["git", "clone"]:
                repo_path.mkdir(parents=True, exist_ok=True)
                (repo_path / "README.md").write_text("# widgets\n")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if len(cmd) > 1 and cmd[1] == "commit":
                return subprocess.CompletedProcess(cmd, 0, "1 file changed", "")
            if len(cmd) > 1 and cmd[1] == "rev-parse":
                stdout = "sha1234" if "--short" in cmd else "sha1234567890abcdef"
                return subprocess.CompletedProcess(cmd, 0, stdout, "")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return _fake_sh

    @pytest.mark.asyncio
    async def test_git_worker_runs_full_safety_pipeline(self, fake_db, tmp_path, monkeypatch):
        """LOAD-BEARING: proves _run_task_with_git now actually
        INVOKES the hallucination-gate, syntax-gate, and lint-gate
        (the 3 stages it was missing) — not just that they exist in
        source, but that a real run through the worker calls them."""
        monkeypatch.setattr(router_mod, "WORKSPACE", tmp_path)
        task_id = "safety_git_ok"
        repo_path = tmp_path / task_id / "repo"

        await fake_db.cto_tasks.insert_one({"task_id": task_id, "status": "queued"})
        await fake_db.dev_users.insert_one({"user_id": "u_saf", "tier": "pro"})
        await fake_db.cto_projects.insert_one(dict(PROJ))

        resume_edits = {"edits": {"feature.py": "def add(a, b):\n    return a + b\n"},
                        "summary": "add helper"}

        # 2026-09-08 follow-up: the 3 gates now live in
        # services/cto_pipeline_steps.py — spy there, not on router_mod.
        hallu_spy = MagicMock(wraps=pipeline_mod._hallucination_reasons)
        syntax_spy = MagicMock(wraps=pipeline_mod._syntax_errors)
        from services import design_linter as _dl
        lint_spy = MagicMock(wraps=_dl.lint_file_blocks)

        with patch.object(router_mod, "_sh", side_effect=self._fake_sh_factory(repo_path)), \
             patch.object(pipeline_mod, "_hallucination_reasons", hallu_spy), \
             patch.object(pipeline_mod, "_syntax_errors", syntax_spy), \
             patch("services.design_linter.lint_file_blocks", lint_spy), \
             patch("services.vanguard_verify_agent.verify_patch",
                   AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})) as vg_mock:
            await router_mod._run_task_with_git(
                task_id, PROJ, "add a helper function", [], "",
                "ghp_faketoken_safety1", resume_edits=resume_edits,
            )

        task_row = await fake_db.cto_tasks.find_one({"task_id": task_id})
        assert task_row is not None
        assert task_row["status"] == "done", f"unexpected: {task_row}"
        assert hallu_spy.called, "hallucination gate did NOT run on the git path"
        assert syntax_spy.called, "syntax gate did NOT run on the git path"
        assert lint_spy.called, "lint gate did NOT run on the git path"
        assert vg_mock.await_count >= 1, "vanguard verify did NOT run on the git path"

    @pytest.mark.asyncio
    async def test_git_worker_rejects_bad_codegen(self, fake_db, tmp_path, monkeypatch):
        """THE direct proof the safety hole is closed: a hallucinated/
        syntax-broken codegen output is fed to the git worker. BEFORE
        this round's fix this would have been written to disk and
        pushed (the git worker had no syntax check at all). AFTER:
        the syntax gate catches it, one auto-retry also fails (same
        broken reply), and the task is REJECTED — zero git writes."""
        monkeypatch.setattr(router_mod, "WORKSPACE", tmp_path)
        task_id = "safety_git_bad"
        repo_path = tmp_path / task_id / "repo"

        await fake_db.cto_tasks.insert_one({"task_id": task_id, "status": "queued"})
        await fake_db.dev_users.insert_one({"user_id": "u_saf", "tier": "pro"})
        await fake_db.cto_projects.insert_one(dict(PROJ))

        sh_calls: list = []

        def _fake_sh(cmd, cwd=None, timeout=None, **kwargs):
            sh_calls.append(cmd)
            if cmd[:2] == ["git", "clone"]:
                repo_path.mkdir(parents=True, exist_ok=True)
                (repo_path / "README.md").write_text("# widgets\n")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        BAD_REPLY = (
            "SUMMARY: add broken helper\n\nFILE: broken.py\n```\n"
            "def broken(:\n    pass\n```\n"
        )

        with patch.object(router_mod, "_sh", side_effect=_fake_sh), \
             patch.object(router_mod, "call_llm", AsyncMock(return_value=BAD_REPLY)), \
             patch.object(pipeline_mod, "call_llm", AsyncMock(return_value=BAD_REPLY)), \
             patch("asyncio.sleep", AsyncMock(return_value=None)):
            await router_mod._run_task_with_git(
                task_id, PROJ, "add a broken helper", [], "",
                "ghp_faketoken_safety2",
            )

        task_row = await fake_db.cto_tasks.find_one({"task_id": task_id})
        assert task_row is not None
        assert task_row["status"] == "failed", f"expected failed, got {task_row}"
        assert "syntax" in (task_row.get("error") or "").lower()

        # THE proof — no commit/push was ever attempted. Before this
        # round's fix, this exact broken content would have reached
        # `git add` / `git commit` / `git push` unblocked.
        write_calls = [c for c in sh_calls if len(c) > 1 and c[1] in ("add", "commit", "push")]
        assert write_calls == [], (
            f"safety hole NOT closed — git worker attempted to commit "
            f"bad code: {write_calls}"
        )

    @pytest.mark.asyncio
    async def test_git_binary_worker_path_guard_rejects_denied_path(self, fake_db, tmp_path, monkeypatch):
        """Guardrail Wave 1 (#2 path-guard, 2026-09-08 follow-up): the
        git-binary worker writes to disk + `git commit`/`git push`
        directly — BYPASSING `github_api_writer.commit_files` (where
        the write_guard deny-list already lived) entirely. In BLOCK
        mode, a denied path (.env) must now be rejected on this path
        too, and — the direct proof — no `git add`/`commit`/`push`
        must ever fire."""
        monkeypatch.setattr(router_mod, "WORKSPACE", tmp_path)
        monkeypatch.setattr("cto_services.db.get_db", lambda: fake_db)
        task_id = "safety_git_pathguard"
        repo_path = tmp_path / task_id / "repo"

        await fake_db.cto_tasks.insert_one({"task_id": task_id, "status": "queued"})
        await fake_db.dev_users.insert_one({"user_id": "u_saf", "tier": "pro"})
        await fake_db.cto_projects.insert_one(dict(PROJ))
        from services import write_guard as wg
        await fake_db.guard_config.update_one(
            {"_id": wg.RULE_PATH_GUARD}, {"$set": {"mode": "block"}}, upsert=True,
        )

        resume_edits = {"edits": {".env": "SECRET=abc123\n"},
                        "summary": "oops"}
        sh_calls: list = []

        def _fake_sh(cmd, cwd=None, timeout=None, **kwargs):
            sh_calls.append(cmd)
            if cmd[:2] == ["git", "clone"]:
                repo_path.mkdir(parents=True, exist_ok=True)
                (repo_path / "README.md").write_text("# widgets\n")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch.object(router_mod, "_sh", side_effect=_fake_sh), \
             patch("services.vanguard_verify_agent.verify_patch",
                   AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})):
            await router_mod._run_task_with_git(
                task_id, PROJ, "leak a secret", [], "",
                "ghp_faketoken_pathguard", resume_edits=resume_edits,
            )

        task_row = await fake_db.cto_tasks.find_one({"task_id": task_id})
        assert task_row is not None
        assert task_row["status"] == "failed", f"expected failed, got {task_row}"
        write_calls = [c for c in sh_calls if len(c) > 1 and c[1] in ("add", "commit", "push")]
        assert write_calls == [], (
            f"path-guard hole NOT closed — git worker attempted to "
            f"commit a denied path: {write_calls}"
        )


# ═════════════════════════════════════════════════════════════════════
# Regression guard — the already-safe worker must stay fully intact
# ═════════════════════════════════════════════════════════════════════

class TestApiWorkerStillFull:

    @pytest.mark.asyncio
    async def test_api_worker_still_full(self, fake_db):
        """Regression guard: the refactor (hoisting hallucination/
        syntax/lint into shared functions) must not have disturbed
        the already-safe API worker — it still runs every stage and
        still reaches a real commit."""
        task_id = "safety_api_ok"
        await fake_db.cto_tasks.insert_one({"task_id": task_id, "status": "queued", "started_at": 0})
        await fake_db.dev_users.insert_one({"user_id": "u_saf", "tier": "pro"})
        await fake_db.cto_projects.insert_one(dict(PROJ))

        resume_edits = {"edits": {"README.md": "# widgets\n\nA test comment.\n"},
                        "summary": "test change"}

        async def _fake_fetch(owner, repo, path, ref, token):
            return "# widgets\n\nA test comment.\n"

        async def _fake_commit(owner, repo, branch, token, files, commit_message,
                                author_name, author_email, progress=None):
            return {"sha": "abc1234", "full_sha": "abc1234" * 5}

        hallu_spy = MagicMock(wraps=pipeline_mod._hallucination_reasons)
        syntax_spy = MagicMock(wraps=pipeline_mod._syntax_errors)
        from services import design_linter as _dl
        lint_spy = MagicMock(wraps=_dl.lint_file_blocks)

        with patch.object(router_mod, "gh_api_fetch_file", AsyncMock(side_effect=_fake_fetch)), \
             patch.object(router_mod, "gh_api_commit", AsyncMock(side_effect=_fake_commit)), \
             patch.object(pipeline_mod, "_hallucination_reasons", hallu_spy), \
             patch.object(pipeline_mod, "_syntax_errors", syntax_spy), \
             patch("services.design_linter.lint_file_blocks", lint_spy), \
             patch("services.git_identity.resolve_git_identity",
                   AsyncMock(return_value=("Jane Dev", "jane@example.com"))), \
             patch("services.vanguard_verify_agent.verify_patch",
                   AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})) as vg_mock:
            await router_mod._run_task_via_api(
                task_id, PROJ, "add a comment to README.md",
                ["README.md"], "", "ghp_faketoken_safety3", resume_edits=resume_edits,
            )

        task_row = await fake_db.cto_tasks.find_one({"task_id": task_id})
        assert task_row is not None
        assert task_row["status"] == "done", f"unexpected: {task_row}"
        assert hallu_spy.called, "hallucination gate did NOT run on the API path"
        assert syntax_spy.called, "syntax gate did NOT run on the API path"
        assert lint_spy.called, "lint gate did NOT run on the API path"
        assert vg_mock.await_count >= 1, "vanguard verify did NOT run on the API path"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
