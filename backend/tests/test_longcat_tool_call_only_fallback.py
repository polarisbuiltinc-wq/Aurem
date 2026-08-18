"""test_longcat_tool_call_only_fallback.py — 2026-02-18

Regression for the "LongCat returns pure `<longcat_tool_call>…</…>` XML
with no prose" prod bug. Frontend sanitizer strips the XML, bubble
renders empty → user sees the placeholder. Fix (backend side):
detect that shape in `_call_longcat` and fall through to GLM-5.2
in-flight, exactly like the pre-existing empty-content branch.

These tests cover the _strip_tool_call_xml_len helper — a full
integration test would need the OpenRouter HTTP layer mocked, which is
already exercised by other tests in this dir. The critical contract is
"len 0 after strip iff frontend would render empty", so unit-testing
the helper is sufficient regression coverage.
"""
from __future__ import annotations

import pytest

from services.llm.openrouter_providers import _strip_tool_call_xml_len


def test_empty_input_returns_zero():
    assert _strip_tool_call_xml_len("") == 0
    assert _strip_tool_call_xml_len(None) == 0


def test_pure_longcat_tool_call_collapses_to_zero():
    """The exact shape LongCat-2.0 emits on prod. Must return 0 so the
    router flips to GLM and the user never sees the ghost bubble."""
    raw = '<longcat_tool_call>read_repo_file {"path":"backend/main.py"}</longcat_tool_call>'
    assert _strip_tool_call_xml_len(raw) == 0


def test_vendor_prefixed_variants_all_collapse():
    for prefix in ("claude", "qwen", "gpt", "deepseek", "custommodel"):
        raw = f'<{prefix}_tool_call>x</{prefix}_tool_call>'
        assert _strip_tool_call_xml_len(raw) == 0, prefix


def test_multiple_tool_calls_collapse():
    raw = (
        '<longcat_tool_call>read_repo_file {"path":"a"}</longcat_tool_call>'
        '\n\n'
        '<longcat_tool_call>read_repo_file {"path":"b"}</longcat_tool_call>'
    )
    assert _strip_tool_call_xml_len(raw) == 0


def test_orphan_open_tag_collapses():
    """Streaming cutoff mid-XML → no closing tag. Must still collapse."""
    raw = '<longcat_tool_call>read_repo_file {"path":"backend/main.py'
    assert _strip_tool_call_xml_len(raw) == 0


def test_prose_plus_tool_call_does_NOT_collapse():
    """A legitimate mixed reply must keep its prose length. Otherwise
    we'd wrongly bounce to GLM for every reply that also happened to
    include a tool call — most Prompt-mode replies would."""
    raw = 'Here is my read.\n<longcat_tool_call>x</longcat_tool_call>\nAll good.'
    n = _strip_tool_call_xml_len(raw)
    assert n > 0, "prose should survive tool-call strip"
    # ~30 chars for "Here is my read." + "\n\n" + "All good." after collapse
    assert 15 <= n <= 60, f"unexpected residual length: {n}"


def test_whitespace_only_after_strip_is_zero():
    raw = '   \n\t\n  <longcat_tool_call>x</longcat_tool_call>  \n\n  '
    assert _strip_tool_call_xml_len(raw) == 0


def test_thinking_and_scratchpad_also_stripped():
    """Non-tool internal fences the frontend also hides — helper must
    match so we don't falsely keep LongCat live when the reply was
    just <thinking>…</thinking>."""
    for tag in ("thinking", "scratchpad", "system_prompt"):
        raw = f'<{tag}>internal only</{tag}>'
        assert _strip_tool_call_xml_len(raw) == 0, tag


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
