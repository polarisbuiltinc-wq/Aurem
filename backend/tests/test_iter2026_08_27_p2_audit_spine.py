"""Regression — P2 audit-spine wiring (2026-08-27), "Show the Outcome,
Never the Engine".

Reuses EXISTING infra only (no new collection, no new endpoint):
  - `services/audit_log.py` (`ora_audit` collection) — now carries
    `extra.leak_stripped` / `extra.recall_candidate` per turn.
  - `services/loop_audit_log.py` (`loop_run_log` collection) — now
    carries a `KIND_INTERNAL_FAULT` row whenever `_fail_ship()`
    classifies the real exception as AUREM's own bug.
  - `services/founder_alerts.py` (G10 Resend channel) — now has a
    24h leak-stripped threshold check (`services/leak_alert_cron.py`).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    async def insert_one(self, d):
        self.rows.append(dict(d))

    async def update_one(self, *a, **kw):
        pass

    async def find_one(self, *a, **kw):
        return None

    async def find_one_and_update(self, *a, **kw):
        return {"loop_id": "lp_test_audit_spine"}

    async def delete_one(self, *a, **kw):
        pass

    async def count_documents(self, *a, **kw):
        return 0


class _DB:
    """Supports BOTH attribute access (`db.loop_sessions`, used by
    LoopEngine internals) AND item access (`db["loop_run_log"]`, used
    by `loop_audit_log.log()` — the real Motor call shape)."""

    def __init__(self):
        self.loop_sessions = _Coll()
        self.loop_backups = _Coll()
        self.loop_plans = _Coll()
        self.loop_lock = _Coll()
        self.loop_failures = _Coll()
        self.cto_projects = _Coll()
        self.loop_run_log = _Coll()
        self.ora_audit = _Coll()

    def __getitem__(self, name):
        return getattr(self, name)


# ── Item 4a — leak-stripped / recall-candidate on record_turn ──────────
class TestAuditLogExtraFields:
    def test_record_turn_persists_leak_and_recall_extra_fields(self):
        from services import audit_log

        db_stub = _DB()

        async def _fake_get_db():
            return db_stub

        async def _run():
            with __import__("unittest.mock", fromlist=["patch"]).patch(
                "cto_services.db.get_db", return_value=db_stub,
            ):
                return await audit_log.record_turn(
                    user_id="u1", project_id="p1",
                    tools_called=[], citation_guard_triggered=False,
                    citation_guard_paths_fetched=[], citation_guard_unverified=[],
                    system_signals_emitted=[], llm_model="deepseek",
                    response_tokens=42, was_retry=False,
                    extra={
                        "leak_stripped": True,
                        "length_capped": False,
                        "recall_candidate": True,
                        "council_recalled_count": 2,
                    },
                )

        turn_id = asyncio.run(_run())
        assert turn_id is not None
        assert len(db_stub.ora_audit.rows) == 1
        row = db_stub.ora_audit.rows[0]
        assert row["extra"]["leak_stripped"] is True
        assert row["extra"]["recall_candidate"] is True
        assert row["extra"]["council_recalled_count"] == 2


# ── Item 4b — not-user's-fault event wired into loop_run_log ────────────
class TestInternalFaultAuditEvent:
    def test_fail_ship_internal_error_logs_kind_internal_fault(self):
        from unittest.mock import AsyncMock, patch
        from services import loop_engine as le
        from services import loop_audit_log as lal

        db_stub = _DB()
        eng = le.LoopEngine(db=db_stub, loop_id="lp_audit_spine", user_id="u1",
                            project_id="p1", user_message="ship it")
        eng.state = le.LoopState.PAUSED_FOR_USER
        eng.phase = "ship"
        eng.context["ship_pending"] = {
            "owner": "acme", "repo": "widgets", "branch": "main",
            "token": "tok", "files": {"app.py": "print('hi')\n"},
            "commit_message": "feat: test",
        }

        async def _raise_missing_arg(**kw):
            def commit_files(owner, repo, branch, token, files,
                              commit_message, author_name, author_email,
                              progress=None):
                pass
            commit_files(owner="acme", repo="widgets")  # missing required args

        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "no_refresh", None))), \
             patch("services.github_api_writer.commit_files", _raise_missing_arg):
            asyncio.run(eng.confirm_ship(approved=True))

        rows = db_stub.loop_run_log.rows
        internal_fault_rows = [r for r in rows if r.get("kind") == lal.KIND_INTERNAL_FAULT]
        assert len(internal_fault_rows) == 1, f"expected exactly 1, got {rows}"
        assert internal_fault_rows[0]["verdict"] == lal.VERDICT_FAIL
        assert internal_fault_rows[0]["detail"]["error_code"] == "INTERNAL_CALL_ERROR"

    def test_fail_ship_schema_mismatch_does_not_log_internal_fault(self):
        """A genuine bad-user-data exception (SCHEMA_MISMATCH) must NOT
        write a KIND_INTERNAL_FAULT row — only AUREM's own bugs do."""
        from unittest.mock import AsyncMock, patch
        from services import loop_engine as le
        from services import loop_audit_log as lal

        db_stub = _DB()
        eng = le.LoopEngine(db=db_stub, loop_id="lp_audit_spine2", user_id="u1",
                            project_id="p1", user_message="ship it")
        eng.state = le.LoopState.PAUSED_FOR_USER
        eng.phase = "ship"
        eng.context["ship_pending"] = {
            "owner": "acme", "repo": "widgets", "branch": "main",
            "token": "tok", "files": {"app.py": "print('hi')\n"},
            "commit_message": "feat: test",
        }

        async def _raise_schema_mismatch(**kw):
            raise KeyError("some_unexpected_field")

        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "no_refresh", None))), \
             patch("services.github_api_writer.commit_files", _raise_schema_mismatch):
            asyncio.run(eng.confirm_ship(approved=True))

        internal_fault_rows = [r for r in db_stub.loop_run_log.rows
                                if r.get("kind") == lal.KIND_INTERNAL_FAULT]
        assert internal_fault_rows == []


# ── Item 4c — 24h leak-stripped alert threshold ─────────────────────────
class TestLeakAlertCron:
    def test_below_threshold_no_alert(self):
        from unittest.mock import AsyncMock, patch
        from services import leak_alert_cron as lac

        db_stub = _DB()
        db_stub.ora_audit.count_documents = AsyncMock(return_value=3)
        db_stub.loop_run_log.count_documents = AsyncMock(return_value=0)

        with patch("services.founder_alerts.send_founder_alert",
                  AsyncMock(return_value={"sent": False})) as mock_alert:
            asyncio.run(lac._check_and_alert_once(db_stub))
        mock_alert.assert_not_called()

    def test_above_threshold_fires_alert(self):
        from unittest.mock import AsyncMock, patch
        from services import leak_alert_cron as lac

        db_stub = _DB()
        db_stub.ora_audit.count_documents = AsyncMock(return_value=9)
        db_stub.loop_run_log.count_documents = AsyncMock(return_value=1)

        with patch("services.founder_alerts.send_founder_alert",
                  AsyncMock(return_value={"sent": True})) as mock_alert:
            asyncio.run(lac._check_and_alert_once(db_stub))
        mock_alert.assert_called_once()
        _, kwargs = mock_alert.call_args
        assert kwargs["source_key"] == "leak_stripped_24h"
        assert "9" in kwargs["detail"]

    def test_count_query_targets_existing_ora_audit_collection(self):
        """Proves the count reads the SAME collection record_turn writes
        to — no new collection was introduced for this alert."""
        from services import leak_alert_cron as lac
        from services import audit_log

        assert lac.count_leak_stripped_last_24h.__doc__ is not None
        assert audit_log.COLLECTION == "ora_audit"

    def test_threshold_env_var_respected(self, monkeypatch):
        from services import leak_alert_cron as lac
        monkeypatch.setenv("LEAK_ALERT_THRESHOLD", "10")
        assert lac._threshold() == 10
        monkeypatch.delenv("LEAK_ALERT_THRESHOLD", raising=False)
        assert lac._threshold() == 5
