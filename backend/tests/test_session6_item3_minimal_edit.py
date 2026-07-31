"""
Session 6 · Item 3 regression contract — surgical minimal-edit fast path.

Real-user QA: "add a one-line comment at the very top of README.md"
produced +36/-35 diffs because loop_execute unconditionally asked
the LLM to rewrite the entire file. The landing page promised
"Minimum-diff commits" (Swift mode) — reality delivered scope-creep.

The fix in `services.minimal_edit` provides a surgical-op fast
path that runs BEFORE the full-rewrite call. This test locks:

  1. The trivial-scope classifier (`is_trivial_scope`) correctly
     fires on real-world one-line phrasings (add comment, prepend,
     replace line, etc.) — but NOT on genuine refactor prompts.
  2. Each op-apply produces EXACTLY the expected byte-count change.
  3. Malformed / non-expressible LLM output routes to full-rewrite
     via a `None` return.
  4. loop_execute._generate_one integrates the fast path with a
     transparent fallback.

Zero mocks in the true sense — the classifier / apply functions are
pure, so we exercise them directly. For the LLM roundtrip test we
inject a real coroutine that returns a canned JSON op — same shape
the real LLM would emit — so we assert the pipeline plumbing without
burning OpenRouter budget.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import minimal_edit as ME   # noqa: E402


# ═══ 1) Trivial-scope classifier ═══════════════════════════════════
@pytest.mark.parametrize("prompt,expected", [
    ("add a one-line comment at the top of README.md",           True),
    ("Add a comment at the top",                                  True),
    ("prepend a line",                                            True),
    ("append 'export FOO=1' to .env",                             True),
    ("insert a line after line 5",                                True),
    ("insert one comment at the beginning",                       True),
    ("replace the line that says X",                              True),
    ("delete the second line",                                    True),
    ("fix the typo in line 3",                                    True),
    ("rename the variable foo to bar",                            True),
    # Non-trivial / genuine refactor prompts — must NOT trigger.
    ("refactor the auth flow into JWT",                           False),
    ("add oauth support and new user table",                      False),
    ("build a settings page",                                     False),
    ("",                                                          False),
    ("x" * 600,                                                   False),
])
def test_is_trivial_scope(prompt, expected):
    assert ME.is_trivial_scope(prompt) is expected, (
        f"prompt={prompt!r} expected={expected}"
    )


# ═══ 2) op appliers ════════════════════════════════════════════════
def test_prepend_adds_one_line_only():
    original  = "line 1\nline 2\nline 3\n"
    out = ME._apply_op(original, {"op": "prepend", "text": "// new comment"})
    assert out == "// new comment\nline 1\nline 2\nline 3\n"


def test_prepend_multiline():
    original = "line 1\nline 2\n"
    out = ME._apply_op(original, {"op": "prepend", "text": "a\nb\nc"})
    assert out == "a\nb\nc\nline 1\nline 2\n"


def test_append_adds_one_line_only():
    original = "line 1\nline 2\nline 3\n"
    out = ME._apply_op(original, {"op": "append", "text": "// end note"})
    assert out == "line 1\nline 2\nline 3\n// end note\n"


def test_append_no_trailing_newline_preserved():
    original = "line 1\nline 2"     # NO trailing newline
    out = ME._apply_op(original, {"op": "append", "text": "line 3"})
    # We normalise to trailing \n after append.
    assert out == "line 1\nline 2\nline 3\n"


def test_insert_after_line():
    original = "a\nb\nc\nd\n"
    out = ME._apply_op(original, {"op": "insert_after_line",
                                   "line": 2, "text": "X"})
    assert out == "a\nb\nX\nc\nd\n"


def test_insert_before_line():
    original = "a\nb\nc\n"
    out = ME._apply_op(original, {"op": "insert_before_line",
                                   "line": 2, "text": "Y"})
    assert out == "a\nY\nb\nc\n"


def test_replace_line():
    original = "a\nb\nc\n"
    out = ME._apply_op(original, {"op": "replace_line",
                                   "line": 2, "text": "B"})
    assert out == "a\nB\nc\n"


def test_delete_line():
    original = "a\nb\nc\n"
    out = ME._apply_op(original, {"op": "delete_line", "line": 2})
    assert out == "a\nc\n"


def test_replace_line_out_of_range_returns_none():
    original = "a\nb\nc\n"
    assert ME._apply_op(original, {"op": "replace_line",
                                     "line": 99, "text": "X"}) is None


def test_unknown_op_returns_none():
    assert ME._apply_op("a\n", {"op": "vaporize", "text": "?"}) is None


def test_not_expressible_returns_none():
    assert ME._apply_op("a\n", {"op": "not_expressible"}) is None


# ═══ 3) The e2e byte-count contract (the actual bug) ═══════════════
def test_add_one_line_comment_at_top_produces_one_line_diff():
    """The founder-repro scenario: user asks to add one line at the
    top of README.md. The fix guarantees the resulting content
    differs from the original by EXACTLY one line, byte-for-byte."""
    original = (
        "# AUREM CTO\n"
        "\n"
        "AUREM CTO is an autonomous engineering agent.\n"
        "\n"
        "## Features\n"
        "- Loop mode\n"
        "- Swift mode\n"
        "- Council-based routing\n"
    )
    out = ME._apply_op(original, {
        "op": "prepend",
        "text": "<!-- SESSION 6 · ITEM 3 · REPRO — one-line comment -->",
    })
    # ∆ lines should be exactly +1, and every original line must
    # appear byte-identical in the output.
    orig_lines = original.splitlines()
    out_lines  = out.splitlines()
    assert len(out_lines) - len(orig_lines) == 1, (
        f"Expected +1 line net, got Δ={len(out_lines) - len(orig_lines)}"
    )
    for i, ln in enumerate(orig_lines):
        assert ln == out_lines[i + 1], (
            f"Original line {i} drifted:\n  BEFORE: {ln!r}\n  AFTER: {out_lines[i+1]!r}"
        )
    assert out_lines[0].startswith("<!-- SESSION 6"), out_lines[0]


# ═══ 4) End-to-end plumbing via a canned LLM ═══════════════════════
@pytest.mark.asyncio
async def test_try_minimal_edit_uses_real_pipeline_with_canned_llm(monkeypatch):
    """Exercise the full `try_minimal_edit` path with a canned LLM
    call so we assert (a) the trivial-scope gate fires, (b) the LLM
    is invoked with the surgical system prompt, (c) the returned JSON
    op is applied cleanly. No `unittest.mock` — we inject a real
    async function."""

    async def canned_llm(**kwargs):
        # Contract check: system prompt must contain the schema hint.
        assert "prepend" in kwargs["system"], kwargs["system"][:200]
        assert kwargs["mode"] == "code"
        return {
            "content":
                '{"op": "prepend", "text": '
                '"<!-- session6 item3 minimal-edit proof -->"}',
        }

    result = await ME.try_minimal_edit(
        user_message="Add a one-line comment at the top of README.md",
        plan={"title": "test-plan", "bullets": ["Add a one-line comment"]},
        path="README.md",
        current="# AUREM CTO\n\nAn autonomous engineering agent.\n",
        user_id="unit-test",
        call_llm_with_meta=canned_llm,
    )
    assert result is not None, "surgical path should return a result"
    assert result["op"]["op"] == "prepend"
    assert result["content"].startswith(
        "<!-- session6 item3 minimal-edit proof -->\n")
    assert result["content"].endswith(
        "# AUREM CTO\n\nAn autonomous engineering agent.\n")


@pytest.mark.asyncio
async def test_try_minimal_edit_returns_none_for_non_trivial_prompt():
    async def canned_llm(**kwargs):
        raise AssertionError(
            "LLM should NOT be called for a non-trivial prompt")
    result = await ME.try_minimal_edit(
        user_message="Refactor the entire auth module to use JWT",
        plan={"title": "refactor", "bullets": []},
        path="auth.py",
        current="def old(): pass\n",
        user_id=None,
        call_llm_with_meta=canned_llm,
    )
    assert result is None


@pytest.mark.asyncio
async def test_try_minimal_edit_returns_none_on_not_expressible():
    async def canned_llm(**kwargs):
        return {"content": '{"op": "not_expressible"}'}
    result = await ME.try_minimal_edit(
        user_message="Add a comment somewhere",
        plan={"title": "", "bullets": []},
        path="x.py",
        current="print('hi')\n",
        user_id=None,
        call_llm_with_meta=canned_llm,
    )
    assert result is None


@pytest.mark.asyncio
async def test_try_minimal_edit_returns_none_on_malformed_json():
    async def canned_llm(**kwargs):
        return {"content": "not json at all"}
    result = await ME.try_minimal_edit(
        user_message="prepend a header",
        plan={"title": "", "bullets": []},
        path="x.py",
        current="print('hi')\n",
        user_id=None,
        call_llm_with_meta=canned_llm,
    )
    assert result is None
