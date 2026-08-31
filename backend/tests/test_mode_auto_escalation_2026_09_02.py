"""
tests/test_mode_auto_escalation_2026_09_02.py

Item #2 (founder's 2026-09-02 decision): auto-escalate a genuine
code-edit (agentic tier) on Swift to the reliable model, transparently
-- no blanket Pro default, no "please switch to Pro mode" manual ask.
Quick chat/query turns stay on the fast/cheap Swift model.
"""
from __future__ import annotations

from services.mode_routing import resolve_model_mode


def test_t_code_edit_uses_reliable_model():
    """A real code-edit (agentic tier) on Swift escalates to Pro."""
    assert resolve_model_mode("agentic", "swift") == "pro"


def test_quick_chat_stays_on_fast_cheap_swift():
    """Casual/clarify/query tiers on Swift are untouched -- this is
    NOT a blanket Swift->Pro flip, only real edits escalate."""
    assert resolve_model_mode("casual", "swift") == "swift"
    assert resolve_model_mode("clarify", "swift") == "swift"
    assert resolve_model_mode("query", "swift") == "swift"


def test_pro_and_maxx_users_are_never_touched():
    """Users already on Pro/Maxx keep their selected mode regardless
    of tier -- escalation only applies to the Swift fast/cheap tier."""
    assert resolve_model_mode("agentic", "pro") == "pro"
    assert resolve_model_mode("agentic", "maxx") == "maxx"
    assert resolve_model_mode("casual", "pro") == "pro"
    assert resolve_model_mode("casual", "maxx") == "maxx"
