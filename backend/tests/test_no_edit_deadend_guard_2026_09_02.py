"""
tests/test_no_edit_deadend_guard_2026_09_02.py

D1 (connect-flow-refinement follow-up, item #1 from the founder's
2026-09-02 decision): a GENERAL (model-agnostic) guard against the
"raw code-block + fake confirm question, no real aurem-handoff fence"
dead end -- the class of bug behind both the Swift/GLM raw-code
symptom and the Loop-timeout raw-content-fallback symptom (item #4a).
"""
from __future__ import annotations

from services.response_confidence import (
    apply_no_edit_deadend_guard,
    contains_no_edit_deadend,
    NO_EDIT_DEADEND_MESSAGE,
)

GLM_BAD_FENCE_REPLY = (
    "Here's what I'd change:\n\n"
    "```js\n"
    "function renderHours() {\n"
    "  return '<div>Open 9am-5pm</div>';\n"
    "}\n"
    "```\n\n"
    "Would you like me to update this change?"
)

CLAUDE_TIMEOUT_FALLBACK_REPLY = (
    "I read through the homepage template and drafted this:\n\n"
    "```jsx\n"
    "const Hours = () => (\n"
    "  <p className=\"hours\">Mon-Fri 9-5</p>\n"
    ");\n"
    "```\n\n"
    "Do you want me to apply this?"
)


def test_t_no_edit_codeblock_dead_end():
    """Edit-looking code-block + 'apply this?' + no fence -> honest
    message WITH a real path forward, never a silent pass-through
    that would later dead-end on a fake 'yes'."""
    assert contains_no_edit_deadend(GLM_BAD_FENCE_REPLY) is True
    out = apply_no_edit_deadend_guard(GLM_BAD_FENCE_REPLY)
    assert "```js" in out  # the code stays -- still useful via Copy
    assert "Would you like me to update this change?" not in out
    assert NO_EDIT_DEADEND_MESSAGE in out
    assert "Pro mode" in out  # real path forward, not a dead end


def test_t_general_guard_not_swift_specific():
    """The guard must fire on ANY model's bad-fence reply -- proving
    it covers the Loop-timeout-fallback case (item #4a) too, not just
    the Swift/GLM path. Nothing in the guard's logic keys on model
    name or mode; it only inspects the shape of the content itself."""
    assert contains_no_edit_deadend(CLAUDE_TIMEOUT_FALLBACK_REPLY) is True
    out = apply_no_edit_deadend_guard(CLAUDE_TIMEOUT_FALLBACK_REPLY)
    assert "Do you want me to apply this?" not in out
    assert NO_EDIT_DEADEND_MESSAGE in out
    # The functions take no `mode`/`model` argument at all -- proof
    # the check is purely shape-based (code block + confirm question +
    # missing fence), not keyed on which model/mode produced it.
    import inspect
    sig_check = inspect.signature(contains_no_edit_deadend)
    sig_guard = inspect.signature(apply_no_edit_deadend_guard)
    assert list(sig_check.parameters) == ["content"]
    assert list(sig_guard.parameters) == ["content"]


def test_valid_aurem_handoff_fence_is_never_touched():
    """A REAL pending fix (proper fence) must pass through untouched
    -- this guard must never eat the one legitimate Approve path."""
    real = (
        "Found it.\n\n```aurem-handoff\n"
        "file: src/Hours.jsx\n```\n\nWant me to apply this?"
    )
    assert contains_no_edit_deadend(real) is False
    assert apply_no_edit_deadend_guard(real) == real


def test_code_block_without_confirm_question_is_untouched():
    """A code block shown purely as reference/explanation (no
    'apply this?' style question) is not a dead end -- leave it."""
    explainer = (
        "Here's roughly what that function looks like today:\n\n"
        "```js\nfunction renderHours() { return 1; }\n```\n\n"
        "Let me know if you'd like changes."
    )
    assert contains_no_edit_deadend(explainer) is False


def test_confirm_question_without_codeblock_is_untouched():
    """A plain confirm question with no code block at all is a
    normal chat turn, not this bug class."""
    plain = "Should I go ahead and update the homepage copy for you?"
    assert contains_no_edit_deadend(plain) is False
