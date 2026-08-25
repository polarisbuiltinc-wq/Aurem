"""tests/test_phase2_ora_council_logger_coverage.py — Phase 2 (2026-08-28)

Targeted coverage wave for services/ora_council_logger.py (CI floor:
60%, prior CI measurement 29.5%). Covers _build_log field mapping,
_insert success/failure, log_conversational + log_code_task (fire-
and-forget task capture), get_council_stats (incl. _retriever_stats_safe
success/failure), ensure_indexes (success + duplicate-index swallow +
outer exception swallow), and export_daily_jsonl (pairs / no rows /
skipped-incomplete rows).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, patch

from services import ora_council_logger as ocl


# ═════════════════════════════════════════════════════════════════════
# Fakes
# ═════════════════════════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    def limit(self, n=None):
        return self

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []
        self.insert_calls: list[dict] = []
        self.fail_insert = False
        self.create_index_calls: list = []
        self.raise_on_index_name = None

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if row.get(k) != v:
                return False
        return True

    async def insert_one(self, doc):
        if self.fail_insert:
            raise RuntimeError("insert failed")
        self.insert_calls.append(doc)
        self.rows.append(dict(doc))

    async def count_documents(self, query=None):
        return sum(1 for r in self.rows if self._match(r, query))

    def find(self, query=None, projection=None):
        return _FakeCursor([r for r in self.rows if self._match(r, query)])

    async def update_many(self, query, update):
        ids = (query.get("_id") or {}).get("$in", [])
        n = 0
        for r in self.rows:
            if r.get("_id") in ids:
                r.update(update.get("$set") or {})
                n += 1
        import types
        return types.SimpleNamespace(modified_count=n)

    async def create_index(self, *a, **k):
        name = k.get("name")
        if name and name == self.raise_on_index_name:
            raise RuntimeError("index exists with a different name")
        self.create_index_calls.append((a, k))


class _FakeDB:
    def __init__(self):
        self._cols: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeCollection()
        return self._cols[name]

    def __getattr__(self, name):
        return self[name]


class _TaskCapture:
    """Intercepts asyncio.create_task so the fire-and-forget coroutine
    can be awaited deterministically inside the test instead of
    racing the test function's own completion."""
    def __init__(self):
        self.coros = []

    def __call__(self, coro):
        self.coros.append(coro)

        class _FakeTask:
            def __init__(self, c):
                self._c = c

        return _FakeTask(coro)


# ═════════════════════════════════════════════════════════════════════
# _build_log
# ═════════════════════════════════════════════════════════════════════

class TestBuildLog:
    def test_defaults_and_truncation(self):
        doc = ocl._build_log("A", "hi" * 2000, "reply" * 1000, "ora")
        assert doc["mode"] == "A"
        assert len(doc["user_message"]) == 2000
        assert len(doc["final_output"]) == 4000
        assert doc["correction_applied"] is False
        assert doc["agents_used_count"] == 1
        assert doc["ora_version"] == "2.0"
        assert doc["exported_for_training"] is False

    def test_kwargs_pass_through(self):
        doc = ocl._build_log(
            "C", "msg", "out", "deepseek",
            repo_context="ctx", deepseek_draft="draft", claude_correction="corr",
            correction_applied=True, pass_result=True, lint_blocked=True,
            lint_issues=["e1"], parallelized=True, agents_used_count=2,
            task_id="t1", user_id="u1", project_id="p1", maxx_mode=True,
            low_confidence=True,
        )
        assert doc["correction_applied"] is True
        assert doc["lint_blocked"] is True
        assert doc["low_confidence"] is True
        assert doc["task_id"] == "t1"


# ═════════════════════════════════════════════════════════════════════
# _insert
# ═════════════════════════════════════════════════════════════════════

class TestInsert:
    @pytest.mark.asyncio
    async def test_insert_success(self):
        db = _FakeDB()
        await ocl._insert(db, {"a": 1})
        assert db["ora_council_logs"].rows == [{"a": 1}]

    @pytest.mark.asyncio
    async def test_insert_failure_is_swallowed(self):
        db = _FakeDB()
        db["ora_council_logs"].fail_insert = True
        await ocl._insert(db, {"a": 1})  # must not raise


# ═════════════════════════════════════════════════════════════════════
# log_conversational / log_code_task
# ═════════════════════════════════════════════════════════════════════

class TestLogConversationalAndCodeTask:
    @pytest.mark.asyncio
    async def test_log_conversational_builds_and_fires_insert(self):
        db = _FakeDB()
        capture = _TaskCapture()
        with patch("services.ora_council_logger.asyncio.create_task", capture):
            await ocl.log_conversational(
                db, "A", "hello there", "hi back", user_id="u1",
                project_id="p1", low_confidence=True,
            )
        assert len(capture.coros) == 1
        await capture.coros[0]
        row = db["ora_council_logs"].rows[0]
        assert row["mode"] == "A"
        assert row["low_confidence"] is True
        assert row["agent_used"] == "ora"

    @pytest.mark.asyncio
    async def test_log_code_task_maxx_mode_agent_label(self):
        db = _FakeDB()
        capture = _TaskCapture()
        with patch("services.ora_council_logger.asyncio.create_task", capture):
            await ocl.log_code_task(
                db, "fix bug", "repo ctx", "draft code", "final code",
                correction_applied=True, pass_result=True,
                claude_correction="corrected", maxx_mode=True,
                task_id="t1", user_id="u1", project_id="p1",
            )
        await capture.coros[0]
        row = db["ora_council_logs"].rows[0]
        assert row["mode"] == "C"
        assert row["agent_used"] == "deepseek+claude"
        assert row["correction_applied"] is True

    @pytest.mark.asyncio
    async def test_log_code_task_non_maxx_agent_label(self):
        db = _FakeDB()
        capture = _TaskCapture()
        with patch("services.ora_council_logger.asyncio.create_task", capture):
            await ocl.log_code_task(
                db, "fix bug", "repo ctx", "draft code", "final code",
                correction_applied=False, pass_result=True,
            )
        await capture.coros[0]
        row = db["ora_council_logs"].rows[0]
        assert row["agent_used"] == "deepseek"


# ═════════════════════════════════════════════════════════════════════
# get_council_stats + _retriever_stats_safe
# ═════════════════════════════════════════════════════════════════════

class TestGetCouncilStats:
    @pytest.mark.asyncio
    async def test_empty_corpus_stats_shape(self):
        db = _FakeDB()
        stats = await ocl.get_council_stats(db)
        assert stats["total_interactions"] == 0
        assert stats["self_learning_active"] is False
        assert stats["ready_for_finetune"] is False
        assert "Collect 1000" in stats["finetune_tip"]
        assert "retriever" in stats

    @pytest.mark.asyncio
    async def test_stats_with_data_computes_rates(self):
        db = _FakeDB()
        coll = db["ora_council_logs"]
        now = datetime.now(timezone.utc)
        for i in range(6):
            coll.rows.append({
                "mode": "C", "correction_applied": (i < 2),
                "lint_blocked": (i == 5), "parallelized": (i < 3),
                "exported_for_training": False, "timestamp": now,
            })
        stats = await ocl.get_council_stats(db)
        assert stats["total_interactions"] == 6
        assert stats["by_mode"]["C_code"] == 6
        assert stats["corrections_applied"] == 2
        assert stats["correction_rate_pct"] == round(2 / 6 * 100, 1)
        assert stats["self_learning_active"] is True  # total >= 5

    def test_retriever_stats_safe_success(self):
        with patch("services.ora_council_retriever.get_retriever_stats",
                  return_value={"active": True, "corpus_rows": 10}):
            r = ocl._retriever_stats_safe()
        assert r == {"active": True, "corpus_rows": 10}

    def test_retriever_stats_safe_import_failure_returns_default(self):
        with patch.dict("sys.modules", {"services.ora_council_retriever": None}):
            r = ocl._retriever_stats_safe()
        assert r == {"active": False, "corpus_rows": 0}


# ═════════════════════════════════════════════════════════════════════
# ensure_indexes
# ═════════════════════════════════════════════════════════════════════

class TestEnsureIndexes:
    @pytest.mark.asyncio
    async def test_no_db_returns_early(self):
        with patch("cto_services.db.get_db", return_value=None):
            await ocl.ensure_indexes()  # must not raise

    @pytest.mark.asyncio
    async def test_success_creates_all_indexes(self):
        db = _FakeDB()
        with patch("cto_services.db.get_db", return_value=db):
            await ocl.ensure_indexes()
        assert len(db["ora_council_logs"].create_index_calls) >= 5
        assert len(db["project_brains"].create_index_calls) == 1
        assert len(db["issues_cache"].create_index_calls) >= 1

    @pytest.mark.asyncio
    async def test_duplicate_ttl_index_name_conflict_is_swallowed(self):
        db = _FakeDB()
        db["issues_cache"].raise_on_index_name = "issues_cache_ttl"
        with patch("cto_services.db.get_db", return_value=db):
            await ocl.ensure_indexes()  # must not raise despite the TTL index conflict

    @pytest.mark.asyncio
    async def test_outer_exception_is_swallowed(self):
        with patch("cto_services.db.get_db", side_effect=RuntimeError("db explode")):
            await ocl.ensure_indexes()  # must not raise


# ═════════════════════════════════════════════════════════════════════
# export_daily_jsonl
# ═════════════════════════════════════════════════════════════════════

class TestExportDailyJsonl:
    @pytest.mark.asyncio
    async def test_no_unexported_rows_returns_zero(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "sub", "latest.jsonl")
            result = await ocl.export_daily_jsonl(db, output_path=out_path)
        assert result == {"exported": 0, "file": out_path}

    @pytest.mark.asyncio
    async def test_exports_pairs_and_marks_exported(self):
        db = _FakeDB()
        coll = db["ora_council_logs"]
        coll.rows.append({
            "_id": "id1", "user_message": "hi", "final_output": "hello",
            "mode": "A", "correction_applied": False, "parallelized": False,
            "ora_version": "2.0", "exported_for_training": False,
        })
        # Incomplete row (missing final_output) must be skipped, not exported.
        coll.rows.append({
            "_id": "id2", "user_message": "no reply here",
            "exported_for_training": False,
        })
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "sub", "latest.jsonl")
            result = await ocl.export_daily_jsonl(db, output_path=out_path)
            assert result["exported"] == 1
            with open(out_path) as f:
                lines = f.readlines()
            assert len(lines) == 1
        exported_row = next(r for r in coll.rows if r["_id"] == "id1")
        assert exported_row["exported_for_training"] is True
        skipped_row = next(r for r in coll.rows if r["_id"] == "id2")
        assert skipped_row["exported_for_training"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
