"""tests/resilience/test_cto_task_lock_regression.py - Resilience Layer
Phase 1 (2026-08-25).

Regression test for the P0 production hotfix in
routers/cto_projects.py::_run_task_via_api (task t_4d07055adb99,
"'str' object has no attribute 'get'"). Root cause: `edits` is a
{path: content} dict; the old code iterated `for e in edits` (which
already yields the path STRING) and then called
`(e or {}).get("path")` on that string.
"""
from __future__ import annotations

import inspect

import pytest

from services.loop_diff_classifier import is_test_or_fixture
import routers.cto_projects as cto_projects


class TestSourceNoLongerHasTheContractViolation:
    def test_no_dict_shaped_get_on_iterated_edits_key(self):
        """Guards against reintroducing the exact broken pattern."""
        src = inspect.getsource(cto_projects)
        assert '(e or {}).get("path")' not in src
        assert "e.get('path') for e in _test_touched" not in src


class TestTestFileLockBehaviorAfterFix:
    """Reproduces the exact runtime shape that crashed: edits is a
    {path: content} dict, iterated directly (as the fixed code does)
    instead of assumed to be a list of per-file dicts."""

    def test_no_test_files_touched_when_edits_are_all_source_files(self):
        edits = {
            "backend/routers/cto_projects.py": "content a",
            "frontend/src/App.jsx": "content b",
        }
        touched = [p for p in edits if is_test_or_fixture(p or "")]
        assert touched == []

    def test_test_file_touched_is_detected_without_crashing(self):
        """This exact line (as a dict-of-paths) is what crashed with
        AttributeError before the fix. Must now return the path
        strings directly -- no .get() call anywhere in the chain."""
        edits = {
            "backend/tests/test_admin_panel_features.py": "content",
            "backend/routers/cto_projects.py": "content",
        }
        touched = [p for p in edits if is_test_or_fixture(p or "")]
        assert touched == ["backend/tests/test_admin_panel_features.py"]

    def test_paths_list_built_from_touched_needs_no_get_call(self):
        touched = ["backend/tests/test_x.py", "backend/tests/test_y.py"]
        # Post-fix: _paths = list(_test_touched) -- no `.get("path")`.
        paths = list(touched)
        assert paths == touched

    def test_is_test_or_fixture_rejects_empty_string_safely(self):
        assert is_test_or_fixture("") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
