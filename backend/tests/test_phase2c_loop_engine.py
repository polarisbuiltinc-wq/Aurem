"""Phase 2c coverage wave — backend/services/loop_engine.py (2026-08-23).

Ship-adjacent file — founder's standing rule requires testing_agent
before this wave is considered done (it is the core loop/ship state
machine).

Real baseline (CONFIRMED, measured before writing anything new):
running the 69 pre-existing test files across this repo that already
import/exercise `services.loop_engine` directly (in-process — none of
them use the live-request style, so no root-cause repeat of the
codebase_health.py/admin_analytics.py finding here) gives:

    services/loop_engine.py   1425 stmts, 716 missed, 50% covered
    676 passed, 25 failed, 1 skipped (pre-existing failures — zero
    changes made to loop_engine.py or those test files before this
    measurement, so all 25 are CONFIRMED pre-existing, not caused by
    this wave)

This file adds targeted coverage for the two biggest remaining gaps
that are safely mockable in-process: `_do_ship` (the manual-ship gate
— 389 of ~425 lines uncovered) and the two security-scan helpers
`_run_security_scan` / `_run_diff_security_scan` (~147 lines
uncovered). It reuses the exact `_Coll`/`_DB`/`_make_engine` fake
pattern already established in
tests/test_iter212m131_loop_engine_rca.py rather than inventing a new
one.

Honest, scoped-out gap (not hidden): `_do_execute`'s LLM-generation
tail (~518 lines — Parliament council dispatch, generate_files(),
scope-drift guard combined with real multi-round LLM calls) and
`_generate_plan`'s planner-LLM tail are NOT covered by this wave —
driving them meaningfully would require mocking Council A's 3-member
parliament + generate_files() end-to-end, which is a materially
bigger and riskier effort than what's needed to clear the 60% floor
here. Flagged for a future dedicated wave if the founder wants
`_do_execute` itself pushed higher.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, patch


# ─── Fakes — same shape as test_iter212m131_loop_engine_rca.py ───────
class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    async def insert_one(self, d):
        self.rows.append(dict(d))
        class _R:
            inserted_id = "x"
        return _R()

    async def update_one(self, q, u, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                for k, v in (u.get("$set") or {}).items():
                    r[k] = v
                class _R:
                    modified_count = 1
                    upserted_id = None
                return _R()
        if upsert:
            self.rows.append({**q, **(u.get("$set") or {})})
        class _R:
            modified_count = 0
            upserted_id = "x" if upsert else None
        return _R()

    async def find_one(self, q, *_a, **_kw):
        for r in self.rows:
            if all(r.get(k) == v for k, v in (q or {}).items() if not isinstance(v, dict)):
                return dict(r)
        return None

    async def delete_one(self, q):
        for i, r in enumerate(list(self.rows)):
            if all(r.get(k) == v for k, v in q.items()):
                self.rows.pop(i)
                class _R:
                    deleted_count = 1
                return _R()
        class _R:
            deleted_count = 0
        return _R()


class _DB:
    def __init__(self):
        self.loop_sessions = _Coll()
        self.loop_backups = _Coll()
        self.loop_plans = _Coll()
        self.loop_lock = _Coll()
        self.loop_failures = _Coll()
        self.cto_projects = _Coll()


def _make_engine(db=None):
    from services import loop_engine as le
    db = db or _DB()
    return le.LoopEngine(
        db=db, loop_id="lp_test", user_id="u1",
        project_id="p1", user_message="ship me a feature",
    )


def _files_to_commit():
    return [{"path": "app.py", "content": "print('hi')\n"}]


# ═════════════════════════════════════════════════════════════════════
# _do_ship — the manual-ship gate (~389 previously-uncovered lines)
# ═════════════════════════════════════════════════════════════════════

class TestDoShip:
    def test_no_files_to_commit_pauses(self):
        eng = _make_engine()
        eng.context["submitted_files"] = []

        async def go():
            await eng._do_ship()

        asyncio.run(go())
        from services import loop_engine as le
        assert eng.state == le.LoopState.PAUSED_FOR_USER

    def test_integrity_guard_violation_fails_ship(self):
        eng = _make_engine()
        eng.bin_ctx = type("B", (), {"repo_owner": "acme", "repo_name": "widgets",
                                      "branch": "main", "pat": "tok"})()
        eng.context["submitted_files"] = _files_to_commit()

        with patch("services.loop_integrity_guard.check_file_integrity",
                  return_value={"rule_fired": "elision_marker", "offending_path": "app.py"}):
            asyncio.run(eng._do_ship())

        from services import loop_engine as le
        assert eng.state == le.LoopState.FAILED
        assert eng.context["integrity_guard"]["violations"]

    def test_integrity_guard_crash_fails_closed(self):
        eng = _make_engine()
        eng.bin_ctx = type("B", (), {"repo_owner": "acme", "repo_name": "widgets",
                                      "branch": "main", "pat": "tok"})()
        eng.context["submitted_files"] = _files_to_commit()

        with patch("services.loop_integrity_guard.check_file_integrity",
                  side_effect=RuntimeError("boom")):
            asyncio.run(eng._do_ship())

        from services import loop_engine as le
        assert eng.state == le.LoopState.FAILED

    def test_independent_verifier_rejects_pauses(self):
        eng = _make_engine()
        eng.bin_ctx = type("B", (), {"repo_owner": "acme", "repo_name": "widgets",
                                      "branch": "main", "pat": "tok"})()
        eng.context["submitted_files"] = _files_to_commit()

        with patch("services.loop_integrity_guard.check_file_integrity",
                  return_value=None), \
             patch("services.loop_diff_classifier.classify",
                  return_value={"source": ["app.py"], "tests": [],
                                "test_touched": False, "test_lines": []}), \
             patch("services.loop_independent_verifier.verify",
                  AsyncMock(return_value={"verdict": "no", "reason": "looks wrong",
                                          "verifier_model": "m1"})):
            asyncio.run(eng._do_ship())

        from services import loop_engine as le
        assert eng.state == le.LoopState.PAUSED_FOR_USER
        assert eng.context["independent_verifier"]["verdict"] == "no"

    def test_test_file_touched_requires_human_review(self):
        eng = _make_engine()
        eng.bin_ctx = type("B", (), {"repo_owner": "acme", "repo_name": "widgets",
                                      "branch": "main", "pat": "tok"})()
        eng.context["submitted_files"] = _files_to_commit()

        with patch("services.loop_integrity_guard.check_file_integrity",
                  return_value=None), \
             patch("services.loop_diff_classifier.classify",
                  return_value={"source": [], "tests": ["test_app.py"],
                                "test_touched": True, "test_lines": [1]}), \
             patch("services.loop_independent_verifier.verify",
                  AsyncMock(return_value={"verdict": "yes"})):
            asyncio.run(eng._do_ship())

        from services import loop_engine as le
        assert eng.state == le.LoopState.PAUSED_FOR_USER
        assert eng.context["requires_human_review"] is True
        assert eng.context["ship_pending"]["commit_message"]

    def test_normal_path_pauses_for_manual_ship_confirmation(self):
        eng = _make_engine()
        eng.bin_ctx = type("B", (), {"repo_owner": "acme", "repo_name": "widgets",
                                      "branch": "main", "pat": "tok"})()
        eng.context["submitted_files"] = _files_to_commit()
        eng.context["trust_level"] = "L2"

        with patch("services.loop_integrity_guard.check_file_integrity",
                  return_value=None), \
             patch("services.loop_diff_classifier.classify",
                  return_value={"source": ["app.py"], "tests": [],
                                "test_touched": False, "test_lines": []}), \
             patch("services.loop_independent_verifier.verify",
                  AsyncMock(return_value={"verdict": "yes"})):
            asyncio.run(eng._do_ship())

        from services import loop_engine as le
        assert eng.state == le.LoopState.PAUSED_FOR_USER
        assert eng.context["ship_pending"]["owner"] == "acme"
        assert "app.py" in eng.context["ship_pending"]["files"]

    def test_l3_trust_level_auto_confirms_ship(self):
        eng = _make_engine()
        eng.bin_ctx = type("B", (), {"repo_owner": "acme", "repo_name": "widgets",
                                      "branch": "main", "pat": "tok"})()
        eng.context["submitted_files"] = _files_to_commit()
        eng.context["trust_level"] = "L3"

        confirm_calls = []
        async def fake_confirm_ship(approved):
            confirm_calls.append(approved)
        eng.confirm_ship = fake_confirm_ship

        with patch("services.loop_integrity_guard.check_file_integrity",
                  return_value=None), \
             patch("services.loop_diff_classifier.classify",
                  return_value={"source": ["app.py"], "tests": [],
                                "test_touched": False, "test_lines": []}), \
             patch("services.loop_independent_verifier.verify",
                  AsyncMock(return_value={"verdict": "yes"})):
            asyncio.run(eng._do_ship())

        assert confirm_calls == [True]

    def test_no_github_linkage_fails_ship(self):
        eng = _make_engine()
        eng.bin_ctx = None
        eng.context["submitted_files"] = _files_to_commit()
        # No cto_projects row seeded -> project not found -> _fail_ship.

        asyncio.run(eng._do_ship())

        from services import loop_engine as le
        assert eng.state == le.LoopState.FAILED

    def test_empty_valid_files_after_filtering_fails_ship(self):
        eng = _make_engine()
        eng.bin_ctx = type("B", (), {"repo_owner": "acme", "repo_name": "widgets",
                                      "branch": "main", "pat": "tok"})()
        # path present but content is None -> filtered out -> empty files_dict
        eng.context["submitted_files"] = [{"path": "app.py", "content": None}]

        asyncio.run(eng._do_ship())

        from services import loop_engine as le
        assert eng.state == le.LoopState.FAILED


# ═════════════════════════════════════════════════════════════════════
# _run_security_scan (module-level helper, called from _do_scan)
# ═════════════════════════════════════════════════════════════════════

class TestRunSecurityScan:
    def test_no_project_id(self):
        from services import loop_engine as le
        result = asyncio.run(le._run_security_scan("u1", None))
        assert result["skipped_reason"] == "no_project"

    def test_no_db(self):
        from services import loop_engine as le
        from cto_services import db as _dbmod
        _dbmod.set_db(None)
        result = asyncio.run(le._run_security_scan("u1", "p1"))
        assert result["skipped_reason"] == "no_db"

    def test_no_project_doc(self):
        from services import loop_engine as le
        from cto_services import db as _dbmod
        db = _DB()
        _dbmod.set_db(db)
        try:
            result = asyncio.run(le._run_security_scan("u1", "p1"))
        finally:
            _dbmod.set_db(None)
        assert result["skipped_reason"] == "no_project_doc"

    def test_no_github_linkage(self):
        from services import loop_engine as le
        from cto_services import db as _dbmod
        db = _DB()
        db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "", "github_repo": "", "auth_method": None,
        })
        _dbmod.set_db(db)
        try:
            result = asyncio.run(le._run_security_scan("u1", "p1"))
        finally:
            _dbmod.set_db(None)
        assert "skipped_reason" in result

    def test_success_finds_a_hardcoded_secret(self):
        from services import loop_engine as le
        from cto_services import db as _dbmod
        db = _DB()
        db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
            "auth_method": "github_app", "installation_id": 1,
        })
        _dbmod.set_db(db)
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                      AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.security_scan._list_repo_tree",
                      AsyncMock(return_value=[{"path": "app.py", "size": 20}])), \
                 patch("routers.security_scan._fetch_file",
                      AsyncMock(return_value="API_KEY = 'sk-abcdef1234567890'\n")):
                result = asyncio.run(le._run_security_scan("u1", "p1"))
        finally:
            _dbmod.set_db(None)
        assert result["scanned_files"] == 1
        assert "summary" in result

    def test_github_tree_fetch_crash_returns_scan_error(self):
        from services import loop_engine as le
        from cto_services import db as _dbmod
        db = _DB()
        db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
            "auth_method": "github_app", "installation_id": 1,
        })
        _dbmod.set_db(db)
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                      AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.security_scan._list_repo_tree",
                      AsyncMock(side_effect=RuntimeError("boom"))):
                result = asyncio.run(le._run_security_scan("u1", "p1"))
        finally:
            _dbmod.set_db(None)
        assert "scan_error" in result


# ═════════════════════════════════════════════════════════════════════
# _run_diff_security_scan
# ═════════════════════════════════════════════════════════════════════

class TestRunDiffSecurityScan:
    def test_no_submitted_files(self):
        from services import loop_engine as le
        result = asyncio.run(le._run_diff_security_scan(_DB(), "u1", "p1", []))
        assert result["skipped_reason"] == "no_submitted_files"

    def test_no_project_id(self):
        from services import loop_engine as le
        result = asyncio.run(
            le._run_diff_security_scan(_DB(), "u1", None, _files_to_commit()))
        assert result["skipped_reason"] == "no_project"

    def test_no_db(self):
        from services import loop_engine as le
        result = asyncio.run(
            le._run_diff_security_scan(None, "u1", "p1", _files_to_commit()))
        assert result["skipped_reason"] == "no_db"

    def test_no_project_doc(self):
        from services import loop_engine as le
        result = asyncio.run(
            le._run_diff_security_scan(_DB(), "u1", "p1", _files_to_commit()))
        assert result["skipped_reason"] == "no_project_doc"

    def test_success_flags_only_changed_lines(self):
        from services import loop_engine as le
        db = _DB()
        db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
            "github_branch": "main", "auth_method": "github_app",
            "installation_id": 1,
        })
        with patch("services.pat_vault.get_repo_token", AsyncMock(return_value="tok")), \
             patch("services.github_api_writer.fetch_file",
                  AsyncMock(return_value="")), \
             patch("services.vanguard_verify_agent.changed_lines_for_file",
                  return_value={1}), \
             patch("services.vanguard_verify_agent.filter_findings_to_changed_lines",
                  side_effect=lambda findings, line_map: (findings, [])):
            result = asyncio.run(le._run_diff_security_scan(
                db, "u1", "p1",
                [{"path": "app.py", "content": "API_KEY = 'sk-abcdef1234567890'\n"}],
            ))
        assert result["diff_mode"] is True
        assert result["scanned_files"] == 1
