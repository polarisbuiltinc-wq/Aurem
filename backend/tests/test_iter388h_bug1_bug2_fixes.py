"""
Iter 388h regression tests

Bug 1 — ORA Diff View silent-failure on real git-path Loop runs.
  `_run_task_with_git` was missing the `edited_files` / rich
  `files_changed` persistence + `task_handoff` + `done` SSE emit that
  `_run_task_via_api` already had. Real user PAT-connected runs use the
  git path, so the frontend's `LiveTaskPopup.onDone` never received an
  `edited_files` payload and the inline `EditedFileBubble` never
  rendered.

Bug 2 — Raw `<longcat_tool_call>` XML leaking into chat UI in Prompt
  mode. The frontend `RenderedMessage.sanitizeForDisplay` regex only
  matched the unprefixed `<tool_call>` shape; vendor-prefixed variants
  (longcat_/claude_/qwen_/gpt_) passed through untouched.

These tests only verify shapes / regex behaviour (no network I/O), so
they run in isolation without a live DB or GitHub PAT.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Allow "backend/" imports when pytest is invoked from /app.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ora_chat.tool_output_wrapper import wrap_edited_files  # noqa: E402
from services.task_diff import build_unified_diff_hunks  # noqa: E402


# ---------------------------------------------------------------------------
# Bug 1 — payload shape parity between API and git task workers.
# ---------------------------------------------------------------------------

def _mock_completed_edits():
    before = {"src/util.py": "def add(a, b):\n    return a - b\n"}
    after  = {"src/util.py": "def add(a, b):\n    return a + b\n"}
    return before, after


def test_git_path_edited_files_shape_matches_frontend_contract():
    """The frontend expects `task.edited_files.files[]` with each entry
    carrying a `path` and non-empty `hunks[]`. Confirm the wrapper +
    hunk builder together produce exactly that shape."""
    before, after = _mock_completed_edits()
    hunk_files = [
        {
            "path":  path,
            "hunks": build_unified_diff_hunks(before[path], after[path], context=2),
        }
        for path in after
    ]
    payload = wrap_edited_files(hunk_files)

    assert payload["type"] == "edited_files"
    assert isinstance(payload["files"], list) and len(payload["files"]) == 1

    entry = payload["files"][0]
    assert entry["path"] == "src/util.py"
    assert isinstance(entry["hunks"], list) and len(entry["hunks"]) > 0

    # Each hunk must carry the dual-gutter old_n / new_n metadata the
    # `EditedFileBubble` monaco renderer keys off of.
    for h in entry["hunks"]:
        assert "lines" in h and isinstance(h["lines"], list)
        assert any("old_n" in ln or "new_n" in ln for ln in h["lines"])


def test_git_path_empty_edits_yields_no_files():
    """Guard: `build_unified_diff_hunks` on identical content should
    produce no hunks so the git-path payload doesn't accidentally
    render a phantom bubble on a no-op commit."""
    same = "print('hi')\n"
    payload = wrap_edited_files([
        {"path": "a.py", "hunks": build_unified_diff_hunks(same, same, context=2)},
    ])
    # Wrapper still emits the file entry, but its hunks list must be empty.
    assert payload["files"][0]["hunks"] == []


# ---------------------------------------------------------------------------
# Bug 2 — sanitize `<longcat_tool_call>` and other vendor-prefixed tags
# by re-implementing the same regex the frontend uses. If the shape
# below drifts, this test fails and forces a paired update.
# ---------------------------------------------------------------------------

_INTERNAL_TAG_RE = (
    r"(?:tool_call|tool_calls|tool_use|tool_result|tool_results|tool_response|"
    r"function_call|function_result|function_response|thinking|chain_of_thought|"
    r"scratchpad|internal|system|system_prompt|orchestrator|"
    r"[a-z0-9]+_tool_call|[a-z0-9]+_tool_calls|[a-z0-9]+_tool_use|"
    r"[a-z0-9]+_tool_result|[a-z0-9]+_tool_results|[a-z0-9]+_tool_response|"
    r"[a-z0-9]+_function_call|[a-z0-9]+_function_result|"
    r"[a-z0-9]+_function_response|[a-z0-9]+_thinking|[a-z0-9]+_chain_of_thought)"
)

_PAIRED = re.compile(
    rf"<\s*({_INTERNAL_TAG_RE})\b[^>]*>[\s\S]*?<\s*/\s*\1\s*>", re.IGNORECASE,
)
_ORPHAN = re.compile(
    rf"<\s*({_INTERNAL_TAG_RE})\b[^>]*>[\s\S]*$", re.IGNORECASE,
)


def _sanitize(text: str) -> str:
    text = _PAIRED.sub("", text)
    text = _ORPHAN.sub("", text)
    return text.strip()


def test_longcat_paired_tool_call_stripped():
    """The exact leak the user reported in prod must vanish."""
    raw = (
        "Here is my read:\n"
        '<longcat_tool_call>read_repo_file {"path":"src/App.jsx"}</longcat_tool_call>\n'
        "Result: ok."
    )
    out = _sanitize(raw)
    assert "longcat_tool_call" not in out
    assert "read_repo_file" not in out
    assert "Here is my read:" in out
    assert "Result: ok." in out


def test_longcat_orphan_open_stripped_when_stream_truncated():
    """Streaming can cut mid-tag; sanitizer must still hide the leak."""
    raw = (
        "Sure, let me look:\n"
        '<longcat_tool_call>read_repo_file {"path":"backend/server'
    )
    out = _sanitize(raw)
    assert "<longcat_tool_call>" not in out
    assert out.startswith("Sure, let me look:")


def test_other_vendor_prefixes_also_stripped():
    """Regression net: sanitizer covers claude_/qwen_/gpt_ variants too."""
    for prefix in ("claude", "qwen", "gpt"):
        raw = f"<{prefix}_tool_result>{{\"ok\":true}}</{prefix}_tool_result>after"
        out = _sanitize(raw)
        assert prefix not in out, f"{prefix}_tool_result leaked through"
        assert out == "after"


def test_plain_prose_untouched():
    """Do not eat legitimate content that merely mentions the words."""
    raw = "The tool_call spec is documented here. See <b>bold</b>."
    out = _sanitize(raw)
    # No XML tags → sanitizer is a no-op except for trim.
    assert "tool_call spec is documented here" in out
    assert "<b>bold</b>" in out


def test_unprefixed_tool_call_still_stripped():
    """The legacy shape must keep working (no regression from widening)."""
    raw = 'A<tool_call>{"name":"x"}</tool_call>B'
    assert _sanitize(raw) == "AB"
