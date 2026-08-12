"""
tests/test_iter388g_diff_view_hunks.py — Iter 388g

Unit tests for `services.task_diff.build_unified_diff_hunks` and the
tool_output_wrapper envelope helpers.  These lock in the SSE payload
contract the EditedFileBubble frontend consumes so a future refactor
can't silently change the shape.
"""
from services.task_diff import build_unified_diff_hunks
from services.ora_chat.tool_output_wrapper import (
    wrap_edited_files, wrap_command_exec,
)


class TestBuildUnifiedDiffHunks:
    def test_identical_files_produce_no_hunks(self):
        assert build_unified_diff_hunks("same\n", "same\n") == []

    def test_new_file_all_additions(self):
        h = build_unified_diff_hunks(None, "a\nb\n")
        assert len(h) == 1
        assert all(l["tag"] == "+" for l in h[0]["lines"])
        assert [l["new_n"] for l in h[0]["lines"]] == [1, 2]
        assert all(l["old_n"] is None for l in h[0]["lines"])

    def test_deletion_only(self):
        h = build_unified_diff_hunks("a\nb\nc\n", "")
        # All lines removed → all `-` with old_n set, new_n None
        assert len(h) == 1
        del_lines = [l for l in h[0]["lines"] if l["tag"] == "-"]
        assert len(del_lines) == 3
        assert all(l["new_n"] is None for l in del_lines)

    def test_single_line_modification_has_context(self):
        before = "l1\nl2\nl3\nl4\nl5\n"
        after  = "l1\nl2\nl3_MOD\nl4\nl5\n"
        h = build_unified_diff_hunks(before, after, context=2)
        assert len(h) == 1
        tags = [l["tag"] for l in h[0]["lines"]]
        # 2 context + 1 del + 1 add + 2 context
        assert tags.count("-") == 1
        assert tags.count("+") == 1
        assert tags.count(" ") == 4

    def test_gutter_numbers_track_through_shifts(self):
        """Insert 1 line at position 3 — subsequent old_n and new_n
        should shift correctly so both columns stay accurate."""
        before = "a\nb\nc\nd\n"
        after  = "a\nb\nINSERTED\nc\nd\n"
        h = build_unified_diff_hunks(before, after, context=1)
        # Locate the `c` context line after the insertion.
        c_line = next(l for l in h[0]["lines"] if l["text"] == "c")
        # `c` was line 3 in old, still 4 in new (shifted by 1 insert).
        assert c_line["old_n"] == 3
        assert c_line["new_n"] == 4

    def test_add_tag_has_no_old_n(self):
        h = build_unified_diff_hunks("a\n", "a\nb\n")
        adds = [l for l in h[0]["lines"] if l["tag"] == "+"]
        assert adds
        assert all(l["old_n"] is None for l in adds)

    def test_del_tag_has_no_new_n(self):
        h = build_unified_diff_hunks("a\nb\n", "a\n")
        dels = [l for l in h[0]["lines"] if l["tag"] == "-"]
        assert dels
        assert all(l["new_n"] is None for l in dels)


class TestWrapEditedFiles:
    def test_empty_input(self):
        env = wrap_edited_files([])
        assert env == {"type": "edited_files", "files": []}

    def test_shape_and_type_tag(self):
        env = wrap_edited_files([{"path": "a.py", "hunks": [{"old_start": 1, "new_start": 1, "lines": []}]}])
        assert env["type"] == "edited_files"
        assert env["files"][0]["path"] == "a.py"
        assert env["files"][0]["hunks"][0]["old_start"] == 1

    def test_drops_files_without_path(self):
        env = wrap_edited_files([{"path": "", "hunks": [{}]}, {"path": "b.py", "hunks": []}])
        assert len(env["files"]) == 1
        assert env["files"][0]["path"] == "b.py"

    def test_path_length_cap(self):
        env = wrap_edited_files([{"path": "x" * 999, "hunks": []}])
        assert len(env["files"][0]["path"]) == 512


class TestWrapCommandExec:
    def test_shape_and_type_tag(self):
        env = wrap_command_exec("pytest tests/", 0)
        assert env["type"] == "command_exec"
        assert env["command"] == "pytest tests/"
        assert env["exit_code"] == 0
        assert isinstance(env["ran_at"], float)

    def test_nonzero_exit_preserved(self):
        env = wrap_command_exec("false", 1)
        assert env["exit_code"] == 1

    def test_command_length_cap(self):
        env = wrap_command_exec("x" * 5000, 0)
        assert len(env["command"]) == 2048

    def test_ran_at_override(self):
        env = wrap_command_exec("echo hi", 0, ran_at=1234.5)
        assert env["ran_at"] == 1234.5
