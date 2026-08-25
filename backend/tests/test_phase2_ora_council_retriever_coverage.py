"""tests/test_phase2_ora_council_retriever_coverage.py — Phase 2 (2026-08-28)

Targeted coverage wave for services/ora_council_retriever.py (CI
floor: 60%, prior CI measurement 25.6%). Complements the existing
test_iter212m77_council_retriever.py (public API happy paths) with
direct unit coverage of the internal helpers: _tokenize, _row_from_log,
_score, _candidate_indices (both bucket tiers + below-threshold),
_format_block, get_retriever_stats when active, and the
_maybe_refresh exception-swallow path.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import services.ora_council_retriever as r


class _AsyncCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        v = self._rows[self._i]
        self._i += 1
        return v


class _FakeColl:
    def __init__(self, rows):
        self.rows = rows

    def find(self, q=None, proj=None):
        return _AsyncCursor(self.rows)


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def __getitem__(self, name):
        return _FakeColl(self._rows)


def _row(msg, reply, mode="A", user="u1", project="p1",
         pass_result=True, lint_blocked=False, low_confidence=False):
    return {
        "user_message": msg, "final_output": reply, "mode": mode,
        "user_id": user, "project_id": project,
        "pass_result": pass_result, "lint_blocked": lint_blocked,
        "low_confidence": low_confidence,
        "timestamp": datetime.now(timezone.utc),
    }


@pytest.fixture(autouse=True)
def _reset():
    r._reset_for_tests()
    yield
    r._reset_for_tests()


# ═════════════════════════════════════════════════════════════════════
# _tokenize / _quality_filter / _row_from_log
# ═════════════════════════════════════════════════════════════════════

class TestTokenizeAndRowFromLog:
    def test_tokenize_lowercases_and_splits(self):
        assert r._tokenize("Hello World_2 !!") == ["hello", "world_2"]

    def test_tokenize_empty_string(self):
        assert r._tokenize("") == []

    def test_row_from_log_builds_tf_and_caps_length(self):
        doc = {"user_message": "a b a", "final_output": "reply", "mode": "B",
               "user_id": "u1", "project_id": "p1"}
        row = r._row_from_log(doc)
        assert row["tf"] == {"a": 2, "b": 1}
        assert row["n_tokens"] == 3
        assert row["mode"] == "B"

    def test_row_from_log_defaults_mode_to_a(self):
        row = r._row_from_log({"user_message": "hi", "final_output": "x"})
        assert row["mode"] == "A"

    def test_quality_filter_low_confidence_excluded(self):
        doc = _row("q", "a", low_confidence=True)
        assert r._quality_filter(doc) is False

    def test_quality_filter_missing_fields_excluded(self):
        assert r._quality_filter({"user_message": "", "final_output": "x"}) is False
        assert r._quality_filter({"user_message": "x", "final_output": ""}) is False


# ═════════════════════════════════════════════════════════════════════
# _score
# ═════════════════════════════════════════════════════════════════════

class TestScore:
    def test_score_zero_when_no_overlap(self):
        row = {"tf": {"apple": 2}, "n_tokens": 2}
        r._index["doc_freq"] = {"apple": 1}
        s = r._score({"banana": 1}, row, total_docs=1)
        assert s == 0.0

    def test_score_empty_query_tokens_returns_zero(self):
        row = {"tf": {"apple": 1}, "n_tokens": 1}
        assert r._score({}, row, total_docs=1) == 0.0

    def test_score_empty_row_tf_returns_zero(self):
        row = {"tf": {}, "n_tokens": 1}
        assert r._score({"apple": 1}, row, total_docs=1) == 0.0

    def test_score_identical_terms_is_positive(self):
        row = {"tf": {"apple": 2, "banana": 1}, "n_tokens": 3}
        r._index["doc_freq"] = {"apple": 1, "banana": 1}
        s = r._score({"apple": 2, "banana": 1}, row, total_docs=1)
        assert s > 0.0


# ═════════════════════════════════════════════════════════════════════
# _candidate_indices
# ═════════════════════════════════════════════════════════════════════

class TestCandidateIndices:
    @pytest.mark.asyncio
    async def test_tier1_user_project_mode_intersection(self):
        rows = [_row(f"q{i}", f"a{i}", mode="A", user="u1", project="p1")
                for i in range(20)]
        db = _FakeDB(rows)
        await r._rebuild_index(db)
        idx, label = r._candidate_indices("A", "u1", "p1")
        assert label == "user+project+mode"
        assert len(idx) == 20

    @pytest.mark.asyncio
    async def test_tier2_user_mode_when_project_mismatch(self):
        rows = [_row(f"q{i}", f"a{i}", mode="A", user="u1", project="p1")
                for i in range(20)]
        db = _FakeDB(rows)
        await r._rebuild_index(db)
        idx, label = r._candidate_indices("A", "u1", "other-project")
        assert label == "user+mode"
        assert len(idx) == 20

    @pytest.mark.asyncio
    async def test_below_threshold_no_cross_user_fallback(self):
        rows = [_row(f"q{i}", f"a{i}", mode="A", user="u1", project="p1")
                for i in range(3)]
        db = _FakeDB(rows)
        await r._rebuild_index(db)
        idx, label = r._candidate_indices("A", "u1", "p1")
        assert label == "below-threshold"
        assert idx == []

    @pytest.mark.asyncio
    async def test_no_user_id_returns_below_threshold(self):
        rows = [_row(f"q{i}", f"a{i}", mode="A") for i in range(20)]
        db = _FakeDB(rows)
        await r._rebuild_index(db)
        idx, label = r._candidate_indices("A", None, None)
        assert label == "below-threshold"
        assert idx == []


# ═════════════════════════════════════════════════════════════════════
# _format_block
# ═════════════════════════════════════════════════════════════════════

class TestFormatBlock:
    def test_format_block_renders_examples(self):
        block = r._format_block(
            [{"msg": "how do I do X", "reply": "do Y"}], "user+mode",
        )
        assert "[ORA COUNCIL — LEARNED EXAMPLES]" in block
        assert "USER: how do I do X" in block
        assert "ORA: do Y" in block
        assert "bucket: user+mode" in block
        assert "[END LEARNED EXAMPLES]" in block

    def test_format_block_empty_examples(self):
        block = r._format_block([], "below-threshold")
        assert "k=0" in block


# ═════════════════════════════════════════════════════════════════════
# get_retriever_stats when active + _maybe_refresh error swallow
# ═════════════════════════════════════════════════════════════════════

class TestStatsAndRefresh:
    @pytest.mark.asyncio
    async def test_stats_active_after_rebuild(self):
        rows = [_row(f"q{i}", f"a{i}", mode="A", user="u1", project="p1")
                for i in range(6)]
        db = _FakeDB(rows)
        await r._rebuild_index(db)
        stats = r.get_retriever_stats()
        assert stats["active"] is True
        assert stats["corpus_rows"] == 6
        assert stats["unique_users"] == 1
        assert stats["built_at_ago_s"] is not None

    @pytest.mark.asyncio
    async def test_maybe_refresh_swallows_rebuild_exception(self):
        class _BrokenDB:
            def __getitem__(self, name):
                raise RuntimeError("mongo exploded")
        await r._maybe_refresh(_BrokenDB())  # must not raise
        assert r._index["row_count"] == 0

    @pytest.mark.asyncio
    async def test_maybe_refresh_skips_when_fresh(self):
        rows = [_row(f"q{i}", f"a{i}") for i in range(6)]
        db = _FakeDB(rows)
        await r._rebuild_index(db)
        built_at_before = r._index["built_at"]
        await r._maybe_refresh(db)  # fresh — should be a no-op
        assert r._index["built_at"] == built_at_before


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
